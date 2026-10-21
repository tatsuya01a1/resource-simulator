# 段階2のローカル保存・移行設計

- 文書版：0.1／更新日：2026-10-22
- 基準コミット：`cf351ea0808ac9c39b05ce83dd1507a845e455c7`
- 上位文書：[要件定義書0.4](resource-simulator-requirements.md)、[基本設計書0.4](resource-simulator-basic-design.md)第5〜8章
- 対応：D-01〜02、D-04〜06、D-09、F-04、AC-04、07、09〜10、12、14〜16
- 状態：**段階2実装時に採用する設計。今回のコードはスキーマ版1のまま。移行・送信・削除は未実装・未検証。** 計算式、公開API契約、既存AC-IDは変更しない。

## 1. 保存するものと責務

スキーマ版1は全確定足にoutboxを要求し、保存中の全OHLCのsample_count合計とstepを比較している。この検査は、送信も履歴削除もしない段階1でのみ成立する。版2では、OHLC本体・配送台帳・未送信キュー・集計進捗・退避の確認記録を分離する。

OHLC本体を削除しても、分ごとの小さい台帳と実行ごとの累積値、最後の確定位置を残す。台帳の無条件な削除・再集計による不足分の帳尻合わせは禁止する。送信済みかどうかをoutboxの「不在」から推測せず、履歴の削除済み状態も明示する。SQLiteのコミット済み記録だけを正常状態の証拠として使う。

台帳は1秒値の履歴ではない。分ごとの識別・サンプル範囲・hash・配送／削除状態だけを保持する。段階2では台帳・退避確認記録を削除しない。長期間の台帳圧縮は別設計とし、台帳の容量・起動検証時間は実装時に測定する。

## 2. スキーマ版2の追加・変更

`PRAGMA user_version=2`を使う。既存の実行・checkpoint・分内状態とrun_idは維持する。MVPではDBごとに1シミュレーション、resource_idは`resource`固定。

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
L + O == checkpoint.step
```

台帳を分順に並べ、first_step=1からlast_stepまで重複・隙間がなく、各範囲の長さ=countであることを検査する。分の実時間には停止の空白を許すが、観測stepの範囲には隙間を許さない。分内状態があれば末尾の`checkpoint.step-O+1..checkpoint.step`に対応し、確定足より後の分であること、close・最終観測日時がcheckpointと一致することを確認する。

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
