# 自分のPCで試す

デモは架空の会社のデータです。**自分の本当の業務**を分析するには、
自分のPCでエージェントを動かします。

---

## 1. 準備（1コマンド）

```bash
python scripts/setup.py
```

必要なライブラリを入れ、**このPCで何が取得できるか**を表示します。

```
[OK  ] 使用アプリ・ウィンドウタイトル
[OK  ] 離席の検出
[OK  ] 入力量の検出
[OK  ] コピーの検出
```

あとから確認したいときは `python -m worklens.agent.cli doctor` です。

---

## 2. まず10分だけ試す

```bash
python -m worklens.agent.cli init --company "会社名" --name "氏名" --email you@example.com
python -m worklens.agent.cli scopes                          # 何を取るか確認
python -m worklens.agent.cli collect --source live --minutes 10
```

**普段どおりPCを使ってください。** 10分では業務は見つかりませんが、
自分の操作がどう記録されるかを確認できます。

```bash
python -m worklens.agent.cli analyze --period-days 1
python -m uvicorn worklens.api.app:app --port 8000
```

→ http://localhost:8000 の「収集データの確認」で、実際に保存された中身を見てください。
**ここで違和感があれば、その項目をOFFにしてから本番を始めます。**

---

## 3. 2週間ぶん貯める

```bash
python -m worklens.agent.cli collect --source live --minutes 480
```

毎朝これを実行して、終業時に Ctrl+C で止めます。中断しても途中まで保存されます。

- **1週間では足りません。** 月次業務（請求・締め）が入らず、候補が偏ります
- 途中でやめたくなったら `python -m worklens.agent.cli consent all-off`
- データを消したくなったら `python -m worklens.agent.cli purge --scope user`

---

## 4. 分析する

```bash
python -m worklens.agent.cli analyze --period-days 30
```

Claude を使うと業務名の精度が上がります（未設定でもルールベースで動きます）。

```bash
export ANTHROPIC_API_KEY=sk-ant-...      # Windows: set ANTHROPIC_API_KEY=...
```

---

## 取得できるもの・できないもの（実PC）

「何を収集しているか」の全項目は `scopes` と `/privacy` 画面が正です。
ここでは**実PCでの制約**だけを書きます。

| | 取得方法 | 備考 |
|---|---|---|
| 使用アプリ・使用時間 | OSのアクティブウィンドウ | |
| ウィンドウタイトル | 同上 | 機密パターンは保存前に破棄 |
| 入力の有無 | 「最後の入力からの経過秒」だけを見る | **押されたキーは取得しません** |
| コピーの発生 | OSのクリップボード**変更カウンタ** | **中身は一度も読みません** |
| 離席 | 同上（経過秒がしきい値超え） | 離席中は記録を止めます |
| 貼り付けの発生 | ✗ 取得しません | 中身を読まずに判別できないため |
| ファイル操作 | ✗ 現時点では未対応 | 書類作成はアプリとタイトルから推定 |
| ブラウザのURL | △ ウィンドウタイトル経由 | 拡張機能を入れれば精度が上がります |

**貼り付けを取らないことの影響**：転記業務は「コピー → 別システムで入力」の形で
検出します。貼り付け操作そのものは見ません。

**コピー検出が使えないPC**（Linuxなど）では転記業務が見つかりにくくなります。
`doctor` が警告します。

---

## 精度が出ないときに見るところ

分析結果が「〇〇を確認」ばかりになる場合、次のどれかです。

1. **`doctor` で「入力量の検出」が不可** → 入力作業と閲覧が区別できていません
2. **`doctor` で「コピーの検出」が不可** → 転記業務が検出できません
3. **収集期間が短い** → 3回以上繰り返された操作しか業務として拾いません
4. **業務システムの名前が辞書に無い** → `worklens/appcatalog.py` の `DOMAIN_ROLES` に
   自社システムのドメインを足すと、「Chrome」ではなく「在庫管理システム」と表示されます

---

## 会社で複数人に配るとき

現状は**1台のPCで完結する構成**です（データは各PCの `~/.worklens/worklens.db`）。
複数人ぶんを集約するには、エージェント→サーバの送信APIが別途必要です。

まずは**自分1人で2週間**動かし、出てくる候補が実感と合うかを確かめてから
配布を検討してください。
