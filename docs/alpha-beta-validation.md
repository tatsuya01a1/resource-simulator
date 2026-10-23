# Alpha/Beta追加の検証記録

版：0.3／2026-10-23。対象：`codex/alpha-beta-resources`の変更。Linux・Windowsリハーサル結果を残し、本番リリース記録を末尾に追記。
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

## Windows実機リハーサル（2026-10-23追記）

以下は0.1の未検証事項のうちWindowsローカル移行・再開を検証した記録。上記Linux結果と当時のpush未実施記録を変更しない。本番切替・Azure検証ではない。要件・数値モデル・実装コードの変更なし。

### 環境・対象・隔離

- 実行：**2026-10-23 15:27:23〜15:28:44 JST**（実時計のバックアップ・移行・70＋5ステップ・独立復元）。固定時計テストも同日のWindowsで実行。
- OS：Microsoft Windows 11 Pro、10.0.26200、64bit。uv **0.13.4**、プロジェクト内CPython **3.13.16**（`.venv/Scripts/python.exe`、本体`.tools/python/cpython-3.13.16-windows-x86_64-none/python.exe`）。`uv sync --locked`成功。PATH・レジストリ・システムPythonを変更せず、依存更新なし。
- 開始時ブランチ：`codex/stage1-local-worker`、HEAD `576595fa8f67eec56a6ffd39ddcd51efafe524bb`。未コミット変更なし。
- `git fetch origin codex/alpha-beta-resources`後、レビュー済み`03d79e81b40e4828b2e094638451793146d610d2`からの後続コミット・差分なし。`git switch --track origin/codex/alpha-beta-resources`で切替。**使用SHAは`03d79e81b40e4828b2e094638451793146d610d2`**。uv.lockのSHA-256は`82335bf9d242ee896dd35b03d78f218c3d327a986895d41035879380c7f3c275`。
- Win32_Processの起動引数と既存の起動補助コードを読み取り照合。実際の運用DBは`C:\Users\LovelyQte\Documents\resource-simulator\.secrets\worker.sqlite3`。既定パスからの推測ではない。運用プロセスPID **3084 / 1856**（Python起動プロセスと実体）は前後で同じ起動引数・PIDのまま稼働。`scripts/worker_control.py`は**一度も実行していない**。
- 全成果物はGit除外の専用ディレクトリ`C:\Users\LovelyQte\Documents\resource-simulator\.cache\windows-rehearsal-20261023-152723`。以下ではこの絶対パスを`$trialRoot`と表記。運用ディレクトリと分離し、一意な新規ディレクトリ・排他的作成・存在確認により既存ファイルを上書きしていない。
- 運用DBへの接続はSQLite URI `mode=ro`のBackup API取得のみ。元DBのjournal_mode等を設定せず、運用DBへのSQL書込・移行・削除・差替えなし。運用Workerの停止・再起動・設定変更なし。本試験からAzure送信・設定変更・デプロイなし（元から稼働中の運用Workerの通常同期は継続）。main・デプロイ元へのmergeなし。

### 実施コマンドと取得方法

初期確認は`git status --short`、`git branch --show-current`、`git rev-parse HEAD`、`git log --oneline <レビューSHA>..origin/codex/alpha-beta-resources`、`git diff --stat <レビューSHA> origin/codex/alpha-beta-resources`、`uv --version`、`.venv/Scripts/python.exe --version`。OSと起動引数は`Get-CimInstance Win32_OperatingSystem`と`Get-CimInstance Win32_Process`で確認した。

検証用ローカルスクリプトを`uv run --locked python .cache/rehearse_windows.py`で実行した。スクリプト自体もGit除外で保持し、SHA-256は`f75d2d41b5bd6eedacb4af57691dad675ff34706d3f611164502576ee6fa91fe`。DB・manifest・全ステップ記録・ログを公開リポジトリへ含めない。

取得・復元で実行した処理は以下（`source`は上記の確認済み絶対パス、`backup`等は`$trialRoot`下の絶対Path）。本番停止は不要で、元DBは読み取り専用のまま取得した。

