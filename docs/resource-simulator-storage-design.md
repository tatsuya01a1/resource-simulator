# 段階2のローカル保存・移行設計

- 文書版：0.4／更新日：2026-10-23
- 基準コミット：`cf351ea0808ac9c39b05ce83dd1507a845e455c7`
- 上位文書：[要件定義書0.6](resource-simulator-requirements.md)、[基本設計書0.6](resource-simulator-basic-design.md)第5〜8章
- 対応：D-01〜02、D-04〜06、D-09、F-04、AC-04、07、09〜10、12、14〜16
- 状態：**現行実装はAlpha/BetaのDB版3。版1→3の保全付き移行を第9章に記載。第1〜8章は配送台帳の旧版2案（未実装・適用禁止）として残す。OHLC送信・削除・日次退避は未実装。**
- 改訂0.2：Beta追加とリソース別開始step、旧設定hash保持、原子的保存、明示移行、公開切替/復旧を追加。旧版2案の実装完了を意味しない。将来の配送台帳は版3からの新しいDB版・移行設計を必要とする。

**改訂0.4**：今回のOHLC配送/履歴表示は下記10章。上記のOHLC送信未実装は0.3時点の記録。運用DBは版3を維持し、旧版2案の台帳・削除・日次退避は今回も実装しない。本番適用は別途指示後。

## 1. 保存するものと責務

スキーマ版1は全確定足にoutboxを要求し、保存中の全OHLCのsample_count合計とstepを比較している。この検査は、送信も履歴削除もしない段階1でのみ成立する。版2では、OHLC本体・配送台帳・未送信キュー・集計進捗・退避の確認記録を分離する。

OHLC本体を削除しても、分ごとの小さい台帳と実行ごとの累積値、最後の確定位置を残す。台帳の無条件な削除・再集計による不足分の帳尻合わせは禁止する。送信済みかどうかをoutboxの「不在」から推測せず、履歴の削除済み状態も明示する。SQLiteのコミット済み記録だけを正常状態の証拠として使う。

台帳は1秒値の履歴ではない。分ごとの識別・サンプル範囲・hash・配送／削除状態だけを保持する。段階2では台帳・退避確認記録を削除しない。長期間の台帳圧縮は別設計とし、台帳の容量・起動検証時間は実装時に測定する。

## 2. スキーマ版2の追加・変更

旧案では`PRAGMA user_version=2`を予定していた。現行では2を飛ばして3を使い、この旧案を直接適用しない。既存の実行・checkpoint・分内状態とrun_idは維持する。MVPではDBごとに1シミュレーション、当時のresource_idは`resource`固定だった。現行は`resource`と`beta`。

| テーブル | キー・主な列（型） | 参照・制約 |
| --- | --- | --- |
| `candle_ledger`（追加） | PK=`(run_id, resource_id, minute_start)`。`first_step, last_step, sample_count` INTEGER、`payload_hash` TEXT、`canonicalizer_version` INTEGER、`delivery_state` TEXT、`acked_at, remote_receipt` TEXT NULL、`payload_state` TEXT、`archive_manifest_id` TEXT NULL | run_id→simulation_runs。sample_count>0、first_step>0、last_step-first_step+1=sample_count。delivery_stateはpending/acked、payload_stateはretained/pruned。ackedには確認日時・受領情報が必要。prunedにはackedとverifiedの退避manifest参照が必要 |
| `candles`（変更） | PKは同じ。既存`candle_json`を保持 | 台帳への複合FK（削除カスケードなし）。retainedなら必ず1行、prunedなら0行。JSONのキー・count・hashは台帳と一致 |
| `outbox`（変更） | PKは同じ。既存のkind、attempts、next_attempt_atを維持し、`payload_hash` TEXTを追加 | FK先をcandlesから台帳へ変更（削除カスケードなし）。pendingなら必ず1行、ackedなら0行。hashは台帳・本体と一致 |
| `run_progress`（追加） | PK=`(run_id, resource_id)`。`finalized_samples_total, pruned_samples_total` INTEGER NOT NULL。`last_finalized_minute, last_finalized_at` TEXT NULL、`last_finalized_step` INTEGER NULL、`last_finalized_close` REAL NULL | run_id→simulation_runs。0≤pruned≤finalized。確定足0件なら最後の確定位置は全てNULL。最後の確定位置はOHLC本体の削除後も保持 |
| `daily_files`（追加） | PK=`manifest_id` TEXT、UNIQUE=`(run_id, resource_id, jst_date)`。対象UTC範囲、`row_count, sample_count, first_step, last_step`、正規化した全行のhash・圧縮ファイルhash、`history_object, archive_object`、各受領情報・照合日時、`history_state, archive_state`（pending/verified） | manifestは対象日の観測キー・各行hash・step範囲・countを含む。verified化後の内容・対象・hashは不変。再試行は同じmanifestと内容を使う。台帳のarchive_manifest_idがこのPKを参照 |

