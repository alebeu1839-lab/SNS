# Instagram を繋ぐ：完全ガイド

Instagramへ自動投稿するための認証情報を取る手順です。SNSの中で一番つまずきやすいので、
**画面ごと**に説明します。最終的に欲しいのはこの2つだけ:

```
META_ACCESS_TOKEN=......
IG_BUSINESS_ACCOUNT_ID=......
```

これを `.env` に書けば接続完了です。

---

## STEP 0. 事前条件（アカウント側）※API以前の必須準備

Instagramの「個人アカウント」では投稿APIが使えません。次の2つが必要です。

1. **Instagramをプロアカウント化**
   - インスタアプリ → 設定 → アカウントの種類とツール → **プロアカウントに切り替える**
   - 種別は「ビジネス」または「クリエイター」（無料）
2. **Facebookページと連携**
   - Facebookページを1つ作成（無料）
   - インスタアプリ → 設定 → **ビジネス/Facebookページとリンク**

> 💡 ここはMetaの仕様で避けられません。「インスタ単体」では繋げず、Facebookページが土台になります。

---

## STEP 1. Meta開発者アプリを作る

1. https://developers.facebook.com にFacebookアカウントでログイン
2. 右上 **マイアプリ → アプリを作成**
3. ユースケースは **「その他」→ タイプ「ビジネス」** を選択
4. アプリ名を入力して作成

---

## STEP 2. アクセストークンを取る（最速ルート：Graph API Explorer）

1. https://developers.facebook.com/tools/explorer を開く
2. 右上「Meta App」で**STEP1のアプリ**を選択
3. **「Add a Permission」** で次の権限にチェック:
   - `instagram_basic`
   - `instagram_content_publish`
   - `pages_show_list`
   - `pages_read_engagement`
   - `business_management`
4. **「Generate Access Token」** をクリック → Facebookの同意画面でインスタ連携先を許可
5. 出てきた長い文字列が **アクセストークン**（これが `META_ACCESS_TOKEN` の元）

> ⚠️ Explorerのトークンは**1〜2時間で失効**します。STEP4で「長期トークン（60日）」に変換します。

---

## STEP 3. InstagramビジネスアカウントID を取る

Graph API Explorer の上部のリクエスト欄で、順に叩きます（GETのまま）。

1. 自分のFacebookページIDを確認:
   ```
   me/accounts
   ```
   → 返ってきた `data[].id` があなたのページID（複数あれば対象ページのもの）

2. そのページに紐づくインスタのIDを確認（`<PAGE_ID>` を置換）:
   ```
   <PAGE_ID>?fields=instagram_business_account
   ```
   → `instagram_business_account.id` が **`IG_BUSINESS_ACCOUNT_ID`** です。

---

## STEP 4. 長期トークン（60日）に変換する

短命トークンのままだとすぐ切れるので、長期トークンへ。ターミナルで（値を置換）:

```bash
curl -s "https://graph.facebook.com/v19.0/oauth/access_token\
?grant_type=fb_exchange_token\
&client_id=<APP_ID>\
&client_secret=<APP_SECRET>\
&fb_exchange_token=<STEP2で取った短命トークン>"
```

- `<APP_ID>` `<APP_SECRET>` … アプリ設定 → **基本設定** にある
- 返ってきた `access_token` が **60日有効**。これを `META_ACCESS_TOKEN` に使う。

> 🔁 60日ごとに再取得が必要です。後で自動更新フローも作れます（必要なら言ってください）。

---

## STEP 5. このプロジェクトに設定する

`.env` に記入（`<...>` を置換）:
```
META_ACCESS_TOKEN=<STEP4の60日トークン>
IG_BUSINESS_ACCOUNT_ID=<STEP3のID>
```

`config/channels.yaml` の `instagram-main` は既に `enabled: true` 済みです。
`handle` を自分のアカウント名に変えておきましょう。

---

## STEP 6. 接続確認

```bash
node --env-file=.env scripts/check-connections.mjs
```

```
instagram-main (instagram) your_brand  ● READY
   ✓ META_ACCESS_TOKEN
   ✓ IG_BUSINESS_ACCOUNT_ID
結果: 1/1 の有効チャンネルが運用可能
```

これが出れば**接続完了**です 🎉

---

## ⚠️ Instagram投稿の前提：画像が必須

Instagramはテキストだけの投稿ができません。**公開URLでアクセスできる画像**が必要です
（`publish-meta.json` が `image_url` をMetaに渡すため）。

つまり実運用には「画像をどこかに置いてURLを得る」仕組みが要ります。選択肢:
- 自分で用意した画像をクラウド（S3/Cloudinary等）に置く
- designerエージェントの画像生成を実アセットに接続する（別途実装）

> この画像供給の部分は未実装です。繋ぎ込みたくなったら実装します（`ASSET_STORAGE_BUCKET` を用意済み）。

---

## よくある詰まり

| 症状 | 原因 / 対処 |
|---|---|
| `instagram_business_account` が null | インスタがプロアカウントでない／Facebookページ未連携（STEP0）|
| トークンがすぐ切れる | 短命トークンのまま。STEP4で60日トークンに変換 |
| 投稿時に権限エラー | `instagram_content_publish` 権限が付いていない（STEP2）|
| `me/accounts` が空 | Facebookページを作成していない |
