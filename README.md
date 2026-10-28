# resource-simulator

数値で表されるリソース量が、一定の法則またはランダムな要因によって時間とともに増減し、その推移や相互作用から生まれる偶然の動きを眺められるWebベースのシミュレータ。

主な利用体験は数値とグラフの観察。勝敗、ランキング、最適化や頻繁な操作は主目的にしない。将来的には各リソースが他のリソースや複数のパラメータを参照し、異なる規則で増減する形へ拡張する。

## MVPの構成

自宅Windows PCのWorkerがAlpha・Betaの2リソース・1動的パラメータを毎秒計算し、SQLiteへ保存する。Azureへ最新状態と1分OHLCを同期し、PC・スマートフォンから閲覧する。

- Workerの起動・停止・設定変更はローカルで行う。
- PC停止中も同期済みの最新値と履歴を閲覧できる。
- 再起動時は最後の保存完了状態から再開する。
- 直近3か月の1分OHLCを閲覧し、古い履歴は圧縮退避して保持する。
- Webは最新状態を初回取得し、閲覧中は継続取得して自動反映する。

具体的な制約・初期案・対象外は要件定義書を参照する。

## Codexへの入口

1. [AGENTS.md](AGENTS.md)を読む。
2. [ドキュメント案内](docs/README.md)から[要件定義書0.8](docs/resource-simulator-requirements.md)と[基本設計書0.8](docs/resource-simulator-basic-design.md)を読む。既存要件・ACを維持し、履歴のAzure最終検証を追記した。
3. 基本設計書第13章の段階に沿って実装し、要件定義書第8章の受入条件で検証する。

要件定義書を優先し、初期案と確定要件を区別する。数値モデル・API等の詳細は各文書を正本とし、このREADMEに複製しない。

## 現在の状態