時刻はUTC固定桁文字列、日本時間の日付は`YYYY-MM-DD`。キー・hashはNOT NULL（表中NULL明記の列を除く）。整数はSQLiteの符号付き64bit内、数値は有限値、状態列はCHECK制約、参照はforeign_keys=ONで検査する。checkpoint・分内JSONの数値検証も継続する。テーブル間の条件はトランザクション完了前のアプリ検査と起動検査で強制し、中間状態を外へ公開しない。

daily_filesの受領情報・照合日時はpendingの間はNULL、verifiedには必須。0行の日はrow_count=sample_count=0、first_step/last_stepはNULLで、台帳への架空の行を作らない。1行以上ならstep範囲とcountを実データから埋める。historyとarchiveの確認状態・格納先・受領情報だけを順次更新し、確定したmanifestの内容を後から書き換えない。

hashはSHA-256、正規化版1はUTF-8のJSON（キー順固定、空白なし、NaN禁止、UTC日時は小数6桁、数値はPythonの往復可能な表現）とする。対象はrun/resource/分、OHLC、count、最初／最後の観測日時、first_step/last_step。正規化版を保存し、将来の実装変更で旧hashを再定義しない。remote_receiptは対象キー・ETag等の確認情報であり、SAS・キー・秘密情報は含めない。

## 3. 正常状態と欠落検出

| 台帳の配送状態 | 本体状態 | candles | outbox | 追加の必要条件 |
| --- | --- | --- | --- | --- |
| pending | retained | 1行 | 1行 | 本体・台帳・outboxのhash一致 |
| acked | retained | 1行 | 0行 | 送信確認日時・受領情報あり、本体hash一致 |
| acked | pruned | 0行 | 0行 | 送信確認とverifiedの退避manifestあり、manifest内のキー・hash・count・step範囲が台帳と一致 |

これ以外は異常。pendingなのにoutboxがない場合は送信済みとみなさず停止する。台帳なしの本体／outbox、retainedなのに本体なし、ackedなのにoutboxあり、prunedなのに確認記録なしも停止する。outboxを自動再作成したり欠落台帳を残存データから自動補完したりしない。診断・保全後に明示的な復旧を行う。

台帳は保持しているOHLC全件と既に削除したOHLC全件の存在証拠になる。台帳自体の欠落は次節の累積値・連続したstep範囲との照合で検出する。悪意ある同時改ざんまで防ぐ認証機構ではなく、保存・実装の不整合を検出するための設計である。

## 4. 状態遷移とトランザクション

1. **ステップ確定**：checkpointと分内集計を同一トランザクションで更新。旧分が確定する場合はcandles・pending/retained台帳・outboxを作り、finalized_samples_totalと最後の確定位置を同時更新する。分内countを確定済み側へ移すだけで、同じサンプルを二重計上しない。起動時の旧分確定・new-runによる旧分確定にも同じ処理を使う。
2. **送信**：pendingの不変なキー・hash・本体を読む。SQLiteトランザクション外で通信し、同じキー・内容で再送する。同期成功応答で確認できた場合、またはタイムアウト後の読戻しで内容hashが一致した場合だけ、短い書込トランザクションで台帳をackedへ変更し、受領情報を保存してoutboxを削除する。UPDATEは期待するpending・hashを条件とする。通信中もoutboxは消さない。送信順だけを根拠に「ここまで全件送信済み」とはしない。
3. **送信の中断**：クラウド反映後・ローカル確認前に終了した場合はpendingのまま再送する。確認のローカルトランザクション中の終了なら、acked＋outboxなし、またはpending＋outboxありのどちらかに戻る。古い応答はキー・hash・期待状態を照合し、別の内容や新runへ適用しない。確定足の内容を後から更新しない。
4. **日次化と退避の確認**：閉じた日本時間の日について、台帳と本体からmanifest・gzipを作る。通常保存先で保存・読戻し・展開・全行照合が成功してhistory_state=verifiedになり、暦上3か月の境界より全足が古くなったら退避先でも同じ照合を行う。両方の確認、対象キー集合・各hash・件数・sample総数が一致した場合のみarchive_state=verifiedを記録する。処理中は本体を保持する。初期退避先は即読出し可能なHot層とし、Archive層は別途評価する。
5. **ローカル本体の削除**：全サンプルが保存境界より古い日が対象。最低暦上3か月保持、全対象行がacked、outboxなし、分内集計との重複なし、historyとarchiveのverified証拠を再確認する。1日・1run・1resource単位のトランザクションで、各台帳のmanifest参照とpayload_state=prunedを設定し、本体を削除し、pruned_samples_totalを削除対象count分だけ増やす。finalized_samples_total・step・最後の確定位置は変えない。繰返し時はretained行だけを条件にし、pruned行を二重加算しない。

