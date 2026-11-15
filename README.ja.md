# 商衡SI（ShangHeng SI）

[English](README.md) | [简体中文](README.zh-CN.md) | **日本語**

企業の商務文書（契約書、見積書、注文書、引合書、各種レター）を対象とした、社内 LAN で利用する AI 一次審査ツールです。

**現在のリリース：V3.2.1**（2026-11-15 リリース。単独ではリリースされなかった V3.2 を含みます）。

機密情報は社内ネットワークの外に出ません。文書はローカルで解析・チェックされ、**自動的にマスキング**されます。クラウドの大規模言語モデル（LLM）に送られるのはマスキング後のテキストだけで、審査結果はローカルで実名に復元してから表示されます。

> ⚠️ 本ツールは**一次審査**のみを行います。結果は法務・コンプライアンス・営業担当者による審査に代わるものではありません。

## 処理の流れ

```text
アップロード → ローカル解析・チェック → 自動マスキング → ユーザー確認 → クラウド LLM による審査
            → ローカルで復元し指摘一覧を表示 → 追加質問と指摘ごとの議論
            → Word レポート／注釈付きコピー／改訂版の出力
```

1. **アップロード**：複数ファイルに対応しています。立場（販売／購買／その他）、出所（自社発信／相手方から受領）、外部送信ポリシーを選択します。
2. **ローカル処理**：統一ドキュメントモデルへの解析、ローカル整合性チェック（金額、合計、日付、通貨など）、会社名・人名・金額・口座番号・電話番号などのマスキングを行います。マスキングした箇所は「客户\_A」「\[金额\_1]」のようなコードに置き換えます。
3. **確認**：どの当事者が「自社」かを確認し、マスキングの概要と実際に送信されるテキストをプレビューします。
4. **AI 審査**：LLM はすべての審査項目に結論を出し、位置番号と原文の逐語引用付きで指摘を列挙します。引用が正確かどうかはローカルで検証します。
5. **結果**：指摘一覧は実名に復元して表示され、審査の進行に合わせてストリーミング表示されます。追加質問（送信前に同様にマスキング）のほか、指摘ごとに議論して議論稿を採用したり、Word レポート・注釈付きコピー・改訂版を出力したりできます。モデルの返信には引用アンカーが付き、クリックすると引用元（ナレッジベース、履歴、法規）をポップオーバーで表示します。

外部送信ポリシーが `local_only` のタスクはモデルを一切呼び出さず、ローカルチェックのみを行います。

## 機能モジュールと新バージョンのハイライト

サイドバーは次の 5 項目です。

| モジュール | 説明 |
|-|-|
| ワークベンチ | 統合ホーム（V3.2 で「スマートアシスタント」から改称）。同じセッションで審査の開始、文書への質問、指摘ごとの議論ができます |
| スマート審査 | 上記の文書審査フロー |
| 会議メモ | 会議音声をローカルで文字起こしし、議事録を生成します |
| 翻訳 | 文書と貼り付けテキストの翻訳。ローカルで動作します |
| 履歴 | 過去の審査タスクとセッションの一覧（V3.2 で「審査履歴」から改称） |

V3.2 / V3.2.1 のハイライト：

- **観点別グループ並行審査**：審査項目を観点ごとにグループ化して並行審査し、結果が速く返ります。
- **マスキング精度の強化**：ラベル付きの人名と完全な会社名は、保持せず直接置換するようになりました。
- **マスキングの取り消し**：タスク単位で、特定の項目をマスキング対象から除外できます。
- **点滅しないローディング**：ローディング表示は 300ms 遅れてから表示されるため、速い操作でスピナーが点滅しません。

## セキュリティとプライバシーの設計

- **唯一の外部通信点**：インターネットにアクセスできるのは `backend/app/llm/gateway.py` だけです。ゲートウェイは常に「月間呼び出し上限チェック → 送信前再スキャン → 送信 → `llm_calls` への記録」の順に処理します。
- **送信前再スキャン**：送信直前にもう一度スキャンし、対応表のすべての実値や、マスキング漏れの電話番号・メール・口座番号・身分証番号・金額が残っていないかを確認します。見つかった場合は送信を遮断します。
- **テキストのみ送信**：画像や元ファイルはモデルに送信しません。
- **保存時の暗号化**：元ファイル、対応表、機密フィールドはマスターキーを用いて AES-256-GCM で暗号化します。
- **厳格な所有者チェック**：すべてのタスク API で所有者を確認し、本人以外（管理者を含む）には 404 を返します。
- **ログの安全性**：ログに原文、実名、金額、API キーを出力しません。

## 対象文書

| 種類 | 例 |
|-|-|
| 契約書 | 販売契約、購買契約、基本契約 |
| 見積書 | 見積書、プロフォーマインボイス |
| 注文書 | 発注書、供給指示書 |
| 引合書 | 引合書、RFQ |
| レター | 供給レター、確約書、通知書、クレームレターなど |

対応フォーマットは DOCX、PDF（テキスト版・スキャン版）、XLSX、JPG、PNG です。旧形式の Office ファイルは、オフラインの converter サービスがローカルで変換します（システム構成を参照）。

審査の観点：表現、論理、法律、コンプライアンスと制裁、商務条件。

## 技術スタック

