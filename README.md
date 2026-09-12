# 昇格ラボ / Shokaku Lab

今年の試験資料と過去問をもとに、自分だけの問題集を作って学ぶ個人用Webアプリです。React + TypeScript、FastAPI、SQLite、IndexedDBで構成しています。

**今年の資料は内容・正解・解説の根拠に、過去問は形式・構成・文体の参考に使います。** 演習はAPIキーなしで利用でき、回答・現在位置・見直しフラグ・メモを端末に保存します。

## 起動

Docker Engine / Docker Desktop と Docker Compose が必要です。

```sh
git clone https://github.com/nekoDecember/exam-studio.git
cd exam-studio
cp .env.example .env
docker compose up -d --build
```

[http://localhost:8080](http://localhost:8080) を開きます。環境によっては `docker-compose` コマンドを使用してください。初回はサンプル問題6問が登録されます。

```sh
docker compose logs -f app
docker compose stop        # データを残して停止
docker compose start       # 再開
```

SQLiteと登録原本は `exam-data` ボリュームに保存されます。`docker compose down -v` は保存データを削除するため、通常の停止には使わないでください。

## 使い方

1. **試験範囲の資料**にPDF、XLSX、PNG/JPEG/WebP、UTF-8 TXTを登録。
2. 抽出本文・ページ・シート・セル範囲を確認し、カテゴリを修正。1資料・1範囲に複数カテゴリを指定できます。
3. **過去問・出題形式**で過去問を登録。本文、選択肢、正解、大問・小問構造、空欄、語群、文体情報を確認・編集します。
4. **出題レシピ**でカテゴリ、過去問の形式、大問・小問数、問題形式、文字数、難易度などを設定。
5. 生成した問題を**問題バンク**で編集・確認。カテゴリ、形式、セット、大問、お気に入り、間違えた問題で絞り込めます。
6. **演習する**で答え合わせ。解説・根拠を読み、見直しやメモを追加。**学習の記録**から再開・復習できます。

選択・穴埋め・単語はローカルで即時採点します。文字幅・大小文字・空白・末尾の句点を正規化し、許容解答も照合します。選択肢は完全一致です。複数空欄の回答は問題で指定された順に ` / ` で区切ります。短文記述はOpenAI接続時に参考点と理由を返します。

## AI接続

初期設定は **MockProvider** です。デモ生成は資料の一文を抜粋した確認用の問題であり、指定の文体・難易度・空欄構成を再現するAI生成ではありません。AI未接続の短文記述は未採点と表示し、正答率に含めません。

本格的な生成・カテゴリ候補・過去問解析・短文採点を有効にするには `.env` に設定します。

```dotenv
LLM_PROVIDER=openai
OPENAI_API_KEY=your-key-here
OPENAI_MODEL=gpt-4.1-mini
```

```sh
docker compose up -d --force-recreate
```

モデル名は利用可能なResponses API対応モデルに変更できます。APIキーはサーバー環境変数だけで保持し、ブラウザへ返しません。AI有効時は選択した資料本文・形式情報・解答がOpenAI APIに送信され、API利用料が発生します。Responses APIは `store: false` で呼び出します。

参照仕様：[OpenAI Responses API](https://developers.openai.com/api/reference/resources/responses/methods/create)

生成物には参照資料、モデル、生成日時、プロンプトバージョンを保存します。表示・採点不能な形式だけを拒否し、根拠の不足・過去問との類似・長さ・AI品質チェックの懸念は警告として表示します。AI障害時も資料の抽出結果は保存され、手動修正できます。生成は全問題の検証後にトランザクションで保存します。

## 保存とオフライン利用

- 演習開始時に問題内容と順序を固定。問題バンクを後から編集しても、開始済みの演習は変わりません。
- 回答・フラグ・メモ・現在位置をIndexedDBへ逐次保存し、約0.7秒後にSQLiteへ同期。失敗時は端末データを保持して定期的に再試行します。
- 遅れて到着した古いリビジョンで新しいサーバーデータを上書きしません。
- 画面移動・更新・ブラウザ再起動後は端末の最新状態から再開できます。ブラウザの保存データを消すとローカル履歴も消えます。
- Service Workerが画面をキャッシュした後は、サーバー停止中でも登録済み演習を開いて解けます。初回の表示・資料管理・問題生成・AI採点にはサーバーが必要です。
- 同じ演習を複数タブで同時編集する用途や、別端末間の同期は初期対象外です。

## 資料解析の範囲

PDFはページ単位の本文・レイアウト文字列、XLSXはシートと40行ごとのセル範囲・表を抽出します。画像および文字の少ないPDFページはTesseract（日本語・英語）でOCRします。PDFの複雑な表は文字レイアウトとして保持し、厳密なセル構造への復元は行いません。OCRや過去問の自動分割結果は必ず確認・修正してください。

上限は1ファイル20MB、PDF100ページ、XLSXシート50,000行・1,000列、1回の生成の対象本文10万文字です。旧Excel形式 `.xls` は `.xlsx` に変換して登録してください。暗号化されたPDFは解除してから登録してください。

## 開発・検証

Python 3.12、Node.js 22、pnpm 10.28.0を推奨します。OCRのローカル検証にはTesseract（jpn/eng）とPopplerが必要です。

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r backend/requirements.txt
cd backend
python -m pytest -q
uvicorn app.main:app --reload --port 8000
```

別ターミナルで：

```sh
cd frontend
corepack enable
corepack prepare pnpm@10.28.0 --activate
pnpm install --frozen-lockfile
pnpm dev
# http://localhost:5173
pnpm test
pnpm build
```

CIではAPI・保存・採点・資料解析・OCR・AIアダプタ契約のテスト、フロントエンドテスト、TypeScript検査、本番ビルド、Docker起動確認を実施します。OpenAIの実APIを呼ぶテストは含まず、モックの応答で接続仕様とエラー処理を検証します。

## 公開とデータ管理

このリポジトリはアプリのソースと架空のサンプルだけを公開します。登録原本、DB、演習記録、`.env`、APIキーはGit管理対象外です。アプリ自体には認証を設けず、Composeの公開ポートを `127.0.0.1` に限定しています。認証を追加せずにインターネットへホストしないでください。

ソース構成・設計上の制約は [docs/architecture.md](docs/architecture.md)、検証記録は [docs/verification.md](docs/verification.md) を参照してください。

## 既存基盤への登録

`public-gateway` の公開サービス・リポジトリCSVに `exam-studio` として登録しています。
公開先は `https://exam.nekodec.party`（`SANDBOX_DOMAIN=nekodec.party`）、Accessは既存の `owner` ポリシーです。
このURLは公開先として予約した設定です。DNS・Access・Tunnelは管理用認証がある端末で反映してください。
公開用Composeはホストポートを削除し、Gatewayが専用の `public-exam-studio` networkを付与します。
DNS・Access・Tunnelへの実反映は、設定済みの管理端末でTerraform plan確認後に実施します。

監視は `observability` network経由の `/api/health` HTTP probe、Dockerのhealth・リソース・OS情報、Alloy/Lokiのログを使用します。
`monitoring-starter` の全体起動一覧・Prometheusの固定ターゲットにも登録しています。
単体起動では外部networkを要求しません。監視だけを有効にしてローカルポートも残す場合：

```sh
# monitoring-starterが起動し、observability networkが存在する状態で実行
docker compose -f compose.yaml -f compose.monitoring.override.yaml up -d --build
```

Cloudflareで公開する場合は、設定済みの `public-gateway` で実行します。

```sh
make apps-up APPS=exam-studio
make gateway-up
# 続いてCloudflareのplan/apply（既存stateと認証がある管理端末で）
```

監視上のサービス名は `exam-studio` です。GrafanaのEnvironmentは `local` を選択します。
