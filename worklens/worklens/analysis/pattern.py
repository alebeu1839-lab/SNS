"""繰り返し作業の検出。

手順:
  1. セグメント間の「間（ま）」の分布から作業の切れ目のしきい値を推定する
     （Otsu法による二値分割。人によって作業リズムが違うためデータから決める）。
  2. しきい値でセグメント列を「作業ブロック」へ分割する。
     1ブロック ≒ 業務の1回分。
  3. ブロックを手順シグネチャでクラスタリングし、同じ業務の反復を束ねる。
  4. 出現の少ないブロックについては、ブロック内の部分列を可変長で
     マイニングし、埋もれた反復（他業務が割り込んだ回など）を拾う。

これにより「どの業務が繰り返し作業なのか」が、見た目ではなく
系列構造と頻度から決まる。
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Sequence

from .sessionizer import Segment

MAX_PATTERN_LEN = 8
MIN_BLOCK_GAP_SEC = 10
MAX_BLOCK_GAP_SEC = 300


# --------------------------------------------------------------- しきい値
def estimate_block_gap(gaps: Sequence[int], default: int = 90) -> int:
    """作業の切れ目とみなす秒数を、間の分布から推定する（Otsu法）。"""
    values = [math.log1p(max(0, g)) for g in gaps]
    if len(values) < 20:
        return default
    lo, hi = min(values), max(values)
    if hi - lo < 1e-6:
        return default
    best_t, best_var = None, -1.0
    steps = 64
    for i in range(1, steps):
        t = lo + (hi - lo) * i / steps
        left = [v for v in values if v <= t]
        right = [v for v in values if v > t]
        if not left or not right:
            continue
        wl, wr = len(left) / len(values), len(right) / len(values)
        ml, mr = sum(left) / len(left), sum(right) / len(right)
        between = wl * wr * (ml - mr) ** 2
        if between > best_var:
            best_var, best_t = between, t
    if best_t is None:
        return default
    threshold = int(math.expm1(best_t))
    return max(MIN_BLOCK_GAP_SEC, min(MAX_BLOCK_GAP_SEC, threshold))


# ------------------------------------------------------------------ 構造体
@dataclass
class Occurrence:
    """業務の1回分の実行。"""

    segments: list[Segment]

    @property
    def duration_sec(self) -> int:
        return sum(s.duration_sec for s in self.segments)

    @property
    def started_at(self):
        return self.segments[0].started_at

    @property
    def ended_at(self):
        return self.segments[-1].ended_at

    @property
    def signature(self) -> tuple[str, ...]:
        return tuple(s.token for s in self.segments)


@dataclass
class Cluster:
    """同じ手順で繰り返されている作業のまとまり。"""

    signature: tuple[str, ...]
    occurrences: list[Occurrence] = field(default_factory=list)
    is_repetitive: bool = False

    @property
    def count(self) -> int:
        return len(self.occurrences)

    @property
    def total_duration_sec(self) -> int:
        return sum(o.duration_sec for o in self.occurrences)

    @property
    def avg_duration_sec(self) -> int:
        return int(self.total_duration_sec / self.count) if self.count else 0

    @property
    def duration_cv(self) -> float:
        """所要時間の変動係数。小さいほど定型的。"""
        if self.count < 2:
            return 1.0
        d = [o.duration_sec for o in self.occurrences]
        mean = sum(d) / len(d)
        if mean <= 0:
            return 1.0
        var = sum((x - mean) ** 2 for x in d) / len(d)
        return (var**0.5) / mean

    @property
    def repetition_score(self) -> float:
        """0..1。回数が多く、所要時間が安定し、手順が定まっているほど高い。"""
        if self.count < 2:
            return 0.0
        volume = min(1.0, self.count / 40.0)
        stability = max(0.0, 1.0 - min(self.duration_cv, 1.0))
        structure = min(1.0, len(self.signature) / 4.0)
        return round(0.45 * volume + 0.35 * stability + 0.20 * structure, 3)

    @property
    def segments(self) -> list[Segment]:
        return [s for o in self.occurrences for s in o.segments]


# ---------------------------------------------------------------- 前処理
def _collapse(segments: Sequence[Segment], merge_gap_sec: int = 5) -> list[Segment]:
    """連続する同一トークンを1つにまとめる。"""
    out: list[Segment] = []
    for seg in segments:
        if out and out[-1].token == seg.token and (
            seg.started_at - out[-1].ended_at
        ).total_seconds() <= merge_gap_sec:
            m = out[-1]
            m.ended_at = max(m.ended_at, seg.ended_at)
            m.keystrokes += seg.keystrokes
            m.input_active_sec += seg.input_active_sec
            m.clicks += seg.clicks
            m.copies += seg.copies
            m.pastes += seg.pastes
            m.file_ops.extend(seg.file_ops)
            m.file_exts.extend(seg.file_exts)
            m.event_ids.extend(seg.event_ids)
            for t in seg.titles:
                if t not in m.titles:
                    m.titles.append(t)
            continue
        out.append(seg)
    return out


def split_blocks(stream: Sequence[Segment], gap_sec: int) -> list[list[Segment]]:
    """間がしきい値以上のところで作業ブロックへ分割する。"""
    blocks: list[list[Segment]] = []
    current: list[Segment] = []
    for seg in stream:
        if current:
            gap = (seg.started_at - current[-1].ended_at).total_seconds()
            if gap >= gap_sec or seg.idle_before_sec >= gap_sec:
                blocks.append(current)
                current = []
        current.append(seg)
    if current:
        blocks.append(current)
    return blocks


# ------------------------------------------------------------------ 本体
def mine(
    segments_by_session: dict[str, list[Segment]],
    min_support: int = 3,
    max_len: int = MAX_PATTERN_LEN,
    block_gap_sec: int | None = None,
) -> tuple[list[Cluster], dict]:
    """繰り返しクラスタを抽出する。戻り値は (クラスタ列, 診断情報)。"""
    streams = [_collapse(s) for s in segments_by_session.values() if s]
    if not streams:
        return [], {"block_gap_sec": block_gap_sec or 0, "blocks": 0}

    gaps: list[int] = []
    for stream in streams:
        for a, b in zip(stream, stream[1:]):
            gaps.append(int((b.started_at - a.ended_at).total_seconds()))
    gap_sec = block_gap_sec or estimate_block_gap(gaps)

    blocks: list[list[Segment]] = []
    for stream in streams:
        blocks.extend(split_blocks(stream, gap_sec))

    # --- 1) ブロックのシグネチャでクラスタリング ----------------------
    by_signature: dict[tuple[str, ...], list[list[Segment]]] = defaultdict(list)
    for block in blocks:
        by_signature[tuple(s.token for s in block)].append(block)

    clusters: list[Cluster] = []
    rare_blocks: list[list[Segment]] = []
    for signature, group in by_signature.items():
        if len(group) >= min_support:
            clusters.append(
                Cluster(
                    signature=signature,
                    occurrences=[Occurrence(b) for b in sorted(group, key=lambda b: b[0].started_at)],
                    is_repetitive=True,
                )
            )
        else:
            rare_blocks.extend(group)

    # --- 2) 少数派ブロックの中から共通の部分手順を掘り出す -------------
    clusters.extend(_mine_subsequences(rare_blocks, min_support, max_len))

    # --- 3) 同一業務の重複クラスタを統合する --------------------------
    clusters = normalize_clusters(clusters, min_support)

    clusters.sort(key=lambda c: c.total_duration_sec, reverse=True)
    stats = {
        "block_gap_sec": gap_sec,
        "blocks": len(blocks),
        "distinct_signatures": len(by_signature),
        "segments": sum(len(s) for s in streams),
    }
    return clusters, stats


def _mine_subsequences(
    blocks: Sequence[Sequence[Segment]], min_support: int, max_len: int
) -> list[Cluster]:
    """ブロック内の連続部分列から、支持度の高いものを貪欲に確定する。"""
    if not blocks:
        return []
    tokens = [[s.token for s in b] for b in blocks]
    claimed = [[False] * len(t) for t in tokens]

    ngrams: dict[tuple[str, ...], list[tuple[int, int]]] = defaultdict(list)
    for bi, stream in enumerate(tokens):
        for n in range(2, min(max_len, len(stream)) + 1):
            for i in range(len(stream) - n + 1):
                ngrams[tuple(stream[i : i + n])].append((bi, i))

    # 支持度を優先し、同支持度なら長いものを優先する
    candidates = sorted(
        (g for g, pos in ngrams.items() if len(pos) >= min_support),
        key=lambda g: (len(ngrams[g]), len(g)),
        reverse=True,
    )

    out: list[Cluster] = []
    for gram in candidates:
        n = len(gram)
        taken: list[tuple[int, int]] = []
        for bi, i in ngrams[gram]:
            if any(claimed[bi][i + k] for k in range(n)):
                continue
            taken.append((bi, i))
            for k in range(n):
                claimed[bi][i + k] = True
        if len(taken) < min_support:
            for bi, i in taken:
                for k in range(n):
                    claimed[bi][i + k] = False
            continue
        out.append(
            Cluster(
                signature=gram,
                occurrences=[
                    Occurrence(list(blocks[bi][i : i + n]))
                    for bi, i in sorted(taken, key=lambda p: blocks[p[0]][p[1]].started_at)
                ],
                is_repetitive=True,
            )
        )

    leftovers: dict[str, list[Occurrence]] = defaultdict(list)
    for bi, block in enumerate(blocks):
        run: list[Segment] = []
        for i, seg in enumerate(block):
            if claimed[bi][i]:
                if run:
                    leftovers[run[0].token].append(Occurrence(run))
                    run = []
                continue
            run.append(seg)
        if run:
            leftovers[run[0].token].append(Occurrence(run))
    for token, occs in leftovers.items():
        out.append(
            Cluster(
                signature=(token,),
                occurrences=sorted(occs, key=lambda o: o.started_at),
                is_repetitive=len(occs) >= min_support,
            )
        )
    return out


# -------------------------------------------------------------- 後処理
def _base_period(signature: tuple[str, ...]) -> tuple[str, ...]:
    """シグネチャが同じ手順の k 回繰り返しなら、その 1 周期を返す。"""
    n = len(signature)
    for size in range(1, n // 2 + 1):
        if n % size:
            continue
        base = signature[:size]
        if all(signature[i : i + size] == base for i in range(0, n, size)):
            return base
    return signature


def normalize_clusters(clusters: list[Cluster], min_support: int) -> list[Cluster]:
    """周期的なシグネチャを1周期へ分解し、同一シグネチャのクラスタを統合する。

    「A→B→A→B」は「A→B を2回」であって別の業務ではない。ここを畳まないと
    同じ業務が別々の候補として二重に数えられてしまう。
    """
    merged: dict[tuple[str, ...], Cluster] = {}
    for cluster in clusters:
        base = _base_period(cluster.signature)
        size = len(base)
        target = merged.setdefault(base, Cluster(signature=base))
        for occ in cluster.occurrences:
            if size == len(cluster.signature):
                target.occurrences.append(occ)
                continue
            for i in range(0, len(occ.segments), size):
                chunk = occ.segments[i : i + size]
                if len(chunk) == size:
                    target.occurrences.append(Occurrence(chunk))
    out: list[Cluster] = []
    for cluster in merged.values():
        cluster.occurrences.sort(key=lambda o: o.started_at)
        cluster.is_repetitive = cluster.count >= min_support
        out.append(cluster)
    return out
