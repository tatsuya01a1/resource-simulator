# 履歴グラフ：本番切替記録

2026-10-28、版0.1。ユーザーの明示実行指示を受け、API/Web先行公開→旧Worker互換確認→停止・保全→新Worker→全件照合を実施した。DB移行・初期化・世代再採番は行っていない。

## 公開物と事前確認

- 公開URL：https://polite-plant-0eb01a400.3.azurestaticapps.net
- API/Web/Workerコード：`36385dece313b50e87cba6b11a1d448ac23a84d8`。検証ブランチとGitHubリモートが一致し、開始時の作業ツリーはクリーン。追加実装なし。
- 公開元`codex/stage1-local-worker`を`514e700`から検証済みSHAへfast-forward。本番release作業ツリーも同一SHA・クリーン・origin一致を確認した。今回の後続コミットは記録/運用文書だけで、公開コードとは区別する。
- 旧公開/Workerコード：`547adcbb565b5278fa23622b96c616e8098b4b34`。コード全体をGit archiveで保全し、設定・旧API設定を秘密領域へ保全した。
- Azure：既存`rg-resource-simulator`、`rsim16701820261022`の非公開`CurrentState`、SWA `resource-simulator-web`。検証リソースとの混用なし。
- WorkerはPython 3.13.16・既存uv.lock、APIはLinux Python 3.11の固定wheel。依存変更なし。

## 切替前の状態と先行公開

開始時は旧Workerプロセスが存在せず、公開値はstep **227,989**（計算日時2026-10-25 06:56:14 JST）で停滞していた。DBとAPIのrun/世代/stepは一致。今回の切替による停止と区別する。DB版3、確定OHLC/outbox各**5,961件**、配送補助DBは未作成。整合Backup APIコピーでintegrity/FK・全run不変条件を検査した。

10/28 **22:14 JST**、保全した旧コードを同じ運用DBから非表示で再開し、次step227,990へ進行。過去の停止区間を補完せず、保存済み量・規則・Beta開始位置を維持した。

Azureの現行API設定を読み直して保全し、`HISTORY_READ_SAS`だけを追加（BUILD_COMMITは公開SHAへ更新）。既存TABLE_READ_SASや他設定の値は保持し、公開後にも再照合した。履歴SASはreadのみ、HTTPS、CurrentState partition `ohlc|`〜`ohlc|~`、90日期限。失効は **2027-01-26 22:14:39 JST**。署名/キー/証明書/設定本文をGit・ブラウザ・報告へ出していない。Workerの既存Table限定Storage Table Data Contributorで配送可能で、権限追加・Table作成は不要だった。

既存deploy.pyのブランチ・clean・origin一致検査を維持して公開。SWA CLIの設定探索先だけをweb/distへ明示し、無関係なキャッシュを探索させない。旧Worker稼働中の新APIを10回観測し、step228,153→228,166、両resource提供・stale=false・新buildを確認。未同期履歴は「確定履歴未作成」、サンプル表示なし。配布JS/CSSはreleaseビルドとバイト単位で一致した。

## 停止・バックアップ・新Worker

**22:19:04 JST**、旧Workerを停止ファイルで正常終了し、旧PID終了とProcessLock解放を確認。停止step **228,277**、OHLC/outbox各**5,973件**。

SQLite Backup APIで`.cache/history-production/stopped-v3.sqlite3`を作成。別接続で版3、integrity_check=ok、FK違反0、Store.validate_all全runを確認し、全テーブルのrowid込み件数・内容hashが停止元と一致した。バックアップSHA-256：`004163787ddf2df3626e60d689fdd50acfbd5400adefaf22bc04623c3b9f38e9`。同一ディスク保全であり別媒体のディスク故障対策ではない。

新Workerは同じDBを開き、boot step228,277、最初の観測で**228,278**へ進行。migrate/new-runは実行していない。

| 対象 | 現行パス/状態 |
| --- | --- |
| 運用DB | `C:\Users\LovelyQte\Documents\resource-simulator\.secrets\worker.sqlite3` |
| cloud設定 | 同じディレクトリのcloud.toml。切替前後の内容hash一致 |
| 配送確認 | 同DB名末尾`.history-delivery.sqlite3`。本番source/target用receipts_v2を新規作成。検証記録の流用なし |
| 稼働コード | `C:\Users\LovelyQte\.codex\worktrees\alpha-beta-release\resource-simulator` |
| 常駐 | 親PID26012・実Python子PID252。同一Workerのlauncher/実行子、旧/検証Workerなし、DBロック保持 |
| 管理 | 標準.secrets/worker.pidとworker.stop、READMEのworker_control.py status/stop/start |