履歴の日付が閉じる前にはファイルを確定しない。再起動後も過去へ追記しない時計検査を維持する。欠測の日にはOHLCを捏造せず、必要なら0行manifestだけを残す。通常領域のクラウドデータ削除についても基本設計第6章の確認・猶予を守り、ローカル削除とは独立に進捗を管理する。通信・圧縮・hash計算・読戻しを長いDB書込トランザクションへ入れない。

## 5. 履歴削除後にも使える起動検査

同じ読取スナップショットで、全run/resourceについて次を検査する。計算を再開するまでに、pending・本体・台帳の欠落と進捗の矛盾を検出する。クラウドへのアクセスを起動条件にせず、確認済みのローカル証拠を使う。

```text
L = 台帳全行のsample_count合計
P = payload_state=prunedの台帳のsample_count合計
R = 残存するcandlesのsample_count合計
O = open_candlesのsample_count（なければ0）

L == run_progress.finalized_samples_total
P == run_progress.pruned_samples_total
R + P == L
L + O == checkpoint.step - resource_starts.start_step
```

台帳を分順に並べ、first_step=start_step+1からlast_stepまで重複・隙間がなく、各範囲の長さ=countであることを検査する。分の実時間には停止の空白を許すが、観測stepの範囲には隙間を許さない。分内状態があれば末尾の`checkpoint.step-O+1..checkpoint.step`に対応し、確定足より後の分であること、close・最終観測日時がcheckpointと一致することを確認する。

分内状態がなければ、step>0ではrun_progressのlast_finalized_step/close/atがcheckpointと一致することを確認する。最後の台帳の分・last_stepも最後の確定位置と一致させる。step=0なら確定・削除累積値は0、台帳・分内状態なし、初期状態の検証を行う。この方式では最後のOHLC本体も退避後に削除できる。

上記に加えて第3節の配送・本体状態とhashを全件検査する。retainedの本体欠落や台帳の欠落を、prunedの正常削除と混同しない。過去のrunも対象にし、旧runの未送信記録も見落とさない。保持期間の経過だけでpendingを消したり、累積値を減らしたりしない。

## 6. スキーマ版1から2への移行

移行は段階2の実装に含める。今回の修正で版1DBに移行SQLを適用しない。将来の移行入口はWorkerの通常再開より前とし、明示的なローカル操作で実施する。ネットワーク通信は行わない。旧バイナリは版2を認識せず停止することを維持し、版2を版1へ戻す自動変換は用意しない。

### 6.1 書換え前の保全

1. Worker・同期・削除・状態確認など全DB利用プロセスを停止し、既存と同じDBパスのOSロックを取得する。移行完了か失敗の確定まで計算を再開しない。
2. 版・必要テーブル・integrity_check・foreign_key_checkを確認し、active_run、設定hash、全runのcheckpointと分内状態を検証する。版1では全OHLCが保持され全件pendingであるため、全runでcount総数=step、全確定足にoutboxがちょうど1行、孤立レコードなしを検証する。欠落があれば送信済み／退避済みを推測せず、元DBを変更せず失敗する。
3. SQLite Backup APIで未使用の一意名へバックアップを作る。WAL使用中の主DBだけをファイルコピーしない。バックアップ先は単体で検査できるDELETEジャーナルへ切替えて閉じ、読取り専用で開き直す。物理整合性・外部キー・版1の全論理検査と、全テーブルのキー順の行数・正規化内容hash（checkpoint、分内、設定、outboxの再試行情報を含む）が移行元と一致することを確認する。
4. バックアップと検証manifest（元の版、対象run、step、各行数・hash、実行日時）を永続化し、バックアップファイルhashも記録する。検証完了記録を最後に保存する。作成・照合・保存が1つでも失敗したら移行しない。バックアップ、DB、manifestはGitへ入れない。

