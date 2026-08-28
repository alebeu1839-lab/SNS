"""モック在庫データ。

STEP2 の練習用。実務でよくある「そのままでは自動化できない例外」を
意図的に混ぜてある（価格が数値でない / 必須項目が空 / 要確認メモ付き）。
自動化はこれらを人へ引き継げなければならない。
"""
from __future__ import annotations

CARS: list[dict] = [
    {"id": 48201, "maker": "トヨタ", "model": "アクア", "year": 2019, "mileage_km": 42000,
     "price_yen": 1280000, "color": "ホワイトパール", "inspection": "2026-08",
     "equipment": "ナビ/ETC/バックカメラ", "note": "", "listed": False},
    {"id": 48202, "maker": "ホンダ", "model": "フィット", "year": 2020, "mileage_km": 31500,
     "price_yen": 1390000, "color": "クリスタルブラック", "inspection": "2027-02",
     "equipment": "ナビ/ETC", "note": "", "listed": False},
    {"id": 48203, "maker": "日産", "model": "ノート", "year": 2018, "mileage_km": 58200,
     "price_yen": 980000, "color": "ガンメタリック", "inspection": "2026-05",
     "equipment": "ETC", "note": "", "listed": False},
    # --- 例外1: 価格が数値でない（商談中・応談） ---
    {"id": 48204, "maker": "レクサス", "model": "RX", "year": 2021, "mileage_km": 18000,
     "price_yen": None, "price_text": "応談", "color": "ソニッククォーツ",
     "inspection": "2027-06", "equipment": "本革/サンルーフ/ナビ", "note": "", "listed": False},
    {"id": 48205, "maker": "スズキ", "model": "ハスラー", "year": 2022, "mileage_km": 12400,
     "price_yen": 1450000, "color": "デニムブルー", "inspection": "2027-11",
     "equipment": "ナビ/全方位カメラ", "note": "", "listed": False},
    # --- 例外2: 走行距離が未入力 ---
    {"id": 48206, "maker": "マツダ", "model": "CX-5", "year": 2019, "mileage_km": None,
     "price_yen": 1880000, "color": "ソウルレッド", "inspection": "2026-09",
     "equipment": "ナビ/ETC/レーダークルーズ", "note": "", "listed": False},
    {"id": 48207, "maker": "ダイハツ", "model": "タント", "year": 2021, "mileage_km": 22800,
     "price_yen": 1180000, "color": "シャイニングホワイト", "inspection": "2027-04",
     "equipment": "両側電動スライド/ナビ", "note": "", "listed": False},
    # --- 例外3: 要確認メモ付き（人の判断が要る） ---
    {"id": 48208, "maker": "スバル", "model": "フォレスター", "year": 2017, "mileage_km": 76500,
     "price_yen": 1290000, "color": "クリスタルブラック", "inspection": "2026-03",
     "equipment": "ナビ/ETC", "note": "※要確認 修復歴の有無を確認中", "listed": False},
    {"id": 48209, "maker": "トヨタ", "model": "ヤリスクロス", "year": 2022, "mileage_km": 15600,
     "price_yen": 2080000, "color": "アーバンカーキ", "inspection": "2027-09",
     "equipment": "ナビ/ETC/バックカメラ/LED", "note": "", "listed": False},
    {"id": 48210, "maker": "ホンダ", "model": "N-BOX", "year": 2020, "mileage_km": 34100,
     "price_yen": 1240000, "color": "プレミアムホワイト", "inspection": "2026-12",
     "equipment": "両側電動スライド/ナビ/ETC", "note": "", "listed": False},
    {"id": 48211, "maker": "日産", "model": "セレナ", "year": 2019, "mileage_km": 61300,
     "price_yen": 1720000, "color": "ブリリアントシルバー", "inspection": "2026-07",
     "equipment": "両側電動スライド/ナビ/プロパイロット", "note": "", "listed": False},
    {"id": 48212, "maker": "トヨタ", "model": "ハリアー", "year": 2021, "mileage_km": 28900,
     "price_yen": 3180000, "color": "プレシャスブラック", "inspection": "2027-08",
     "equipment": "本革/ナビ/ETC/パノラマルーフ", "note": "", "listed": False},
]


def reset() -> None:
    for car in CARS:
        car["listed"] = False