```python
with backup.open("xb"):
    pass
with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src:
    with closing(sqlite3.connect(backup)) as dst:
        src.backup(dst, pages=256, sleep=.05)
        dst.execute("PRAGMA journal_mode=DELETE")  # バックアップ先だけ
# backupを閉じてreadonly再接続し、版・物理/FK・全run論理検査・fingerprintを保存
assert not trial.exists()
shutil.copyfile(backup, trial)  # 検証済み単体バックアップから新規パスへ
assert sha256(trial) == sha256(backup)
# 全リハーサル後、元の版1バックアップをさらに新規の独立復旧先へ
assert not recovery.exists()
shutil.copyfile(backup, recovery)
```

WAL稼働中の主DBだけのコピーは行っていない。復元先には既存DB/WAL/SHMなし。試験Workerは次の既存CLIを、`sys.executable`（固定Python）から**独立子プロセス**として実行した。`--cloud-config`なし。起動は`subprocess.Popen(..., creationflags=subprocess.CREATE_NO_WINDOW)`、自分で起動したPIDを`poll()`で監視し、強制停止は行っていない。

```powershell
$trialRoot = 'C:\Users\LovelyQte\Documents\resource-simulator\.cache\windows-rehearsal-20261023-152723'
$trialDb = Join-Path $trialRoot 'trial.sqlite3'
uv run --locked python -m resource_simulator migrate --db $trialDb --config config/simulator.toml
uv run --locked python -m resource_simulator migrate --db $trialDb
uv run --locked python -m resource_simulator run --db $trialDb --steps 70
# 終了コード0・プロセス終了・保存状態を確認し、別の新規パスへBackup APIで停止状態を保存
# 4秒待機しDBの状態が変化しないことを照合後、別プロセスで再開
uv run --locked python -m resource_simulator run --db $trialDb --steps 5
uv run --locked python -m unittest discover -s tests -v
git diff --check
```

### 固定したバックアップと移行・復旧（AC-04/14/15/23/24）

運用DBの後刻の状態は比較基準に使わず、最初の整合した`verified-v1.sqlite3`を固定基準にした。全run（実データは1 run）について既存`validate_all(legacy=True)`で設定hash、checkpoint、分内/確定OHLC、outboxの対応を検査。物理整合性`ok`、foreign_key_checkは0件、DB版1。移行前後・移行処理のバックアップ・独立復元でも同じ検査に合格。

| 旧テーブル | バックアップ件数 | 版3移行直後 | 独立版1復元 |
| --- | ---: | ---: | ---: |
| simulation_runs | 1 | 1 | 1 |
| active_run | 1 | 1 | 1 |
| checkpoints | 1 | 1 | 1 |
| open_candles | 1 | 1 | 1 |
| candles | 1433 | 1433 | 1433 |
| outbox | 1433 | 1433 | 1433 |

- 旧6テーブルの`SELECT rowid,* ORDER BY rowid`の全行を直接比較し完全一致。既存`migration.fingerprint()`の論理SHA-256も全比較先で`d9faae67bd6052d007ed16d341c093111516a30da67f7259c999b4d1c790a4cc`と一致。JSONの元文字列・設定hash・outboxのattempts/next_attempt_atを含む。
- active run：`ac23fe78-636c-40ba-ab6d-bef83f17c26f`、世代1、Alpha step **85960**、値 **672.0068116941972**、計算日時`2026-10-23T06:27:22.671877+00:00`。run/世代/step/値/日時は移行直後も一致。
- 設定JSONは旧6キーのまま。config_version=`c693897acb23dfd3ba0cd5bf3a5f1b2c02dfe1bae719bfeb6c062403a5895be6`、rules_hash=`4cbecdf143cc115abd8b652c773aa3bee68c7b6f56aed68a7b5e7ae9067e37a1`を保持。
- 元の検証済みバックアップfile SHA-256：`7f4fb06084680a88086709ae66383842fe048a918d15f392842c6a6b0d7f205a`。移行前のtrialコピーも一致。
- 版3移行でBeta初期値**100**、start_step=**85960**、step/timeはAlphaと一致。Beta確定足0・分内足0で、追加前履歴なし。追加したAlpha開始位置は0。DB版3の全run・物理/FK検査成功。
- 移行処理自身のバックアップ：`trial.sqlite3.v1-backup-9b7de2f2d4ea489c8fa448f272df391b.sqlite3`。manifestのverified=true、source_schema=1、target_schema=3、件数・論理hash一致。file SHA-256=`50bf1699ad950a170b94a11d572a87283dc99079c68bdf46f8edd75e1912c698`をmanifestと再照合。外側のバックアップと物理hashが異なっても、論理全行・版・整合性は一致している。
- 同一trial DBへmigrateを再実行し`already_migrated=true`。旧全行・Beta状態・開始位置不変、Beta重複なし、manifestは1件のまま。
- 最後に元の`verified-v1.sqlite3`を**`independent-recovery-v1.sqlite3`へ独立復元**。file SHA-256は元と完全一致、旧全行/rowid・論理fingerprintも一致し、版1・全run・integrity/FK検査成功。進行済みtrial DBは上書きしていない。これは移行前の状態の復元であり、**移行後75ステップを保持して版1に戻せることを意味しない**。

