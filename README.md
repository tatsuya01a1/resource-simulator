# resource-simulator

数値で表されるリソース量が、一定の法則またはランダムな要因によって時間とともに増減し、その推移や相互作用から生まれる偶然の動きを眺められるWebベースのシミュレータ。

主な利用体験は数値とグラフの観察。勝敗、ランキング、最適化や頻繁な操作は主目的にしない。将来的には各リソースが他のリソースや複数のパラメータを参照し、異なる規則で増減する形へ拡張する。

## MVPの構成

自宅Windows PCのWorkerが1リソース・1動的パラメータを毎秒計算し、SQLiteへ保存する。Azureへ最新状態と1分OHLCを同期し、PC・スマートフォンから閲覧する。

- Workerの起動・停止・設定変更はローカルで行う。
- PC停止中も同期済みの最新値と履歴を閲覧できる。
- 再起動時は最後の保存完了状態から再開する。
- 直近3か月の1分OHLCを閲覧し、古い履歴は圧縮退避して保持する。
- Webは最新状態を初回取得し、閲覧中は継続取得して自動反映する。

具体的な制約・初期案・対象外は要件定義書を参照する。

## Codexへの入口

1. [AGENTS.md](AGENTS.md)を読む。
2. [ドキュメント案内](docs/README.md)から[要件定義書0.4](docs/resource-simulator-requirements.md)と[基本設計書0.4](docs/resource-simulator-basic-design.md)を読む。要件0.3の機能・ACは維持し、要件0.4ではユーザー指定のWorker環境だけを追記した。
3. 基本設計書第13章の段階に沿って実装し、要件定義書第8章の受入条件で検証する。

要件定義書を優先し、初期案と確定要件を区別する。数値モデル・API等の詳細は各文書を正本とし、このREADMEに複製しない。

## 現在の状態

2026-10-22：基本設計第13章の段階1を実装。計算Engine、TOML設定検証、SQLiteへの原子的保存、単一起動、保存済み状態からの再開、1分OHLCと分内状態の復元をWindowsで検証済み。確定足と未送信記録も同じトランザクションで保持する。Azureへの送信はまだ行わない。

段階1はローカルWorkerの完成範囲であり、MVP全体の完成ではない。Azureリソース作成・同期・Web・API・公開・料金実測は未実施。

`cf351ea`を基準とするレビュー修正では、通常の予定期限を1秒ずつ進めるよう計算周期を修正した。保存が0.2秒でも通常の開始は1・2・3秒となり、保存時間が毎周期に加算されない。次期限に間に合わない場合は、保存完了から1秒後へ期限を立て直して高速な追いつき計算を防ぐ。Windows・固定Pythonで既存29件＋追加3件の計32テストが成功している。

段階2の配送台帳・退避後の再開・版1→2移行方法は[保存・移行設計0.1](docs/resource-simulator-storage-design.md)に具体化した。**保存設計だけを追加した段階で、現在のコードとDBスキーマは版1のまま**。移行・送信・削除はまだ実装しておらず、既存DBのoutboxやOHLCを手動で削除してはいけない。今後はackedのoutbox削除、確認済み退避後の本体削除、欠落検出を同時に成立させる版2を実装・検証する。

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

段階1の実行・テストはPython標準ライブラリのみで、外部依存は0件。`tomllib`・`sqlite3`・Windowsの`msvcrt`等の互換性は実際のテストで確認した。将来のAzure SDKとクラウドAPIのランタイムはそれぞれの実装段階で検証・固定する。

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