### 6.2 1トランザクションでの変換

バックアップ照合完了後、元DB上の`BEGIN IMMEDIATE`で以下を行う。明示COMMITを使い、途中で暗黙コミットする処理を入れない。foreign_keys=ONを維持する。

1. `daily_files`・`candle_ledger`・`run_progress`と、台帳を参照する`candles_v2`・`outbox_v2`を作る。実行・checkpoint・分内テーブルは再作成しない。
2. **全run**の確定足をUTC分順に走査し、先頭からcountを累積してfirst_step/last_stepを割り当てる。既存count・値・観測日時・run_id・分キーを変更せず、版2の正規化hashを算出する。全足をpending/retainedとして台帳とcandles_v2へ移し、元outboxのattempts・next_attempt_atを保持してoutbox_v2へ移す。送信済みの証拠がないためackedを作らない。クラウドへ既に存在しても段階2で同じキーによる安全な再送を行う。
3. finalized_samples_totalは確定足count合計、pruned_samples_totalは0。最後の確定分・step・close・日時を残す。分内countと合わせてcheckpoint.stepに一致することを確認する。確定足0件やstep=0のrunもrun_progressを作る。日次・退避のverified記録は新造しない。
4. 元と移行先の全キー・本体・count・outbox再試行情報を比較し、第3〜5節の検査を行う。比較成功後、旧outbox→旧candlesの順にDROPし、candles_v2→candles、outbox_v2→outboxの順にRENAMEする。新しいFKは台帳を参照するので、旧親への参照を残さない。新しい制約・必要インデックスも同じトランザクションで作る。
5. 物理整合性・外部キー・全論理検査を再度行い、最後に`PRAGMA user_version=2`を設定してCOMMITする。バージョンだけを先に書き換えない。

### 6.3 失敗・強制終了・再実行

| 失敗時点 | 保全・再開方針 |
| --- | --- |
| バックアップ作成／照合前 | 元DBに移行書込なし。未検証バックアップを復旧の正本にしない。原因解決後に最初から実施 |
| 変換トランザクション中 | 例外時はROLLBACK。強制終了時はSQLiteの回復後、版1と元の論理hash・件数を照合して再試行。空DBへの初期化は禁止 |
| COMMITの応答が不明 | 自動再変換しない。全接続を閉じ、開き直して版・テーブル構成・全論理検査を行う。完全な版1なら再実行、完全な版2なら変換済みと判断。不一致は停止して保全 |
| COMMIT後・Worker再開前 | 別接続で版2の全検査とrun_id・checkpoint・分内状態の移行前後一致を確認。成功まで計算・送信・削除を開始しない。再起動で版2なら検証だけを行い、二重計上しない |
| 実I/O障害などでDBを検査できない | 自動上書きしない。停止・全接続閉鎖後に主DB・WAL・SHMを一式別の保全先へ残す。検証済みバックアップを別の新規DBへ復元して照合し、明示的に切り替える。古いWALを復元DBへ混在させない |

バックアップは移行成功後も保持する。版2で計算・送信・削除を開始した後に版1バックアップへ戻すと、その後の進捗が失われるため自動ロールバックしない。復旧対象の選択とクラウド再照合は明示操作とする。電源断・ファイルシステム障害時の完全な耐久性はWindows上の障害試験で評価する。

## 7. 段階2実装前に必要なテスト

以下は**今回の合格テストではなく、今後の受入試験**。機能実装と合わせて検証記録を追加する。