### 実時計の再開・正常停止・再起動（AC-04/08/09/14/21/24、NF-02）

S=85960、最初のN=70、再起動N=5。保存DBを読み取り専用トランザクションで約25ms間隔で観測し、欠落のない**75件の連続したstep**を取得。全件でAlpha/Betaのstep・計算日時一致、`Beta要求量=同stepのAlpha実増減量×倍率`、`Beta値=max(0,前Beta値+要求量)`、実増減量を照合した。乱数は固定していない。

| 時点（JST） | 共通step | Alpha | Beta | Beta開始step |
| --- | ---: | ---: | ---: | ---: |
| 移行直後（15:27:23） | 85960 | 672.0068116941972 | 100 | 85960 |
| 70ステップ正常停止（15:28:34） | 86030 | 687.8462182647313 | 179.19703285267076 | 85960 |
| 別プロセス5ステップ正常停止（15:28:44） | 86035 | 681.183176371926 | 145.88182338864442 | 85960 |

- 最初の観測は15:27:25.807910、15:28の分境界を通過。70ステップ後の保存状態は`stopped-after70.sqlite3`へBackup APIで保全。子プロセス終了を確認し4秒待機して状態不変を確認。次の起動ログは保存step86030を報告し、次観測は15:28:40.177025。停止中の追いつき計算なく追加5ステップだけ進行した。
- 新規子プロセスPID：70ステップ **24212**、5ステップ **4664**。両方終了コード**0**、`poll()`で終了確認、終了後のWin32_Process列挙でも試験Workerなし。stdoutは各起動時に保存run/step/config hashを報告。stderrは両方空、worker.logは初期化・正常停止4行のみ、WARNING/ERROR/Tracebackなし。
- Beta開始位置とresource_startsの初期値は移行時のまま。再起動前後で基準値・倍率・対象日・有効期間・取得状態も一致。実時計では昼の倍率5・pending、04/18基準値と確定倍率/有効期間はnull。未取得の境界値を補っていない。**確定した夜間倍率の復元は下記固定時計テストによる確認**。
- 最後の両計算日時は`2026-10-23T06:28:44.176952+00:00`。Alphaサンプル合計**86035=step**、Beta合計**75=86035−85960**。Alpha確定1434＋分内40サンプル、Beta確定1（35サンプル）＋分内40サンプル。確定足とoutboxはAlpha各1434件、Beta各1件。最後の全run/物理/FK検査成功。
- 実時計ではAlpha最小658.3645826741982、Beta最小31.78885490000539で、0への到達はなかった。全観測で下限を含む式を照合したが、**下限での切捨ての発生確認は固定入力テスト**で行った。

### Windows固定時計・一時DBの境界/失敗試験

`uv run --locked python -m unittest discover -s tests -v`：**65件成功、失敗0、skip0、6.703秒**。一時DBは`.cache/tests`配下。全ログはGit除外`.cache/windows-alpha-beta-tests.log`（SHA-256 `47900f9dadb6d2ac4365f2a53e866d7946af04e4e60f81e6671d0d0c2c21c533`）。Azureはテストダブルであり、実通信ではない。

