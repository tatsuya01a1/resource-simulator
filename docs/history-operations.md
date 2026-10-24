# 履歴表示の適用・復旧手順

版0.1、2026-10-23〜24（更新2026-10-24）。**準備のみ。今回この手順の本番操作は実行していない。** 最新リリース記録は[Alpha/Beta本番切替](alpha-beta-validation.md)。実装起点は同じコードを含む`514e700`、公開/稼働コードSHAは`547adcb`。今回の検証は[履歴検証記録](history-validation.md)。

## 適用前

1. レビュー済み履歴コミット・固定依存・テスト結果・予算見込みを確認する。対象は既存RG/CurrentState/SWAのみ。運用DBの絶対パス、cloud設定、run/世代/step、Beta開始位置、現在のデプロイSHAを控える。運用DBを開く/Workerを切り替えるのは別途本番指示後。
2. 本番切替時は稼働Workerのコードを編集しない隔離release worktreeで、承認された`codex/stage1-local-worker`へ取り込む。クリーン状態・origin一致を確認する既存deploy.pyのガードは維持する。mainへの取り込みや強制pushは不要。
3. 今回、計算DBは**版3のまま・移行不要**。バックアップは省略しない。既存のSQLite Backup APIで取得し、別接続でintegrity/FK・全runのStore.validate_all、run/step/値/開始位置/全行を照合する。旧版1→3のmigrateを繰り返さない。source/outboxを消さない。新規補助DBはWorker再開後に作成される。

## Azure設定とAPI/Webの先行切替

既存`TABLE_READ_SAS`はcurrent 1Entityの範囲なので、履歴には使用できない。APIだけに`HISTORY_READ_SAS`を追加する。既存Table ContributorのWorkerは追加ロール不要。履歴SASはCurrentStateの`ohlc|`〜`ohlc|~` partitionに限定したread権限（Queryにもreadを使用）、HTTPS限定、90日期限。currentと履歴のSASを別々に更新・期限監視する。ブラウザへいずれも渡さない。

```powershell
# 明示された本番切替時のみ。SASはファイルへ保存し、stdoutへ出さない。
uv run --locked python scripts/prepare_history_settings.py
```

この準備コマンドは既存deployment.jsonを読み、Azure CLIで対象Storageキーを取得して`.secrets/history-api-settings.json`を作成する。Azure設定は適用しない。API既存設定を含むため、切替直前に現在の設定と比較し、他の設定を失わないことを確認して`.secrets/api-settings.json`へ反映する。SASをログ・Gitへ含めない。既存deploy.pyがこのファイルを読みBUILD_COMMITを設定し、設定反映・ビルド・API/Web公開を行う。

```powershell
# レビュー・origin一致・設定反映準備後、承認されたreleaseブランチでのみ
uv run --locked python scripts/deploy.py
```

公開直後、最新値は旧Workerのまま約1秒更新、履歴はまだ未作成でも正常。`/api/current`と`/api/ohlc`の200/no-store、履歴400/416/503の区別、設定済みならcurrent.history_available=trueを確認。history_availableは履歴APIの設定有無であり、配送済み件数を保証しない。SDK fixed12.7.0、API Python3.11のLinux依存配置は従来通り。

## Worker切替と確認

1. `worker_control.py stop`で正常停止し、全プロセス終了・ProcessLock解放を確認。停止時の整合バックアップを改めて取る。旧コードと設定を保全する。
2. 承認コミットの固定環境で、同じ絶対DB・cloud設定を使い`worker_control.py start`。新しいrunを作らず、同run/世代・次stepから再開する。DB版3・両値・Beta開始位置不変を確認する。
3. `.secrets/worker.sqlite3.history-delivery.sqlite3`（運用DBが従来位置の場合）が新規作成され、履歴同期が全runの古い未配送足から追送する。既存のcandles/outboxは減らない。`status.pending_ohlc`は配送待ちではなく保持outbox総数のまま。配送進行はreceipt件数とsourceの差で確認する。足の内容とreadback一致を確認したものだけreceiptが作られる。
4. 保存済みの同run/resource/minuteのOHLC・sample_countをTableとAPI（resolution_minutes=1）で比較。直近3か月全体を7日以下/cursorで取得し、欠測位置/件数/count合計をSQLiteと照合する。Beta開始前と別runが混ざらないことを確認。追送完了前に完全な履歴と断定しない。
5. PC/320/390px・実スマートフォンで、当日/週/月/3か月、足選択、最新1秒/履歴60秒更新、Worker停止後の最終値・履歴、通信断/復旧・前景復帰を確認する。本番ではブラウザ試験用fixtureを使わない。4時/18時境界の計算は変更していない。
6. デプロイ/稼働SHA、同一step照合、画像、配送時間、エラー/要求数/転送量/料金見込みを記録する。現行[Table上限](https://learn.microsoft.com/azure/storage/tables/scalability-targets)と[Azure料金計算](https://azure.microsoft.com/pricing/calculator/)でJapan Eastの単価を再確認。月1,500円目標と3,000円許容を区別する。予算通知は課金停止ではない。

## 障害・復旧

- 同期失敗：ローカル計算/全OHLC/outboxを継続保存。最初の失敗種別と復旧イベントを秘密を含めずログ出力。2〜60秒の再試行、100件単位追送。クラウド足が異なる場合は上書きせず保全・調査する。
- 履歴API失敗：最新値取得を継続。同期間の取得済みグラフには古い表示を明記し、次の60秒/online/前景復帰で再取得する。期間外は期間選択を促す。別期間/runの履歴を代用しない。
- 補助DB破損：Workerを停止し補助DBを別名で保全、新規補助DBでsource全件から再送する。同じキー/同じ値なので重複しない。source計算DBは消さない。補助DBを失っても全履歴は原本に残る。
- リリース不具合：新Workerを正常停止して旧Alpha/Beta版へ戻せる（計算DBは同じ版3）。同期済みのTableと互換API/Webを残して最終値/履歴を閲覧する。古いバックアップで進行したsourceを上書きしない。新旧Workerを同時に起動しない。
- ディスク故障/破損は別途バックアップ復元手順を使う。receiptだけから計算状態を復元できるとは扱わない。

## 残る運用整備

日次Blob・圧縮退避・照合manifest・版3からの配送台帳移行・安全なprune/ローカル容量抑制は未実装。保持中の原本を削除しないため通常3か月閲覧は成立するが、長期容量/起動時間の監視が必要。SAS更新、料金/長期負荷、Azure実通信、大量追送時間、停電/実I/O障害、実スマートフォンは今回未確認。