release側の.secrets/.venvは従来どおり本体へのjunction。非表示常駐であり、PC再起動時の自動起動登録は未実施。

## 同一step・全履歴の照合

run **`ac23fe78-636c-40ba-ab6d-bef83f17c26f`**、世代 **1**を維持。Alpha開始step0、Beta開始step **98,497**を保持。規則/config hash、旧全candles/outbox/run/開始行のrowid・内容が不変で、削除なし。

85秒の通常観測でstep228,339→228,424、連続86ローカル状態。Storage同step一致100観測、API同step一致101観測、3経路共通一致**80ステップ**（228,339〜228,423の観測部分集合）。量・実増減量・P・Beta倍率/取得状態・計算日時を含む公開状態全体が一致。異なるstepは比較していない。計算日時差の中央値**1.00385秒**、後退・取得エラー・停滞なし。新Workerの同期エラーログなし。

固定範囲 **2026-10-22 15:34 JST以上〜10/28 22:25 JST未満**を、1分原本・1000行cursor・最大7日以内で全量照合した。全保有期間が7日未満であり、週/月/3か月は同じ全保有集合になる。当日は両11足。Storageも同じキー範囲で照合した。

| リソース | SQLite/API/Storage一致足数 | APIページ数 |
| --- | ---: | ---: |
| Alpha | 3,814 | 4 |
| Beta | 2,171 | 3 |
| 合計 | **5,985** | **7** |

キー集合・件数・OHLC・sample_count・first/last_observed_atを含む**全フィールド完全一致**。重複/不足なし、全走査4.657秒。22:25の配送確認は5,985/5,985件、**未確認0件**。既存履歴の同期完了に加え、切替後に確定した足も公開済み。outbox総数は保持するため配送待ち数とは異なる。

Betaの最古足は10/23 18:57 JSTで、追加前の足なし。10/25〜28の停止区間にも足を作成していない（原本キー全件との一致）。全3か月分の存在を意味せず、保存済み実履歴だけを表示する。今後も通常の確定・配送を継続する。

## 本番画面・定期更新

Chromiumで1280/320/390pxを本番実データで確認。PC同幅2列、スマートフォン幅1列、数値・期間ボタンの横溢れなし。グラフ中心配置、JST軸、ローソク足、タップ詳細、当日/週/月/暦3か月、タブ復帰を確認。実スマートフォンではない。

本番の実保有量で1週間2.868秒、1か月1.931秒、3か月1.947秒。検証時約40秒/約22万足とはデータ量が異なり、性能改善値として比較しない。「履歴を取得中…」と取得中の最新step進行を確認。66秒観測で最新値の継続更新、次周期の3か月取得はmanifest **2要求**・変更日の本体**2要求**（合計1,848 bytes）。全期間の再取得なし。

画像は`.cache/history-production/production-quarter-{1280,320,390}.png`と同期前`before-sync-1280.png`。全画像を目視確認。3か月表示の空白は保存履歴がない期間/実欠測で人工補完なし。取得失敗/通信復旧は分離Azure検証の結果を採用し、本番DBの障害注入・負荷試験は行っていない。

## 確認時点・復旧・残事項

22:24:37 JSTのAPI確認step228,608に続き、**22:33:44 JSTでstep229,153**、stale=false、PID/DBロック維持。配送確認**6,001/6,001件・未確認0**、設定hash不変・同期エラーなし。新しい履歴も22:25の全照合で一致。さらに後の稼働確認値は完了報告を参照する。

復旧時は新Workerを標準stopで正常停止し、DBロック解放・整合Backup API保全の後、保全した旧SHA547adcbを**進行した同じ版3DB**で再開する。旧版では履歴配送が止まる。互換API/Webと既存履歴は保持。任意の旧版への復帰は保証しない。古いバックアップへの自動巻戻しはせず、進捗喪失を伴う復元は別途判断する。DB/OHLC/outbox/配送記録/公開Entityを削除して解決しない。[運用手順](history-operations.md)参照。今回ロールバックは不要だった。

検証環境を保持し、検証DB最終step422,143不変・Worker停止を確認。日次Blob/圧縮退避/prune、再起動時自動起動、実スマートフォン、実請求、長期負荷、別媒体/ディスク故障復旧は後続/未検証。費用前提は[最終検証記録](history-azure-validation.md)を維持する。SAS失効前更新と容量監視が必要。

再検証：Worker78/API10/Web16件すべて成功、TypeScript/Viteビルド成功。秘密・DB・生ログ・画像はGitに含めない。補助は`.cache/history_production*.py`/history_production_browser.mjs、結果JSON/backup/旧コードは.cache/history-production/、設定保全は.secrets/history-*にローカル保持する。
