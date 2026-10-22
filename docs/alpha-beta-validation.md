# Alpha/Beta追加の検証記録

版：0.1／2026-10-22。対象：`codex/alpha-beta-resources`の本変更。
実装元：`origin/codex/stage1-local-worker`、開始時の最新SHA `576595fa8f67eec56a6ffd39ddcd51efafe524bb`。開始時の未コミット変更なし。fetch設定がmain限定だったため、リモートの全ブランチを読み取り確認して実装元refを明示fetchし、そこから作業ブランチを作成した。mainから実装を組み立てていない。

## 実装と採用初期案

Alphaは既存内部ID `resource` のまま。Betaは同じステップのAlpha実増減量に連動する。数値式・同日基準値・有効期間・境界の正本は[基本設計4.1章](resource-simulator-basic-design.md)。DB版1→3、設定hash保持・開始step・バックアップ・復旧・公開切替は[保存・移行設計9章](resource-simulator-storage-design.md)。公開版は独立した2で、旧版1も読む。

ユーザー指定の式・時間帯は確定要件。採用した**実装上の初期案**はBeta初期値100（設定可能）、下限0、夜間基準値不足時は倍率5・未確定表示、観測間隔の許容幅2.5秒。再起動後の最初のステップは非連続、新規作成した同じWorkerのコミット済み初期値は境界の直前値にできる。初期値をOHLCサンプルには数えない。

OHLCクラウド同期・配送台帳・日次退避・履歴グラフは追加していない。設計のみの旧DB版2を実装済みと扱わず、現行DB版3には台帳を含めない。

## 実行環境と結果

Linuxクラウド環境。Worker CPython **3.13.16**（プロジェクト内`.tools/python`、`.venv`、uv.lockを変更せず`uv sync --locked`）、API CPython **3.11.17**（独立`api/.venv`、固定requirements.txt）。Node **24.19.0**、npm **11.9.0**、既存package-lock.jsonで`npm ci`。システム既定のPythonやPATHは変更していない。

| 実施コマンド | 実測結果 |
| --- | --- |
| `uv run --locked python -m unittest discover -s tests -v` | **65件成功**。失敗0、skip0。既存39件を維持し、2リソース化による確定足/outbox件数のみ1→2に更新 |
| `api/.venv/bin/python -m unittest discover -s api/tests -v` | **7件成功**。失敗0、skip0 |
| `npm --prefix web test` | **9件成功**。失敗0、skip0 |
| `npm --prefix web run build` | TypeScript検査・Vite production build成功 |
| `CHROMIUM_PATH=/usr/bin/chromium node web/tests/local-browser.mjs` | Chromium **154.0.8037.57**で4検証群成功。APIはローカルfixture、Azure接続なし |
| `git diff --check` | 成功 |

Playwright管理ブラウザの取得先はネットワーク制限で403だったため、既設`/usr/bin/chromium`でブラウザ検証を実施した。TLS/署名検証の無効化やネットワーク設定の変更は行っていない。取得失敗をブラウザ検証成功として数えていない。

## 何を確認したか