| ケース | 期待結果・対応AC |
| --- | --- |
| pendingのoutboxだけ／台帳だけ／本体だけを欠落させる | 全runで異常を検出し、送信済みと推測して再開しない（AC-04、15） |
| 送信確認とoutbox削除の各段階でkill | pending＋outbox、またはacked＋outboxなしへ復元。前者は同じキーで再送（AC-07、10、16） |
| ackedのoutbox削除後に再開 | 正常復元し、不要なoutboxを再作成しない（AC-04、10） |
| 退避読戻し／hash照合失敗、通常履歴だけ保存済み | verifiedを記録せず本体・未送信を保持（AC-12） |
| prune中のkill、同じ日の削除を再実行 | 削除前か削除後に原子的に復元し、pruned累積値を二重加算しない（AC-04、12） |
| 部分削除／全確定足削除、分内あり／なし、旧runあり | 保持・削除累積値とcheckpointを照合し正常再開。最後の本体がなくても復元（AC-04、09、14） |
| サンプル数・step範囲・hash・manifest参照を壊す | 正常な退避削除と区別して停止（AC-15） |
| 版1の空run・複数run・分内状態・日跨ぎ・再試行中outboxを移行 | run_id・全値・count・再試行情報が一致。全足pending、pruned=0（AC-04、09、14） |
| 版1でoutbox／OHLC欠落、バックアップ不能、容量不足 | 変換を開始せず元データ維持。成功記録なし（AC-15） |
| 移行DDL・コピー・検査・version設定・COMMIT前後でkill／保存失敗 | 版1全体か版2全体に復元。検証済みバックアップから独立復旧可能。再実行で重複なし（AC-04、15） |

## 8. 根拠資料

確認日：2026-10-22。設計の根拠を確認したもので、移行の実装・実機検証を示すものではない。