2026-10-28：**履歴版を本番公開済み**。[本番画面](https://polite-plant-0eb01a400.3.azurestaticapps.net)、公開API/Web/Worker SHA `36385dece313b50e87cba6b11a1d448ac23a84d8`。[切替記録](docs/history-production-release.md)にバックアップ・同一step照合・全5,985足照合・画面確認を記録。同じ版3DB/run/世代を維持し初回同期完了。非表示Worker常駐中、再起動時自動起動は未登録。履歴SASは2027-01-26 22:14 JST失効。日次Blob/圧縮退避/pruneは後続。以下の日付付き記録は過去の状態。

2026-10-26：履歴版を分離した[Azure検証環境](https://mango-wave-0fef6ca00.1.azurestaticapps.net)へ反映し、実通信・配送障害・3か月全量照合・旧→新→旧Workerのリハーサルを実施。[最終検証記録](docs/history-azure-validation.md)と[適用/復旧手順](docs/history-operations.md)を参照。毎分の全期間再取得を日別更新確認へ改め、配送記録のsource/内容照合と原子的通知を補強した。**本番は未切替**。最新は要件/基本設計0.8・保存設計0.5。

履歴版は`codex/ohlc-history`で実装・ローカル検証、**本番未適用**。保存済み確定OHLCの非公開Table配送/再送、`/api/ohlc`、JSTローソク足と当日/週/月/暦3か月を追加し、グラフ中心のカードへ変更した。DBは版3のままで移行不要。元OHLC/outboxは削除せず、配送確認用の補助SQLiteを別に作る。[履歴検証記録](docs/history-validation.md)と[適用・復旧手順](docs/history-operations.md)を参照。APIには履歴専用read SASが必要。日次Blob・圧縮退避・配送台帳/pruneは後続で、MVP全体の完成ではない。

以下は現在の本番Alpha/Beta版と従来手順の記録。新履歴版の公開記録とは区別する。最新仕様は要件/基本設計0.7、保存設計0.4。

2026-10-23にAlpha/Beta版を本番公開し、運用Workerを常駐稼働中。計算・原子的保存・最新値公開版2・レスポンシブカードをWindowsとAzure実通信で確認済み。Alphaは旧内部ID `resource` と値・履歴を引き継ぐ。Betaは同じstepのAlpha実増減量に連動する。時間帯の規則は[基本設計4.1章](docs/resource-simulator-basic-design.md)、移行と切替・復旧は[保存・移行設計9章](docs/resource-simulator-storage-design.md)、テスト結果は[Alpha/Beta検証記録](docs/alpha-beta-validation.md)。

**運用DBは保全付きで版1→3へ移行済み**。公開スキーマは独立した版2。公開URLは **https://polite-plant-0eb01a400.3.azurestaticapps.net**、デプロイSHAは`547adcbb565b5278fa23622b96c616e8098b4b34`（後続の記録コミットとは別）。同じrun/世代を保持して両値の本番更新を確認した。[本番切替記録](docs/alpha-beta-validation.md)を参照。配送台帳・OHLCクラウド同期・日次退避・履歴グラフは未実装。

採用初期案：Beta初期値100（`[model].initial_beta`で指定）、下限0、基準値不足の夜は倍率5と「基準値未確定」。連続観測の許容間隔2.5秒。確定したxは午前0時でも保持し翌04時に失効する。詳細な式を変更する場合は基本設計も更新する。

### 旧1リソースの公開記録（今回の2リソース版のデプロイ記録ではない）

最新値公開の実装・Azure実機確認済み。公開URLは **https://polite-plant-0eb01a400.3.azurestaticapps.net** 。デプロイ元は`codex/stage1-local-worker`、デプロイSHAは`cc721d2b4b5573c92545dd1644d3feda1fad7328`。PC Workerから非公開Azure Tableへコミット済み最新値を同期し、APIとReact Webで初回取得・約1秒更新・停滞表示を行う。結果は[最新値公開検証記録](docs/latest-values-validation.md)、構築・料金・認証は[Azure構築・運用](docs/azure-latest-values.md)。OHLCクラウド同期・日次化・退避・履歴グラフは後続。

### 最新値同期を使うWorkerの起動・停止

初期構築後は、固定環境で次を実行する。開発者のAzure CLIログインはWorkerの認証に使わない。

```powershell
uv sync --locked
uv run --locked python scripts/worker_control.py start
uv run --locked python scripts/worker_control.py status
uv run --locked python scripts/worker_control.py stop
```

この補助コマンドはWindows上でコンソールを開かず常駐し、ローカルの停止ファイルで正常停止する。PIDと停止ファイルは`.secrets`内。計算用SQLite・ログも同フォルダーに置き、既存の別DBは変更しない。証明書・設定の初期構築は`docs/azure-latest-values.md`を参照。PC再起動後の自動起動登録はまだ行わない。

端末で直接実行する場合（停止はCtrl+C）：

```powershell
uv run --locked python -m resource_simulator run --db .secrets/worker.sqlite3 --cloud-config .secrets/cloud.toml
```

旧版の同期導入時はDB版1のままだった。今回の新Workerを既存DBで使う前に、停止・バックアップ付き明示移行で版3へ変換する。通信失敗時も計算・保存を継続し、回復後は最新checkpointだけを送る。OHLCとoutboxは保持し、手動削除しない。現在の公開Webは閲覧専用で、履歴グラフは未実装。

### 段階1時点の記録（以下は過去の状態）

2026-10-22：基本設計第13章の段階1を実装。計算Engine、TOML設定検証、SQLiteへの原子的保存、単一起動、保存済み状態からの再開、1分OHLCと分内状態の復元をWindowsで検証済み。確定足と未送信記録も同じトランザクションで保持する。段階1時点ではAzureへの送信は行わない。

段階1はローカルWorkerの完成範囲であり、MVP全体の完成ではない。Azureリソース作成・同期・Web・API・公開・料金実測は未実施。

`cf351ea`を基準とするレビュー修正では、通常の予定期限を1秒ずつ進めるよう計算周期を修正した。保存が0.2秒でも通常の開始は1・2・3秒となり、保存時間が毎周期に加算されない。次期限に間に合わない場合は、保存完了から1秒後へ期限を立て直して高速な追いつき計算を防ぐ。Windows・固定Pythonで既存29件＋追加3件の計32テストが成功している。

段階2の配送台帳・退避後の再開・版1→2移行方法は[保存・移行設計0.1](docs/resource-simulator-storage-design.md)に具体化した。**保存設計だけを追加した当時はコードとDBスキーマは版1のままだった**。移行・送信・削除はまだ実装しておらず、既存DBのoutboxやOHLCを手動で削除してはいけない。配送台帳は未実装。現行版3からの将来の別移行で、ackedのoutbox削除、確認済み退避後の本体削除、欠落検出を同時に成立させる。

## 既存DBの移行と公開切替（2026-10-23に運用適用済み）

[保存・移行設計9章](docs/resource-simulator-storage-design.md)の保全・復旧・切替順序を読む。先に互換API/Webをデプロイして旧データのAlphaとBeta未提供表示を確認し、その後にWorkerを停止して移行する。運用反映が明示された場合だけ、対象の絶対DBパスを確認して以下を実行する。

```powershell
# Workerと全DB利用プロセスを停止後。通常runは版1を暗黙変換しない。
uv run --locked python -m resource_simulator migrate --db <絶対DBパス> --config config/simulator.toml
uv run --locked python -m resource_simulator status --db <同じ絶対DBパス>
```

移行コマンドが出力する検証済みbackupとmanifestを保持する。元の全Alpha行・rowid/世代・設定hash・outbox再試行情報を照合し、Betaだけを現在stepから追加する。Betaの追加前履歴は作らない。移行済みDBへの再実行は検査だけを行う。失敗時にDBを消さず、元DB/WAL/SHMを保全する。進捗のある版3を古いバックアップへ自動で戻さない。

## セットアップ（Windows 11・PowerShell）

Gitとuvをインストール済みのPCで、リポジトリのルートを作業フォルダーにする。確認済みのuvは0.13.4。Pythonは3.13系の最新パッチ候補を確認して**3.13.16**へ固定した（[公式一覧](https://www.python.org/downloads/)、[リリース](https://www.python.org/downloads/release/python-31316/)）。Python 3.10は導入しない。

初回は次を実行する。Pythonのダウンロード時だけネット接続が必要。

```powershell
uv python install 3.13.16 --install-dir .tools/python --no-bin --no-registry
uv venv --python .tools/python/cpython-3.13.16-windows-x86_64-none/python.exe
uv sync --locked
uv run --locked python --version
```

各コマンドが成功してから次へ進む。最後は`Python 3.13.16`と表示される。`.ps1`スクリプトを使わないので、PowerShellの実行ポリシーの変更は不要。以後の環境同期は`uv sync --locked`（`uv venv`で環境を再作成する必要はない）。

Python本体は`.tools/python`、専用仮想環境は`.venv`、uvキャッシュは`.cache/uv`へ置く。PATH・レジストリ・システム既定のPython・uvのグローバル設定は変更しない。`.python-version`と`pyproject.toml`でPythonのパッチ版を固定し、依存解決結果を`uv.lock`へ記録している。自動のPythonダウンロードは無効化してあり、上記の明示的な導入だけを行う。仮想環境をactivateする必要はない。

計算・保存・OHLCはPython標準ライブラリを使い、`tomllib`・`sqlite3`・Windowsの`msvcrt`等の互換性を確認済み。現在は最新値同期のAzure SDKも追加し、uv.lockで固定している。クラウドAPIのPython 3.11と依存は別に検証・固定する。

VSCodeではこのフォルダーを開き、Pythonインタープリターとして`.venv\Scripts\python.exe`を選択する。現在のPCの`code`コマンドはCursorを指すため、VSCodeのアプリから開くか、次を使う。

```powershell
& "$env:LOCALAPPDATA\Programs\Microsoft VS Code\bin\code.cmd" .
```

## 実行と状態確認

設定は`config/simulator.toml`。初期値・乱数範囲・係数・周期は[基本設計第4章](docs/resource-simulator-basic-design.md)を正本とする。未知・欠落キー、文字列・真偽値の数値指定、NaN・Infinity、範囲違反、1秒以外の周期はエラーになる。

```powershell
# 5ステップ実行して正常終了（初回計算まで約1秒待つ）
uv run --locked python -m resource_simulator run --steps 5

# 読み取り専用で最新のコミット済み状態・分内OHLC・未送信件数を表示
uv run --locked python -m resource_simulator status

# 同じ保存状態から常駐実行。停止はCtrl+C
uv run --locked python -m resource_simulator run
```

DBの既定保存先は`%LOCALAPPDATA%\resource-simulator\worker.sqlite3`。ログは同じフォルダーの`worker.log`で、1,000,000 bytes×現行1本＋過去3本を上限にローテーションする。毎秒の値はログへ出さない。DB名の末尾に`.lock`を付けたファイルはOSロック用で、終了後も残ってよい。稼働中のロックファイルは削除しない。

同じDBへの二重起動は終了コード1で拒否する。正常終了は0、設定・保存・復元・時計異常等は1。Windowsの強制終了でも、OSがロックを解放し、次回起動で最後の保存完了状態から再開する。保存失敗や異常値では、そのステップを確定せず停止する。

別の保存先を使う場合は各コマンドに`--db`を付ける。同じシミュレーションには必ず同じ絶対DBパスを使い、ローカルディスクへ置く。ネットワーク共有・OneDrive等の同期フォルダー上での運用は検証していない。

```powershell
uv run --locked python -m resource_simulator run --db C:\simulator-data\worker.sqlite3 --config .\config\simulator.toml
uv run --locked python -m resource_simulator status --db C:\simulator-data\worker.sqlite3
```

内部日時・OHLCの分境界はUTC。日本時間の日付境界はUTC+9として扱う。step=0はOHLCサンプルに数えず、正常停止時の分内状態は未確定のまま保存する。同じ分に再開すれば継続し、後の分に再開すれば旧分を確定する。停止だけの分は作らない。PC停止中の時間を追いつき計算しない。

## 再開と設定変更

通常起動は保存済み状態・保存済み設定を優先する。設定ファイルの初期値だけを変更しても、保存済みのR・P・stepは初期化しない。係数・生成範囲・周期が保存した計算規則と異なる場合は停止する。その際は、保存した規則での継続か、新しい実行の作成を明示する。

```powershell
# 設定ファイルを検証した上で、保存した計算規則を使って継続
uv run --locked python -m resource_simulator run --use-saved-rules

# 設定ファイルの初期値・規則で別のrun_idを作成し、step=0を保存して終了
uv run --locked python -m resource_simulator new-run

# 作成した新しい実行を開始
uv run --locked python -m resource_simulator run
```

`new-run`は旧実行を上書き・削除せず、旧実行の未確定OHLCを確定し、未送信記録も残す。旧実行と新実行はrun_idで区別する。実行中のWorkerを停止してから使用する。段階1ではクラウド送信・日次化・退避が未実装なので、確定OHLCとoutboxは蓄積し続け、自動削除しない。常用前の容量監視・保持期間の運用は段階2以降で整備する。

復元に失敗したDBを削除して起動し直さず、まずログ・DBのバックアップを確保して原因を確認する。未対応スキーマや整合しない状態を設定の初期値で上書きする処理はない。

## バックアップ

SQLiteのWALを使うため、稼働中のDBファイル1個だけをコピーしない。次は標準Backup APIで整合したローカルバックアップを作る例。運用DBの保存先を変えた場合は`$dbPath`を合わせる。

```powershell
$dbPath = Join-Path $env:LOCALAPPDATA 'resource-simulator\worker.sqlite3'
$backupPath = Join-Path $env:LOCALAPPDATA ('resource-simulator\backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.sqlite3')
uv run --locked python -c "import pathlib,sqlite3,sys; source=sqlite3.connect(pathlib.Path(sys.argv[1]).resolve().as_uri()+'?mode=ro',uri=True); target=sqlite3.connect(sys.argv[2]); source.backup(target); target.close(); source.close()" $dbPath $backupPath
```

バックアップから戻す場合はWorkerと状態確認プロセスを停止し、元のDB・WAL・SHMを一式別ディレクトリへ保全してから、元DBパスへバックアップを配置する。古いWAL・SHMを復元DBに混在させない。PCディスク故障時の無損失復旧・バックアップ復元の実機試験は未検証。

## テストと検証範囲

最新の2リソース検証（Azureを使わず一時DBで実施）：

```powershell
uv run --locked python -m unittest discover -s tests -v
api/.venv/Scripts/python.exe -m unittest discover -s api/tests -v
npm.cmd --prefix web ci
npm.cmd --prefix web test
npm.cmd --prefix web run build
node web/tests/local-browser.mjs
git diff --check
```

local-browser.mjsはビルド済みWebをローカルで検証し、運用Workerを起動停止しない。PlaywrightのChromiumが必要。Linuxでは`CHROMIUM_PATH=/usr/bin/chromium node web/tests/local-browser.mjs`も使える。APIの固定環境の準備は[構築記録](docs/azure-latest-values.md)。旧`web/tests/browser.mjs`は運用Workerの停止・起動を伴うため、今回のローカル検証では使用しない。

以下は段階1からの検証手順・説明：

```powershell
uv run --locked python -m unittest discover -s tests -v
git diff --check
```

テストは`.cache/tests`内に一時DBを作り、終了時に削除する。既定の運用DBは使用しない。子プロセスを強制終了する試験も含む。固定入力での計算、下限制約、実トランザクション中の強制終了・復元、設定変更、保存失敗、分・日本時間の日付境界、二重起動を検証する。

周期の固定時計テストだけを実行する場合：

```powershell
uv run --locked python -m unittest tests.test_stage1.PersistenceTests.test_worker_storage_time_does_not_shift_normal_deadlines tests.test_stage1.PersistenceTests.test_worker_rebases_when_storage_reaches_or_exceeds_next_deadline tests.test_stage1.PersistenceTests.test_worker_sleep_with_storage_delay_does_not_catch_up tests.test_stage1.PersistenceTests.test_worker_waits_first_second_and_does_not_catch_up -v
```

0.2秒の保存遅延、1秒ちょうど／1.2秒の期限超過、3600秒のスリープを実SQLite保存と固定時計で検証する。実機で1時間待った試験ではなく、スケジューラの挙動を再現する試験である。

AC-IDごとの結果と未検証事項は[段階1検証記録](docs/stage1-validation.md)を参照。AC-16は単一起動とローカルの古い状態の上書き防止のみを検証し、クラウドの遅延応答は段階2以降で扱う。