| 要件・AC | 試験と結果 |
| --- | --- |
| AC-02/03/21 | 昼のAlpha+2→Beta要求+10、Alpha要求−8/実量−3→Beta要求−15、Beta自身の0制限。夜の04時100・18時130/70→x=30、Alpha−2→Beta要求−60。x=0は不動。Beta overflowは停止 |
| AC-22 | 03:59:59→04:00、17:59:59→18:00、境界+0.4秒の通過。前コミット値を記録し、その18時ステップから新x。午前0時でx/対象日不変、翌04時で昼5に切替 |
| AC-22 | 2.5秒は連続、2.500001秒は欠測。1秒でも再起動直後は未取得、3600秒スリープは1回だけ進めて基準なし。初回・境界ちょうど開始・停止中境界・日跨ぎ再開、18時だけ取得した際のnullを確認 |
| AC-04/14/22 | 確定x・有効期間・基準日を保存し、午前0時後の再起動で復元。期限切れ後は旧xを使わず、別日の基準値を組み合わせない。境界直後の再起動で二重確定しない |
| AC-23 | 実装元の版1 DDLを凍結したfixture（空stepの旧run＋active step3、確定足/分内足/outbox再試行あり）を移行。旧6テーブル全行・rowidのfingerprintと件数が完全一致。旧JSON/hashのまま保存規則で継続可能 |
| AC-23 | Beta開始step3で追加前の足0件、次step4で最初のサンプル1件。新規runは開始0と新初期値。旧inactive runへBeta履歴を作らず世代2→3も保持 |
| AC-15/23 | バックアップ不能・旧outbox欠落で変換せず停止。backup後/DDL/Beta作成/検査/version設定に失敗を注入し版1全行を保全。version設定後COMMIT前の子プロセスkillでも版1＋検証済みbackupへ復元。backupのfile hashと旧論理hashを照合 |
| AC-23 | 明示migrate CLI→通常run 1 stepを一時DBで実行し、Alpha/Beta step4、Beta開始3を確認。移行再実行で重複追加やバックアップの上書きなし |
| AC-08/09/24 | 両OHLC・outboxが同じトランザクション。Alpha checkpoint直前の失敗とBeta outbox書込失敗で全体ROLLBACK。18時基準/xも戻る。Betaは開始stepを差し引く整合性検査、inactive runのBeta count破損も検出 |
| AC-04/24 | 既存の実SQLiteトランザクション中killと再開を維持。別接続の同期読取は未コミットBeta変更を含めず、コミット済みの不一致は公開せず拒否 |
| AC-07/16/25 | TableテストダブルでETag競合、遅延step/旧run、同世代UUID衝突、同step公開日時不変、通信失敗中4step継続→最新のみ復帰。公開版1→2後の版後退を拒否 |
| AC-18/25 | 旧データをAlpha＋Beta未提供へ正規化し、0を捏造しない。API HTTPハンドラー200/no-store・404・503、公開項目のトップ/ネストwhitelist、失効/不整合の基準を拒否 |
| AC-19/20/25 | Web即時取得・約1秒更新・非重複要求・タブ復帰・旧応答破棄・run/step/版比較・障害後の自動復帰を維持。取得失敗中は最後の両値を保持 |
| AC-17/26 | 320/390/767pxで縦1列、768/1280pxで同幅横2列。最大有限浮動小数級の長い現在値と増減量でもdocument/card内に横溢れなし。Alpha/Beta名と色、基準未確定、停滞・取得失敗後の保持/復帰を確認 |
| NF-02/F-06 | 既存の固定時計周期試験を維持。保存0.2秒なら開始1/2/3秒、保存1秒ちょうど/1.2秒の期限超過、3600秒スリープでも追いつきループなし |

ブラウザの統合fixtureは**実SQLite→cloud.committed_snapshot→CurrentPublisher→Tableテストダブル→実read_current→React**を通す。HTTPハンドラーは独立したAPI試験で確認する。長い数値・旧版/障害等の画面状態はfixtureで注入したもの。これらを実Azure試験と呼ばない。

ローカル生成物（Git除外）：`.cache/worker-tests.log`、`.cache/validation/alpha-beta-browser.json`、`alpha-beta-320.png`、`alpha-beta-1280.png`。320pxと1280pxのスクリーンショットは目視でも確認し、数値・状態の可読性と列配置を確認した。

## 未検証・未実施

- 今回変更のWindows 11実機試験、実スマートフォン、実OSのスリープ・停電・物理通信断、実I/O障害・大量運用DBでの移行時間/容量。
- 今回変更のAzure Table実通信・API/SWA本番デプロイ・本番料金/通信量/応答時間。旧1リソースの過去のAzure結果は[最新値検証記録](latest-values-validation.md)に区別して保持。
- 配送台帳、OHLCクラウド同期、日次Blob/退避、履歴グラフは対象外のまま。

**運用Workerを停止/再起動せず、運用DBやAzureへ書き込んでいない。push・merge・本番デプロイは未実施。** 公開切替は保存設計9.6のAPI/Web先行→旧版互換確認→Worker停止/backup/移行→再開/比較の順で別途行う。