- [SQLite Transactions](https://www.sqlite.org/lang_transaction.html)：明示トランザクションでDDL・データ・版更新をまとめる。
- [SQLite Backup API](https://www.sqlite.org/backup.html)：整合したバックアップを別DBへ作る。
- [SQLite WAL](https://www.sqlite.org/wal.html)：主DBとWALの関係、同一ホストでの運用。

## 9. 現行：Alpha/Betaの版1→3移行と保存（2026-10-23運用適用済み）

改訂0.3：明示承認により運用DBを移行し、新Workerの本番常駐と実通信照合を確認した。方式の変更なし。旧全行保持・新規backup・同一step比較の実測は[本番切替記録](alpha-beta-validation.md)を参照。第1〜8章の配送台帳案は未実装のまま。

### 9.1 版と識別の分離

| 版 | 状態 |
| --- | --- |
| DB版1 | 実装元・運用中の旧Worker。Alpha相当のみ |
| DB版2 | 第1〜8章の旧配送台帳案。未実装。版3が含む機能ではない |
| DB版3 | 今回実装。Beta状態・分内OHLC・リソース開始位置を追加。台帳/削除機能なし |
| 公開版1/2 | Table payload/APIの版。SQLiteの版番号とは独立 |

Alphaの内部IDは`resource`のまま、表示名だけAlpha。既存candles/outboxの外部キーを含め単純置換しない。simulation_runs.rowid（run_generation）、run_id、step、値、設定JSON・hash、全OHLC、outboxのattempts/next_attempt_atを保持する。

### 9.2 追加データと不変条件

旧6テーブルの構造を変更せず、次を追加する。

- `resource_starts(run_id, resource_id, start_step, initial_value)`：複合主キー、runへのFK。全runのAlphaはstart_step=0。Betaは移行時のactive runにだけ追加し、そのcheckpoint.stepを開始位置とする。終了済み旧runにはBetaを作らない。新規runは両者0。
- `beta_checkpoints(run_id, state_json)`：runへのFK。Beta step・量・要求/実増減・計算日時とSchedule（基準日・基準値・倍率・有効期間・取得状態）を保存。
- `beta_open_candles(run_id, candle_json)`：Beta checkpointへのFK。Alphaの既存open_candlesを保持し、確定足/outboxは既存テーブルへresource_id=`beta`で追加する。

Alphaは残存確定count＋分内count=step。Betaは同じ合計=step−start_step。開始checkpointはOHLCのサンプルに数えず、Betaの最初の足はstart_step+1の実観測から。step/timeは両checkpointで一致し、実サンプルがあれば最後のOHLC close/atもcheckpointと一致する。全runを起動検査し、全確定足にoutboxを要求する。台帳のない今回の版でoutboxや本体を削除しない。

各ステップではAlphaから計算済みの実増減量を使ってBeta・Scheduleを計算し、両分内足、境界での両確定足とoutbox、両checkpointを**同一BEGIN IMMEDIATE/COMMIT**で更新する。片方の失敗は全体ROLLBACK。同期は独立接続の読み取りトランザクションで一致した両checkpointだけを公開し、通信を計算トランザクションへ入れない。再開時に旧分を閉じる処理とnew-runも両分内足を同一トランザクションで確定する。

### 9.3 設定とhash

旧6キー設定を引き続き読む。新しい`initial_beta`は初期案100で、新規runまたは移行時のBeta追加にのみ使用。旧runのconfig_json/config_version/rules_hashは**一文字も書き換えない**。旧config_versionは旧キー集合をそのまま正規化してSHA-256照合し、新しい既定キーを入れて再hashしない。新runは7キーを保存する。rules_hashからは全初期値（Alpha/P/Beta）を除くので、Betaキー追加だけでは規則不一致を起こさない。

移行時に選んだBeta初期値はresource_starts.initial_valueを正本とする。旧runの設定JSONにはBetaを追記しない。継続では保存済みAlpha規則・Beta量を優先し、係数変更等は従来どおり`--use-saved-rules`かnew-runを明示する。CLIのconfig_versionも実際に保存した旧hashを報告する。

### 9.4 明示移行コマンド

2026-10-23に以下の手順で運用DBへ適用済み。以後も運用切替が明示承認された場合にだけ実施し、通常runで暗黙移行しない。

1. 運用Workerと同期、状態確認等の接続を停止する。DBの絶対パス・旧コードSHA・configを確認し、元DBを削除しない。旧コードと設定も保全する。
2. 新コードの固定依存を準備し、`uv run --locked python -m resource_simulator migrate --db <絶対DBパス> --config config/simulator.toml`。run/new-runと同じProcessLockを保持する。通常runは版1を拒否し、暗黙移行しない。
3. コマンドは版1の物理/FK/設定hash/全run OHLC/outboxを検査後、DB横の未使用UUID名`.v1-backup-<uuid>.sqlite3`へBackup APIで保存する。DELETEジャーナルにして閉じ、別readonly接続で論理検査・旧全行とrowid込みhash/件数の一致を確認する。バックアップのfile SHA-256とlogical hash/件数を`.manifest.json`へfsyncしてから変換を開始する。バックアップ不能・照合失敗なら変換しない。
4. 単一トランザクションで追加DDL・Alpha開始位置・active Beta初期状態を挿入。全旧行のfingerprint一致、全run新不変条件を検査し、最後にuser_version=3としてCOMMIT。旧テーブルのDROP・書換えは行わない。移行中に計算・公開はしない。
5. 別接続で版3の全検査を実施。出力のbackup/manifestを保持し、`status --db <同じパス>`でrun_id/step/Alpha/Beta開始位置を確認する。移行時のBeta初期checkpointは旧Alphaと同じstep/timeの追加状態で、境界観測や履歴を表さない。最初の次ステップは非連続扱い。
6. 版3への再実行は検証のみで何も追加せずalready_migratedを返す。版2や未知版は変換しない。

### 9.5 失敗・保全・復旧

- バックアップ/manifest失敗：元DBに変換書込なし。未検証ファイルは復元元にしない。原因を解決して再試行。
- DDL・Beta作成・検査・version設定で例外：ROLLBACK、版1全体を維持。強制終了時も再接続で版とfingerprintを検査し、検証済みバックアップを保持する。
- COMMIT結果不明／後検査失敗：自動で上書き復元しない。全接続を閉じ、完全な版1か版3かを調査する。版1なら全検査後再実行、版3なら検査のみ。破損なら主DB/WAL/SHMを一式別の保全先に残す。
- 明示復旧：全利用プロセス停止後、manifestのbackup_sha256と版1検査を照合したバックアップを**別パス**へ配置し、旧コードで検証する。壊れた元DBや古いWAL/SHMを混在させない。保全・検証後にのみ運用パスへ切り替える。新DBや空DBで初期化する復旧は禁止。
- 版3で計算後の版1復元は進捗を失う。Tableには進んだ世代/stepがあり得るので、公開Entityを消したり世代を再採番して上書きしない。原則は新コードを修正して前進復旧。旧Workerや旧APIへの単独ロールバックは版3/公開版2を読めず失敗するため、互換API/Webは残して停止値を表示する。

バックアップは元DBと同じローカルディスクのためディスク故障対策ではない。Windowsでの停電・実I/O故障・大量の運用DB容量/移行時間は未検証。

### 9.6 公開切替順序

1. 今回の作業ブランチをレビューし、明示承認後に既存デプロイ元`codex/stage1-local-worker`へ取り込む。クリーン状態・origin一致を確認する既存deploy.pyのガードは維持する。2026-10-23の承認済み切替ではfast-forwardによる取り込み・リモート反映・本番デプロイを実施した。今後も明示承認された切替作業で実施する。
2. **互換APIとWebを先に**デプロイ。旧Worker・旧Table版1でAlphaが従来値、Betaが未提供となることを確認。旧WebはAlpha互換トップレベル項目で継続できる。新Webも旧API版1を読み取れる。
3. Worker正常停止、バックアップと版1→3移行、ローカルstatusを確認して新Workerを開始。同期再開後にTable/APIの公開版2・単一resourcesペアとSQLiteの同じrun/step/計算日時を比較する。verify_azure.py --compare-onlyも両resourcesを比較する。
4. 既取得Webの自動更新、下限表示、04/18境界、停止値/停滞・障害復帰、320px/PCでの2/1列を実運用側でも確認。ビルドSHAを記録。今回のローカル結果をAzure実機結果と読み替えない。
5. 新Workerから版2を送る前に旧APIを更新しておく。版2取得後は同期・Webとも版の後退を受け入れない。同run/同stepの移行checkpointは再公開しないため、最初の正常な次ステップからペアが公開される。1つのEntityとETagで片方だけの公開を防ぐ。

## 10. 版3を保持した履歴配送（0.4現行）

### 10.1 原本を変更しない配送確認

`<絶対DBパス>.history-delivery.sqlite3`を同期スレッドで新規作成する。`receipts(target,run,resource,minute,hash)`、主キーは先頭4列。targetはSDK client.url（endpoint/table）と設定partitionのSHA-256、hashは正規化済み公開OHLCのSHA-256。sourceは同じ絶対DBパスをmode=roで開き、版3を確認し、配送DBをreadonly ATTACHしてoutbox JOIN candlesからreceiptのない行だけ最大100件読む。source接続を閉じてからネットワーク送信・読戻しを行い、成功した足のreceiptをFULL同期SQLiteトランザクションで保存する。全接続は明示closeする。

計算DBのDDL/user_version/checkpoint/既存行/rowid/設定/outbox再試行情報は変更しない。全足にoutboxが必要という版3の復元不変条件は維持。statusのpending_ohlcは従来どおりoutbox総数であり「配送未確認件数」ではない。receiptとの差分が実配送残件。別DBのため同時原子性を主張せず、配送確認が欠けた時は安全な再送で解決する。sourceを削除する判断には一切使用しない。

### 10.2 失敗・復旧・切替

- sourceは復旧の正本、receiptは再送省略の補助。receiptなし/応答不明/COMMIT前終了は同じクラウドキーで再送・読戻し。一致しないクラウド値は書換えず停止/再試行する。ネットワーク障害は計算を止めない。
- receipt DBを紛失した場合は全outboxを再走査し既存足を照合する。破損時は計算を継続し同期のエラーを記録。停止後に壊れたreceipt DBを別名で保全し、新規補助DBで再配送する（sourceは削除/移行しない）。別クラウドへ向けた場合はtargetが異なるため古いreceiptを流用しない。
- receiptには履歴削除後のsample累積/step範囲/退避manifestがない。旧版2のcandle_ledgerと同等ではなく、版3からのpruneは許可しない。日次Blob/圧縮退避照合/ローカルおよびTable削除は別移行で対応する。
- 今回、版3運用DBの移行は**不要**。既存バックアップ手順と全runの整合性検査は適用時にも行い、運用Workerを止めて固定コードを切替える。過去の版1→3移行をもう一度行わない。元のrun/世代/step/Beta開始位置を維持する。
- 新Workerを止め旧リリースコードへ戻しても、計算DBは版3のままで互換性を維持する。補助DBを旧コードは参照せず、同期済みTableは残して閲覧可能。進行したsourceを古いバックアップで上書きしない。

通常3か月はTableから読めるので日次化未実装でも履歴閲覧は成立する。ただし退避要件D-09/C-11と容量抑制は残作業。全データを保持し続ける間のディスク容量/起動検査時間を監視する。適用手順は[履歴運用手順](history-operations.md)、実施範囲は[検証記録](history-validation.md)。
