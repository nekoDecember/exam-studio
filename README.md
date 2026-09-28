# 昇格ラボ / Shokaku Lab

今年の試験資料から自分だけの問題集を作って学ぶ個人用Webアプリです。React + TypeScript、FastAPI、SQLite、IndexedDBで構成しています。

**登録資料を内容・正解・解説の根拠に使います。** 問題バンクには問題を手入力でき、選択・穴埋め・単語・短文記述の形式を選べます。演習はAPIキーなしで利用でき、回答・現在位置・見直しフラグ・メモを端末に保存します。

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

1. **試験範囲の資料**でPDF、Excel（XLSX/XLS）、PowerPoint（PPTX/PPT）、HTML、画像、UTF-8 TXTを複数まとめて選ぶか、公開Webページ・公開ファイルのURLを登録します。ファイルごとに成功・失敗が表示され、失敗した資料だけ再試行できます。長い抽出本文は自動分割されます。
2. 同じ画面で使用する資料、選択・穴埋め・単語・短文記述の形式、問数、難易度を選んで**問題を作成**します。カテゴリやレシピの設定は不要です。出題レシピページは通常のナビから外し、直接生成を主導線にしています。
3. **問題バンク**で問題を手入力・編集して形式を設定するか、生成した問題を確認します。**演習する**で答え合わせをします。問題を削除すると、問題本体と編集・演習履歴からも完全に消えます。

選択・穴埋め・単語はローカルで即時採点します。文字幅・大小文字・空白・末尾の句点を正規化し、許容解答も照合します。選択肢は完全一致です。複数空欄の回答は問題で指定された順に ` / ` で区切ります。短文記述はOpenAI接続時に参考点と理由を返します。

記述入力の数値問題は、`%`や個数などの単位を省いて数値だけで回答できます。生成時は問題文に正解が含まれる候補を検出して作り直します。

## AI接続

初期設定は **MockProvider** です。AI未接続でも指定した形式の簡易確認問題を作れますが、資料の抜粋を使うため難易度や文体の再現はできません。AI未接続の短文記述は未採点と表示し、正答率に含めません。

本格的な生成・カテゴリ候補・短文採点、およびPDFのLLMマルチモーダル解析を有効にするには `.env` に設定します。

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

生成物には参照資料、モデル、生成日時、プロンプトバージョンを保存します。抽出資料を最大4行の範囲に分け、未使用範囲を優先しますが、別の問題を作れるよう既使用範囲も再利用できます。モデルには本文と不透明な参照キーに加え、同じ資料に関する既存問題や直前の候補をNGリストとして渡します。本文で裏付けられる数値・固有名称・条件は、直接想起も含めて出題します。保存前は問題形式を検証し、正規化した問題文と解答が既存問題と一致する場合だけ作り直します。話題や資料範囲の重なりだけでは候補を棄却しません。問題生成はサーバー側ジョブで進み、画面移動や再読み込み後も状態を確認できます。直接生成は20問ごとに保存し、途中で失敗しても保存済み分を問題バンクで確認できます。AI障害時も資料の抽出結果は保存され、手動修正できます。

## 保存とオフライン利用

- 演習開始時に問題内容と順序を保存します。確認画面で問題内容を編集すると、現在の演習にも反映され、解答・採点済み結果は消して再回答できるようにします。
- 問題を完全削除すると、編集履歴と保存済み演習からもその問題の内容・回答記録を取り除きます。オフライン端末のコピーも次回同期時に削除します。
- 回答・フラグ・メモ・現在位置をIndexedDBへ逐次保存し、約0.7秒後にSQLiteへ同期。失敗時は端末データを保持して定期的に再試行します。
- 遅れて到着した古いリビジョンで新しいサーバーデータを上書きしません。
- 画面移動・更新・ブラウザ再起動後は端末の最新状態から再開できます。ブラウザの保存データを消すとローカル履歴も消えます。
- Service Workerが画面をキャッシュした後は、サーバー停止中でも登録済み演習を開いて解けます。初回の表示・資料管理・問題生成・AI採点にはサーバーが必要です。
- 同じ演習を複数タブで同時編集する用途や、別端末間の同期は初期対象外です。

## 資料解析の範囲

標準抽出では、PDFの本文をPyMuPDFでページ単位・読み順付きに抽出し、Excelはシートと40行ごとのセル範囲を、PowerPointはスライドごとのテキストを抽出します。旧形式XLS/PPTはLibreOfficeで変換して読み取ります。Webページは表示テキストを抽出し、スクリプト・ナビゲーション等を除きます。画像ページや文字の少ないPDFページはTesseract（日本語・英語）でOCRします。画像だけのPowerPointスライドや複雑な表の厳密な復元は対象外です。文字化けしたPDFには詳細設定の「OpenAIで画像を解析」を利用できます。抽出結果は確認・修正してください。

上限は1ファイルまたは1 URLあたり100MB、Excelシート250,000行・2,000列です。PDFページ数の固定上限はありません。抽出本文は1チャンク12,000文字、登録時のAI解析は1バッチ48,000文字、問題生成は1入力バッチ60,000文字を上限として自動分割します。1回に最大1,000問を指定でき、サーバー側で順に作成・保存します。新しい範囲を優先して使い、資料範囲数より問数が多い場合や既使用範囲しかない場合は範囲を再利用します。URLは公開HTTP/HTTPSの標準ポートのみ対応します。ログインが必要なページ、暗号化されたPDFは登録前に公開・解除してください。

## 開発・検証

Python 3.12、Node.js 22、pnpm 10.28.0を推奨します。PDF本文の標準抽出にはPyMuPDFを使用し、OCRのローカル検証にはTesseract（jpn/eng）とPopplerが必要です。

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
