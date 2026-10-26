# 履歴ローソク足の検証記録

0.2追記（2026-10-26）：後続の実Azure・配送DB障害・全量cursor・切替/旧Worker復帰・性能/費用は[Azure最終検証](history-azure-validation.md)。以下はf22b2e6時点のローカル結果として保持する。最新方式は基本設計17章・保存設計11章。本番未適用は継続。

版0.1、2026-10-23〜24（更新2026-10-24）。作業ブランチ`codex/ohlc-history`。基準は本番コード`547adcbb565b5278fa23622b96c616e8098b4b34`を含む最新リリース記録`514e700`。`git fetch origin`後に同refから作成した。mainから作り直していない。作業開始時の未コミット変更なし。

**本番未適用**。今回、運用DBへの接続/移行、運用Workerの停止/再開、Azure書込/本番デプロイを行っていない。計算規則/run/世代/step・既存DBは変更しない。実装・試験は隔離一時SQLiteとTableテストダブルで行った。

## 実装とAC対応

| 対応 | 実装・検証 |
| --- | --- |
| D-04/05、AC-07/10/27 | HistorySync独立スレッド、mode=ro source、永続配送補助DB、create/409/readback、別値上書き拒否。送信成功後の応答喪失、配送記録喪失、通信断/回復、送り先変更を検証。原本/outbox全行・DB版3・既存復元検査を保持 |
| D-08、UI-05、AC-11/28 | 半開区間/7日/limit/cursor、run/resource/期間scope、暦月末/閏年/JST日付・3か月、期間外を検証。3か月は7日以下へ分割取得 |
| D-06/07、UI-04、AC-29 | 実保存足を同期/読取り。OHLC/countの集約、欠測でのグループ分割、Beta開始前/別runは空、原本不変。既存65件が移行/開始位置/両resource保存/強制終了復元を継続検証 |
| UI-10/11、AC-19/20/30 | 既存pollerの巻戻り防止・前景/復旧・遅延を維持。ブラウザで履歴60秒失敗/古い足保持/最新step進行/履歴復旧・遅延中run切替消去を検証 |
| UI-12、NF-01、AC-17/26/31 | 1280/768px同幅2列、390/320px1列、横溢れなし、グラフが上段より大きい。タップ/左右キー/Endで日時/4値/count表示。320pxはSVG幅と軸文字を調整し目視確認 |

## 実行コマンドと結果

固定環境：既存Worker Python3.13.16、API検証Python3.11.17、Node20.19.0、既存package-lock/uv.lock。依存追加/更新なし。

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests -q
api/.venv/Scripts/python.exe -m unittest discover -s api/tests -q
npm.cmd --prefix web test
npm.cmd --prefix web run build
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path (Get-Location) '.cache/pw-browsers'
node web/tests/local-browser.mjs
node web/tests/history-browser.mjs
git diff --check
```

Worker系72件（既存65＋履歴7）、API10件（既存7＋履歴3）、Web14件（既存9＋履歴5）が成功。TypeScript検査とVite本番ビルド成功。ブラウザの最終結果はGit除外`.cache/validation/history-browser.json`、画像は同フォルダーの`history-1280.png`、`history-390.png`、`history-320.png`。既存カード回帰の結果も同フォルダーへ保存する。

画像は**一時DBの実計算・実OHLC保存→HistorySync→Tableダブル→実read_history→React**を通した検証画面。3時間の疎な観測/16分停止を作り、未確定分を除いた実保存足だけを表示する。架空のグラフ配列を本番アプリへ埋め込んでいない。期間変更/失敗/遅延等はブラウザでHTTPを差替えて検証。Azure実通信の画像ではない。実スマートフォンは未確認。

## 検証中の修正・制約

Windowsで補助SQLite接続がcontext managerだけではcloseされず削除/後処理が失敗したため、明示closeに修正して再検証した。Node20/Windowsのglob展開に合わせテスト対象ファイルを明示した。ブラウザ試験はhasTouchと時計を初回navigation前に設定し、非同期期間取得の完了を待つよう調整した。320pxの固定SVG縮小で文字が小さくなる点も修正した。パス読取り/Chromium実行がsandboxで拒否された箇所は承認後に同じ隔離試験を実行。運用への代替操作は行っていない。

## 残作業・未検証

- Azure実Tableの履歴SAS範囲/Query、実API/SWA公開、旧Workerからの切替と運用履歴全件照合。手順は[履歴運用手順](history-operations.md)。今回のダブル試験を実機試験としない。
- 実スマートフォン、実機の長期/3か月全量表示時間、大量追送時間、費用/転送/負荷。分割/集約ロジックと固定時計境界は検証済みだが3か月連続運転ではない。
- 日次Blob/圧縮退避/照合後削除/配送台帳の版3からの新移行。旧DB版2案は実装していない。AC-12/C-11を含むMVP全体の完成ではない。
- 停電・物理I/O障害・ディスク故障の無損失保証。既存強制終了試験と区別する。自動起動登録・長期容量監視は後続。

必要なDB移行：なし（版3維持、別配送補助DBを作成）。必要なAzure設定：APIサーバーだけにHISTORY_READ_SAS。公開順：API/Web先行→Worker正常停止・backup/照合→同DBでコード切替/再開→全履歴/実画面照合。今回は準備のみ。

追加全量境界試験：暦92日/132,480分のTableダブルを7日ずつ読み、Alpha132,479分（1分欠測）、30日遅れて開始するBeta89,279分の件数・OHLC・count合計が一致。原本10,080行上限と描画射影を通過した。これはAzure実データの3か月連続運転試験ではない。
