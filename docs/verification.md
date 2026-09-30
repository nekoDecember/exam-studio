# 検証記録

## 2026-10-01 問題品質・生成UI・LAN対応・本番反映

- 保存前に「学習価値・資料の根拠・問いの明確さ」を審査する経路を接続。資料名・記載場所・グラフの目盛り本数の明白な問題は事前に棄却。
- 表現の修正は同じ題材で最大2回、低価値な題材は別範囲へ移動。ジョブ全体で候補生成を要求問数の3倍に制限。少数・0問は `partial` として終了し、空セットを作らない。
- Dockerでバックエンド **89 passed**（OCRを含む）。フロントエンド **4 passed**、TypeScript検査、Viteビルド、Ruff Fルールが成功。
- 実運用モデル `gpt-5.6-luna` で架空資料の48例を評価。不要問題 **24/24棄却**、有用問題 **24/24採用**。20例の明白な禁止問題はローカルルールで除外し、残る28例を実AIで審査。小規模な固定ケースによる結果であり、任意の実資料への保証ではない。
- 保存volumeを接続しない使い捨てコンテナで実AIの選択問題生成→審査→保存→採点を検証。目標3問、採用3問、棄却0問。目標値・必要手続き・事故報告と保存期間を出題。
- 独立した検証用ブラウザで1440pxと390pxを確認。LAN HTTPで `crypto.randomUUID` が使えない状態でも資料追加・演習開始が成功。全選択・解除、無効な問数、生成→演習、0問完了→再読み込み、横スクロールなしを確認。
- 生成操作を資料一覧より上へ移動。資料追加は折りたたみ可能。問題形式の説明、5/10/20問の選択、保存数と進行段階、少数完了と障害の区別、スマートフォンのナビ文字ラベルを追加。
- 一時的にLANポートを追加した既存 `exam-studio-app-1` で、**http://192.168.1.5:8080** でページ・health・bootstrap・採点API・原本取得を確認。LANオリジンの操作は成功し、別オリジンの書き込みは403。稼働先の390px表示でJavaScriptエラーなし。
- `exam-studio_exam-data` volumeを維持。SQLiteのbackup APIで反映前後のDBを取得し、**26件の全エンティティpayloadが完全一致**。バックアップはGit対象外の `data/backups/` に保存。
- LAN検証時はコンテナ再起動後もLAN HTTPが成功、healthはhealthy、restartはunless-stopped。ColimaのLaunchAgentは起動中・RunAtLoad有効。公開・監視ネットワークとラベルを維持し、監視からのhealth 200を確認。既存公開URLはAccessへ302転送。
- 最終反映は `public-gateway` の `make apps-up APPS=exam-studio` で実施。既存の本番用Compose構成へ戻し、LANポートは任意のoverlayとして分離。Gateway側の変更なし。
- 本番originのページ・health・bootstrapが200、監視ネットワークのhealthが200。`https://exam.nekodec.party` は既存Cloudflare Accessへの302を確認。認証後の外部画面操作は未検証。最終反映後もhealthはhealthy、保存DBの26件が反映前と完全一致。
- 実機の別LAN端末からの接続とMac自体の再起動は未実施。LAN検証はMac上からそのLAN IPへの接続とブラウザ操作によるもの。

再評価：`python scripts/evaluate_quality.py --provider openai`。意味判断の追加ケースは `--cases backend/tests/fixtures/question_quality_semantic.json`。キーとモデルは環境変数で設定する。UI検証は隔離したMockProviderプレビューのLAN HTTP URLを `EXAM_PREVIEW_URL` に設定し、`node scripts/verify-ui.mjs` を実行する。

## 2026-09-28 数値回答・正解漏れ対策の本番反映

- Docker本番イメージのTypeScript検査とViteビルドが成功。アプリコンテナと内部 `/api/health` は正常。
- 公開URLがCloudflare Accessへ転送されることを確認。保存volume、Gateway、監視構成を維持。
- この反映では自動テストスイートを実行していません。

## 2026-09-24 過去問登録機能の削除

- Backend: **30 passed, 2 skipped**。Ruffチェック成功。手入力問題の保存と、過去問登録APIが返らないことを確認。
- Frontend: **3 passed**。DockerビルドでTypeScript検査とVite本番ビルド成功。
- 稼働中コンテナ: healthy。`/api/health` と `/api/bootstrap` は200、bootstrapに`exams`キーなし、旧書類APIは404、過去問URL登録は422。
- SQLiteボリュームは保持。画面・APIから旧過去問データを返さないことを確認。

2026-09-12、macOS / Colima（Linux arm64）で検証。

## 自動テスト

- Dockerの本番イメージでバックエンド **11 passed**。OCRのスキップなし。
- IndexedDB・採点のフロントエンド **3 passed**。
- TypeScript型検査、本番Viteビルド成功。
- Python静的検査（ruff Fルール）成功。
- Docker Composeでビルド・起動し、`/api/health` の正常応答を確認。

バックエンドの対象：初期データ、技術的な問題検証、手入力した問題形式の保存、問題の削除・復元・編集履歴、表記揺れ採点、未接続時の短文未採点、古い回答リビジョンの排除、資料登録からレシピ・複数セット生成まで、過去問登録APIの除去、長文資料の12,000文字チャンク化と48,000文字解析バッチ、60,000文字以内の分割生成、選択資料だけを使う生成、根拠原本、XLSXセル範囲、PDFテキスト、画像OCR、スキャンPDF OCR、無効ファイル、同一オリジン制限、生成失敗時の原子性、OpenAI Responses APIの通信契約。

## ブラウザ操作

- ダッシュボードと演習画面をデスクトップで目視確認。
- 390px幅で演習と下部ナビゲーションを目視確認。
- サンプル問題を選択・即時採点し、見直しフラグとメモを保存。
- 再読み込み後に解答・正誤・フラグ・メモを復元。
- テキスト資料をアップロードし、カテゴリ修正・確定。
- デモ生成した3問を問題バンクでセットに絞り込み、演習へ移動。
- 生成問題の採点・根拠表示を確認。
- 検証サーバーを停止し、画面再読み込み・演習再開・メモ入力を実施。
- オフラインで入力したメモが再読み込み後も復元され、サーバー再開後のSQLite同期をAPIで確認。

## 未検証・制約

OpenAI実APIの呼び出しは実施していません。接続コードは模擬応答で検証し、実際のモデル品質・APIアカウント権限は未検証です。実運用の社内資料、複雑な帳票、ブラウザの全種類、OS自体の強制終了・ブラウザプロセスの強制終了は試していません。初期版は個人のローカル利用を対象とします。

検証用の原本、DB、ブラウザの学習履歴は公開リポジトリに含みません。
