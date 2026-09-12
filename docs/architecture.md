# 実装の構成

- `backend/app/db.py`: SQLite、初期マイグレーション、トランザクション。可変の問題構造はJSON payloadとして保存。
- `backend/app/models.py`: 問題・レシピ・生成条件のPydantic検証。
- `backend/app/parser.py`: PDF、XLSX、画像、OCR、AI未接続時の過去問構造抽出。
- `backend/app/providers.py`: LLMProvider、MockProvider、OpenAIProvider。外部接続はこの境界に集約。
- `backend/app/main.py`: API、生成の原子保存、問題編集履歴、ローカル利用のオリジン検査、静的ファイル配信。
- `frontend/src/store.ts`: IndexedDB、問題の固定スナップショット、ローカル採点、同期。
- `frontend/src/main.tsx`: ダッシュボード、演習、問題バンク、資料、過去問、レシピ、分析。
- `frontend/public/sw.js`: 個人APIデータを含まないアプリ画面のキャッシュ。

## データの役割

今年の資料から選択したカテゴリのチャンクだけを生成の根拠にします。過去問は `question_type`、`structure_json`、`style_profile_json` を渡します。過去問の正解は生成プロンプトに渡しません。生成結果のchunk IDは実際の対象チャンクに照合し、存在しない参照は警告にします。

## 初期版の制約

- 利用者1人、ローカル環境、単一タブでの編集を想定。認証・クラウド同期は未実装。
- AI生成の意味的な正しさや配点の正確さは保証せず、根拠と警告のレビューを前提とする。
- MockProviderは画面・保存の動作検証用。AIの代替品質は提供しない。
- PDF表はレイアウト文字列、XLSX表はセル範囲付きテキスト。スキャンの表セルを厳密に復元する機能はない。
- 過去問の複雑な構造は直接編集と詳細JSONで補正可能。
- 生成は同期HTTP処理。非常に大きな生成ジョブのバックグラウンド化・進捗通知・ジョブ再開は今後の拡張対象。
- 編集履歴はAPIで参照可能。専用の差分・ロールバック画面は未実装。
- 資料の解析結果編集でversionを進める。原本差し替え時は新しい資料として登録する。