| レイヤー | 採用技術 |
|-|-|
| フロントエンド | Vue 3、TypeScript、Vite、Element Plus、Pinia、Vue Router |
| バックエンド | Python 3.12、FastAPI、Uvicorn、Pydantic v2 |
| データベース・ジョブキュー | PostgreSQL 16（`SELECT … FOR UPDATE SKIP LOCKED` によるジョブキューも兼ねる） |
| ORM・マイグレーション | SQLAlchemy 2、Alembic、psycopg 3 |
| 解析 | python-docx + lxml、openpyxl、pdfplumber、RapidOCR（onnxruntime、CPU 推論） |
| 暗号 | cryptography（AES-256-GCM）、argon2-cffi（argon2id） |
| LLM | OpenAI 互換の Chat Completions API（DeepSeek など）。ゲートウェイ内で `httpx` を介してのみ呼び出す |
| デプロイ | Docker Compose v2、Caddy 2（HTTPS・静的ファイル配信） |

## システム構成

1 台の Ubuntu Server 24.04 仮想マシン上で 7 つのサービスが動作します（定義は `deploy/docker-compose.yml`）。

```text
ブラウザ（LAN、HTTPS）
  └─> web     Caddy：フロントエンド静的ファイル + /api の転送
        └─> app     FastAPI：全 API、追加質問チャット
              ├─> db      PostgreSQL：データ + ジョブキュー
              └─> /data/files（暗号化された元ファイル、共有ボリューム）
worker    解析、OCR、ローカルチェック、マスキング、AI 審査、バックアップ、定期クリーンアップ
          └─> クラウド LLM API（唯一許可された送信先）
converter / transcriber / translator
          ネットワークに一切接続しないオフライン補助サービス（network_mode: none）。
          worker とは共有 /spool ボリューム経由でのみファイルをやり取りします
          （旧形式の変換、音声の文字起こし、ローカル翻訳）
```

## ディレクトリ構成

```text
backend/    FastAPI アプリ、worker、Alembic マイグレーション、リソース、テスト
frontend/   Vue 3 シングルページアプリ
converter/  オフライン旧形式変換サービス（ネットワーク接続なし）
transcriber/ オフライン音声文字起こしサービス（ネットワーク接続なし）
translator/ オフラインローカル翻訳サービス（ネットワーク接続なし）
deploy/     docker-compose.yml、Caddyfile、.env.example
docs/       SPEC.md（全仕様）、TASKS.md（タスク一覧）、open_questions.md、design/、
            バージョン別フォルダ（v2/ … v321/）、acceptance/（導入・アップグレード記録）、ops/
```

## はじめに（開発環境）

必要なもの：Docker（Compose v2 を含む）。フロントエンドを開発する場合は Node.js も必要です。バックエンドの実行・テスト・マイグレーションはすべて Docker 内で行います。以下は PowerShell の例です。bash では `${PWD}` を `$(pwd)` に置き換えてください。

```powershell
# 実行イメージをビルドし、開発用マスターキーを生成（既存ファイルは上書きしません）
docker build -t aireview-backend:dev --target runtime backend
docker run --rm -v "${PWD}/deploy/secrets:/out" aireview-backend:dev python -m app.cli gen-key --out /out/master_key

# 環境変数ファイルを用意し、POSTGRES_PASSWORD を設定
# （80/443 が使用中の場合は HTTP_PORT/HTTPS_PORT を 8080/8443 に変更）
copy deploy\.env.example deploy\.env

# システム全体を起動（app の起動時に alembic upgrade head を自動実行）
docker compose -f deploy/docker-compose.yml up -d --build
```

フロントエンド開発サーバー（`/api` は起動中のシステムへプロキシ）：

```powershell
npm --prefix frontend ci --include=dev
npm --prefix frontend run dev        # http://localhost:5173
```

バックエンドのテスト：

```powershell
docker build -t aireview-backend-test --target test backend
docker network create aireview-test
docker run -d --name aireview-test-db --network aireview-test -e POSTGRES_PASSWORD=test postgres:16.15
docker run --rm --network aireview-test -e TEST_DATABASE_URL=postgresql://postgres:test@aireview-test-db:5432/postgres -v "${PWD}/backend:/srv/backend" aireview-backend-test pytest -q
```

`deploy/.env`、`deploy/secrets/`、`deploy/backup/` は git の管理対象外です。

## プロジェクトの状況

現在のリリースは V3.2.1（2026-11-15）で、本番サーバーで稼働中です。リリース報告は [docs/v321/reports/V321-Z.md](docs/v321/reports/V321-Z.md)、本番アップグレード記録は `docs/acceptance/` にあります。タスク一覧は [docs/TASKS.md](docs/TASKS.md) を参照してください。

## ドキュメント

- [docs/SPEC.md](docs/SPEC.md)：製品・開発の全仕様（中国語）
- [docs/TASKS.md](docs/TASKS.md)：タスク分割と進捗
- [docs/open\_questions.md](docs/open_questions.md)：仕様に明記されていない細部とその決定
- `docs/acceptance/`：本番サーバーの導入・アップグレード記録。`docs/ops/`：運用ガイド（導入、アップグレード、バックアップ、復旧）
- [CLAUDE.md](CLAUDE.md) / [AGENTS.md](AGENTS.md)：本リポジトリで作業する AI コーディングアシスタント向けのルール