| 対応AC | Windowsで成功した既存テストと意味 |
| --- | --- |
| AC-03/21 | `test_day_five_times_actual_alpha_delta_and_both_lower_bounds`：Alpha実量使用とAlpha/Beta両下限0。`test_absolute_night_difference_is_fixed_and_zero_is_stationary`：差の絶対値、差0、午前0時で倍率保持 |
| AC-22 | `test_exact_and_inexact_boundaries_take_pre_step_value`、`test_evening_crossing_applies_new_x_on_same_step`：04/18時と+0.4秒で直前コミット値を取得、18時同stepからx、翌04時で失効 |
| AC-22 | `test_gap_allowance_and_restart_are_distinct_from_crossing`：2.5秒は連続、2.500001秒は欠測、再起動直後は1秒でも非連続。`test_restart_on_boundary_never_acquires_or_repeats_it`：再起動で境界を補わず二重取得なし |
| AC-04/14/22 | `test_restored_night_retains_x_until_dawn_but_not_next_evening`：保存した確定倍率を午前0時後の再起動でも保持し、期限外は流用しない。`test_evening_without_morning_is_unconfirmed_not_zero`：夜の欠測は倍率5・missing・null |
| AC-15/24 | `test_baselines_and_two_candles_roll_back_together`、`test_atomic_failure_when_beta_outbox_write_fails`：両リソース・基準値・OHLC・outboxの一括ROLLBACK |
| AC-15/23 | `test_failure_at_each_migration_phase_preserves_original_schema_and_data`：backup後・DDL・Beta追加・検査・版更新で例外を注入し版1全行保全。`test_kill_during_migration_recovers_v1_and_verified_backup`：COMMIT前の自分の子プロセスkillから版1・検証済みbackupを回復 |
| AC-04/15/24 | `test_ac04_kill_during_transaction_restores_all_committed_state`：実SQLite保存トランザクション中の強制終了・再開。`test_corruption_in_beta_history_and_inactive_run_is_rejected`：inactive runも検査 |
| NF-02 | 既存の4周期テストと`test_sleep_across_boundary_does_one_step_and_has_no_baseline`：保存遅延・スリープ後の追いつきなし |

固定時計試験でWindowsの実時計は変更していない。04/18時まで待った実時間試験・OS休止・停電・物理I/O故障の試験とは区別する。

### 失敗・制約と切替判断

初回のOS/プロセス読取り、運用DB読取り、ブランチ切替はサンドボックス権限で拒否された。承認後、同じ読取り・指定ブランチ切替・隔離試験を実行して成功した。初回DB読取りはファイル存在確認の段階で失敗し、運用DBへの接続や変更は発生していない。失敗試行の専用ディレクトリも上書きせず残した。試験コード不具合・移行不整合・Workerエラーは見つからず、実装修正は不要だった。

**Windowsのローカル移行・再開に関する阻害事項は今回の範囲で認められず、別途承認された本番切替の準備へ進める。** ただし互換API/Webの先行公開と旧版互換確認、運用停止/新規backup/移行後のAzure実通信・公開版2比較は未実施で、保存設計9.6の本番切替作業で必要。実スマートフォン・OSスリープ/停電/実I/O障害・費用実測も未検証。OHLCクラウド同期・日次退避・履歴UIを含むMVP全体の完成ではない。

本追記は作業ブランチだけにコミット・pushする。運用Worker操作・運用DB書込・本試験からのAzure送信・本番デプロイ・main/デプロイ元へのmergeは実行していない。


## 本番リリース（2026-10-23）

2026-10-23のユーザー指示により、既存デプロイ元への取り込み・リモート反映、Azure本番API/Web更新、運用Worker正常停止、運用DBの保全付き移行、新Worker常駐起動と実通信検証を実施した。前節までの「本番未実施」は当時の記録として残す。今回の範囲のリリースは**成功**。機能修正・要件変更・mainへのmerge・force pushなし。

### 対象・事前準備（AC-18/23/25）

- 公開URL：**https://polite-plant-0eb01a400.3.azurestaticapps.net**。
- 取り込み・実際のデプロイ・稼働WorkerコードのSHA：**`547adcbb565b5278fa23622b96c616e8098b4b34`**。その後の検証記録コミットはデプロイしていないため、公開Web/APIのbuild SHAはこの値のまま。
- 開始時：ローカル`codex/alpha-beta-resources`とリモートが確認済みSHAに一致、未コミット変更なし、後続コミットなし。デプロイ元`codex/stage1-local-worker`の旧先端は`576595fa8f67eec56a6ffd39ddcd51efafe524bb`。
- 旧公開SHAは`cc721d2b4b5573c92545dd1644d3feda1fad7328`。旧Workerの起動情報と同時点のコードを確認し、旧公開SHAから旧デプロイ元先端までWorker・起動補助・設定・uv.lockの差分なし。旧コードを`previous-worker-code.zip`へ保全。プロセスから実行中Pythonの全ソースを抽出したものではなく、既存リリース情報とGit差分に基づく旧コードの特定である。
- 稼働中の運用コードを変更しないため、管理worktree `C:\Users\LovelyQte\.codex\worktrees\alpha-beta-release\resource-simulator`でデプロイ元を`git merge --ff-only origin/codex/alpha-beta-resources`。GitHub接続のref更新（expected旧SHA・force=false）でfast-forwardをリモート反映し、`git fetch`と`git ls-remote`で一致確認。従来のWindows Git認証問題を避け接続済みGitHubを使用し、認証設定は変更していない。
- `.secrets`、固定`.venv/.tools`、既存ビルド依存はworktreeから既存パスを参照。Workerは従来の元リポジトリで稼働し、新旧いずれも同じ絶対DB・既存cloud設定を使用。運用DB：`C:\Users\LovelyQte\Documents\resource-simulator\.secrets\worker.sqlite3`（Win32_Process起動引数を再確認）。設定内容は変更なし、Beta初期値100の新設定は保存済み旧runのJSONを上書きしない。
- Windows 11 Pro 10.0.26200、uv 0.13.4、Worker CPython 3.13.16、API検証Python 3.11.17、Node 20.19.0、npm 11.4.1。システムPython・PATH・レジストリ変更なし。`uv sync --locked`成功、npm依存はpackage-lockの固定版を`npm ls --depth=0`で確認。
- 停止前にWorker **65件**、API **7件**、Web **9件**のテスト成功。`npm.cmd --prefix web run build`も成功。実装・固定依存の変更なし。
- 対象Azure：subscription `167018da-cd98-490d-9d4f-ecf252ff676a`、RG `rg-resource-simulator`、SWA `resource-simulator-web`（Free）。既存Storage `rsim16701820261022`のCurrentStateを既存証明書設定で使用。新規リソース・権限追加なし。SWA実プロパティのbranch/repositoryUrlはnullであり、公開元の制限は既存deploy.pyのガードとmanifestで管理している。別PJ資源を変更していない。
- 現行[Managed Functionsランタイム表](https://learn.microsoft.com/en-us/azure/static-web-apps/languages-runtimes)のPython 3.11と[Managed Functionsの制約](https://learn.microsoft.com/en-us/azure/static-web-apps/apis-functions)を再確認し、既存HTTP API構成を維持した。

### 互換API/Webの先行公開（AC-19/20/25/26）

隔離worktreeで`uv run --locked python scripts/deploy.py`を実行。ブランチ・クリーン状態・origin一致のガードを変更せず成功した。APIへBUILD_COMMITを設定し、既存固定Linux Python 3.11用wheelとWebをproductionへ公開。deployment.jsonの更新時刻は**2026-10-23T18:48:59.130440+09:00**。以後のDB操作は先行公開の確認成功後のみ実行した。

旧Worker PID 3084/1856とDB版1を維持した段階で、実Table版1→新API公開版2の同一run/stepを**98317、98319〜98323の6件**照合。Alpha値・parameter・増減・日時・モデルが一致し更新を継続。Betaはavailable=false、数値項目なしで架空の0を生成しない。APIは200・no-store・build SHA一致。公開JS/CSSのバイト列はworktreeのdistと一致した。

Playwright実ブラウザ（API差替えなし）で**1280/320/390px**を確認し、PCは同幅2列、モバイル幅は1列、Alpha赤系/Beta青系、Beta「未提供」、Alpha自動更新、横溢れなし、build表示を検証。1280/320pxスクリーンショットは目視確認も実施した。ここで旧Workerを停止するブラウザ試験は使用していない。

### 正常停止・新規バックアップ・版1→3（AC-04/14/15/23/24）

- 正常停止要求：**2026-10-23T18:56:19.462556+09:00**。`uv run --locked python scripts/worker_control.py stop`が成功し、**2026-10-23T18:56:19.978306+09:00**に旧2プロセスの終了とProcessLock取得によるロック解放を確認。強制終了なし。
- 停止直後の版1DBを読み取り専用接続し、SQLite Backup APIで新しい`stopped-production-v1.sqlite3`を取得。全runの設定hash・checkpoint・OHLC/outbox、integrity_check=ok、foreign_key_check=0件を検査。リハーサル時の古いバックアップは使用していない。
- 復旧基準のfile SHA-256：`32aa7abbb79cf2cecb6aa57a80a640dce4b1170b196736b6d573b235d3859e07`。バックアップはGit除外の`C:\Users\LovelyQte\Documents\resource-simulator\.cache\production-release-20261023-184726`に保全した。
- 停止時run：`ac23fe78-636c-40ba-ab6d-bef83f17c26f`、世代**1**、step **98497**、Alpha **755.7335644981893**、計算日時`2026-10-23T09:56:19.819881+00:00`。設定JSONは旧6キー、config_version=`c693897acb23dfd3ba0cd5bf3a5f1b2c02dfe1bae719bfeb6c062403a5895be6`、rules_hash=`4cbecdf143cc115abd8b652c773aa3bee68c7b6f56aed68a7b5e7ae9067e37a1`。
- 既存の明示CLI `uv run --locked python -m resource_simulator migrate --db <上記絶対DBパス> --config config/simulator.toml`が成功。**2026-10-23T18:56:20.632339+09:00**に版3の全run・物理/FK検査成功。

| 旧テーブル | 停止時・新規バックアップ | 移行直後 |
| --- | ---: | ---: |
| simulation_runs | 1 | 1 |
| active_run | 1 | 1 |
| checkpoints | 1 | 1 |
| open_candles | 1 | 1 |
| candles | 1642 | 1642 |
| outbox | 1642 | 1642 |

旧6テーブルの**全行・rowidを直接比較し完全一致**。論理fingerprint SHA-256も`30cac64ff3f25068744d809c12f36823d03bded0b2130a219368b39431471a5c`で一致。旧JSON文字列・設定hash・全OHLC・outboxの再試行情報を含む。Alphaのrun/世代/step/値/日時を保持し、Betaは**初期値100・開始step98497・同step/計算日時**で追加。追加前Beta確定足0・分内足0。new-run・初期化・世代再採番なし。

移行CLI自身の`worker.sqlite3.v1-backup-821ee70ee554477797af166230012474.sqlite3`とmanifestも運用DB横のGit除外領域に保全。manifestのverified=true、版1→3、件数・論理hash・file SHA-256を再照合した。今回は外側バックアップとCLIバックアップのfile hashも一致。旧バックアップを運用DBへ復元する操作は行っていない。今後版1へ戻すと移行後進捗を失うため、自動復元しない。

### 新Worker・Azure実通信・公開画面（AC-08/09/19/21/24/25/26、NF-02）

`uv run --locked python scripts/worker_control.py start`で従来のCREATE_NO_WINDOW常駐方式を使用。PID **8664**、実体PID **11272**、同じ運用DB絶対パスと既存`.secrets/cloud.toml`で起動。起動ログは**18:57:36.396 JST、同じrun・step98497**。二重Workerなし。最初のstep98498は18:57:37.935737 JSTに計算、Table公開18:57:38.335348 JSTをAPIでも取得した。

監視期間：**2026-10-23T18:57:35.990806+09:00〜2026-10-23T19:00:57.274881+09:00**。約200秒でstep98497〜98697の**201状態（200ステップ）**を欠落なく読み取り専用スナップショットとして保存。全200ステップで両checkpointのstep/計算日時一致、Beta要求量=同stepのAlpha実増減量×倍率、下限を含む値・実増減量の式、開始位置保持を検査した。停止時間を追いつかず98498から再開。

実Tableと公開APIはSQLiteの**保存した同じrun/世代/step**のスナップショットと、それぞれ**196件ずつ一致**。SQLite/Table/APIの3経路で共通する同一stepは**188件**。別時点の最新値同士を無理に一致させる比較はしていない。世代・step・提供済み公開版2の後退なし、APIエラー・ネットワーク監視エラー0、stale=false。`verify_azure.py --compare-only`は停止状態の単純逐次比較のため稼働中には使わず、同一stepを保持して照合した。

3経路共通サンプル：step **98696**、計算日時`2026-10-23T10:00:55.938520+00:00`、Alpha **838.4560163502888**、Beta **513.6122592604977**、Alpha実増減 **-0.145544405764781**、Beta要求/実増減 **-0.727722028823905**。夜間で同日基準値が揃わないため倍率**5**・**missing**（画面「基準値未確定」）、04/18時値はnullが正しい。境界値の補間は行っていない。

| 監視終了時 | Alpha | Beta |
| --- | ---: | ---: |
| step | 98697 | 98697 |
| サンプル合計 | 98697 | 200 (=98697−98497) |
| 確定OHLC件数 | 1646 | 3 |
| outbox件数 | 1646 | 3 |
| 分内サンプル数 | 57 | 57 |

18:58/18:59/19:00の分境界を通過し、両OHLC/outboxを保存。全run・物理/FK検査も成功。ローテーションのため単一ログのbyte offsetだけでは検査できず、後からworker.logと過去3世代を時刻で横断検査した。**2026-10-23 18:57:36,396〜2026-10-23 19:08:18,055 JST、2448件のログイベント、起動ログ保持、同期204応答あり、WARNING/ERROR/CRITICAL 0件**。ログ本体は非公開保全した。

新ペア公開後も実ブラウザ1280/320/390pxで、両数値・実増減量・倍率5・基準値未確定・未取得の表示と自動更新を確認。PC同幅2列・モバイル1列、赤/青の識別、横溢れなし、ページエラー0、build `547adcb`。監視後の画面でもstep98805→98812の進行を確認。1280/320px画像を目視確認済み。実スマートフォンは使用していない。

### コマンド・成果物・残事項

実行コマンドは上記の既存deploy.py・worker_control.py・migrateと、Git除外の`.cache/production_preflight.py`、`production_compatibility.py`、`production_migrate.py`、`production_monitor.py`、`production_logcheck.py`（すべて`uv run --locked python`）を使用。画面確認は`PLAYWRIGHT_BROWSERS_PATH=<PJ>/.cache/pw-browsers`で`node .cache/production-browser.mjs v1`および`v2`。DBパスを省略したrun/new-runは実行していない。

生データ・旧コード・設定hash・backup・manifest・ログ・全スナップショット・スクリーンショットは上記専用Git除外ディレクトリと運用DB横に保全。秘密値やDB/ログをGitへ含めず、文書だけを記録コミットで反映する。公開ビルドSHAを記録コミットへ書き換える再デプロイは行わない。

事前確認スクリプトではAzure CLI showの未対応引数を修正して再実行。移行ゲートでは日本語JSONの既定cp932読取りが失敗したためUTF-8へ修正。いずれもWorker停止・移行前の検証補助スクリプト内で発生し、本体コードや運用データの不具合ではない。実装修正不要。検証なしの再開・強制終了・バックアップ自動復元・Table Entity削除は行っていない。

**リリース成功、新Workerは常駐稼働を継続。** 実スマートフォン、04/18時の本番実時間通過、OSスリープ/停電/物理I/O故障、長期稼働・実料金は未検証。境界・ロールバック・強制終了は前節のWindows固定時計/一時DBテストと区別する。配送台帳・OHLCクラウド同期・日次Blob/退避・履歴UIは未実装のままで、MVP全体完成とは扱わない。
