# 最新値公開の構築・運用

履歴版の運用手順は[履歴適用・復旧](history-operations.md)。履歴専用HISTORY_READ_SASを追加し、currentの1Entity限定SASは維持する。今回の履歴版は本番未適用。以下は従来リリース時の構築記録。

版：0.4／2026-10-23。基準：51ba06ce415374ca71e8b040e64cf3310a3a7d00。

改訂：0.1で構築方式・認証・費用を具体化。0.2で実機検証済みのデプロイ、再現手順、運用上の残作業を記録した。

## Alpha/Beta切替（2026-10-23に本番適用済み）

新実装はDB版3・公開版2。旧運用DB版1と旧API版1の存在を前提に、[保存・移行設計9章](resource-simulator-storage-design.md)の順序で互換API/Webを先に、Worker停止・バックアップ・明示移行を後に行う。旧Web用のAlpha互換項目を維持し、Beta未提供時は0を返さない。単一EntityのETag、同期失敗時の計算継続と秘密除外は維持する。

2026-10-23の明示承認により既存デプロイ元をfast-forwardし、互換API/Web先行公開→旧版実通信確認→正常停止→新規backup/版3移行→新Worker常駐起動を完了した。公開SHAは`547adcbb565b5278fa23622b96c616e8098b4b34`。日時・backup照合・同一stepのSQLite/Table/API比較・公開画面の結果は[本番切替記録](alpha-beta-validation.md)を参照。新規Azure資源は作成していない。既存のdeploy.pyは承認済みデプロイ元ブランチとorigin一致のガードを維持する。取り込み・本番公開は実施済み。ローカル・本番検証は[Alpha/Beta検証記録](alpha-beta-validation.md)。最新状態は2リソースでも1 Entityのままなので操作数モデルは同じ、転送量・本番応答時間・費用の再実測は未実施。

## 旧1リソース先行公開の範囲（以下は過去の構築記録）

PC→非公開Table→SWA Managed Functions→匿名閲覧Webの最新値だけを公開する。OHLCはローカル保存・outbox保持を継続し、クラウド送信・日次化・退避・削除を行わない。従ってDB版2は不要で、版1と従来の復元検査を維持する。

既存subscriptionの接続先を読み取り、専用の`rg-resource-simulator`へStandard LRS StorageとStatic Web Apps Freeを構築する。既存NetworkWatcherRGは変更しない。設計したBicepは`infra/main.bicep`、初期設定は`scripts/azure_setup.py`。SWAのrepositoryUrlとbranchは本リポジトリと`codex/stage1-local-worker`を指定し、mainをデプロイしない。

## 現行仕様・依存

2026-10-22に[公式ランタイム表](https://learn.microsoft.com/en-us/azure/static-web-apps/languages-runtimes)でManaged Functionsのpython:3.11を確認。[API制約](https://learn.microsoft.com/en-us/azure/static-web-apps/apis-functions)に従いHTTPのみを使用し、Managed Identityは仮定しない。

Workerは既定Pythonを変えず3.13.16を維持し、Azure SDKはuv.lockで固定。APIは独立したPython 3.11。最新azure-functions 2系はPython>=3.13のため採用せず、3.11対応の1.25.0を選び、全依存をapi/requirements.txtへ固定する。WebはReact・TypeScript・Viteの固定版とpackage-lock.jsonを使う。

SWA CLI 2.1.0はAPIビルドを省略するため、`scripts/deploy.py`でLinux CPython 3.11向けwheelを`.python_packages/lib/site-packages`へ明示的に梱包する。HTTP関数は`api/current/function.json`にGET専用・匿名を定義し、`api/handler.py`を呼ぶ。Windowsのapi/.venvや秘密情報はアップロードしない。デプロイ前に作業ブランチ・Git作業ツリーのクリーン状態・originとHEADの一致を検査し、APIのBUILD_COMMITへそのSHAを設定する。

## 保存と巻き戻り防止

同期スレッドは毎回SQLiteの読み取り専用トランザクションからactive_run・設定・checkpointを読む。計算トランザクションでは通信しない。待ち行列を作らず、失敗後は最大30秒程度のバックオフ後にその時点の最新値を取り直す。OHLC・outboxは変更しない。

現在のsimulation_runs.rowidをrun_generationとして公開する。版1では実行を追加するだけで削除・再採番しないので、new-runは世代が増える。将来の版2移行でもこの値を保持する必要がある。別DBの新規作成や古いバックアップ復元で世代が後退・衝突した場合はクラウドを上書きせず、明示的な復旧が必要。run_idの辞書順やPC時計では実行の新旧を決めない。

最新状態はCurrentStateの単一Entityへ格納する。初回はcreate（存在時409）、以後は読んだETagに対するIfNotModifiedの置換更新。古い世代、同世代の別run_id、同じか小さいstepは公開しない。409/412は読直して比較する。SDKの自動再試行は無効にし、不確定終了した要求もETagで保護する。Webでも世代・run_id・stepとリクエスト世代を比較して遅れた応答を破棄する。

## 認証・秘密情報

開発者だけが`az login`を使う。Workerは専用EntraサービスプリンシパルとRSA証明書で認証し、CurrentStateテーブルスコープのStorage Table Data Contributorのみを割り当てる。サブスクリプションOwnerをWorkerへ渡さない。

APIにはCurrentStateの固定PartitionKey/RowKeyだけを読めるHTTPS・read-only・90日期限のTable SASをSWAのサーバー側appsettingsへ設定する。Storageキーは初期設定時だけメモリで使用し、ファイル・Git・Webへ残さない。秘密鍵・SAS・デプロイトークン・実接続設定はGit除外の`.secrets`へ保存し、Windows ACLを現在ユーザー限定にする。更新時には証明書・SASの期限を確認し、同じ権限で再発行してappsettingsを更新する。

Storageの匿名Blobアクセスは禁止し、Tableの匿名アクセスも許可しない。公開ネットワークエンドポイントは認証付きHTTPS用であり、匿名の静的サイトやブラウザから直接Storageを読む構成にはしない。APIは明示した公開項目だけを返し、エラーに例外本文・SAS・内部パスを含めない。

## 料金見込み（30日、MVPは1リソース）

確認元：[Azure Retail Prices API](https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices)、[Table料金](https://azure.microsoft.com/en-us/pricing/details/storage/tables/)、[SWA料金](https://azure.microsoft.com/en-us/pricing/details/app-service/static/)、[SWA制限](https://learn.microsoft.com/en-us/azure/static-web-apps/quotas)。JPY価格は参考値で、請求通貨・税・改定で変動する。

東日本のTables Standard LRSは各操作0.0567円/10,000件、保存7.0897円/GB月（公式料金APIから取得）。Account Encrypted Tablesの別単価を混同しない。1秒同期は毎回1読取＋最大1書込で、各2,592,000件＝合計約29.4円/月。Webを24時間表示する1端末は2,592,000 API要求・Table読取で追加約14.7円/月。今回のStorage操作合計は約44.1円/月＋微小な保存量。

SWA Freeのサイト固定費は0円。APIを別の有料常駐プランにしない。[Functions料金](https://azure.microsoft.com/en-us/pricing/details/functions/)と公式料金APIでEast AsiaのStandard単価を確認した。標準の無料割当や特別クレジットを見込まず、0.0003円/10要求、0.0025円/GB秒、256MiB・平均250msとして計算すると、24時間1端末のAPIは要求約77.8円＋計算約405円/月。10GBの転送を26円/GBとして別に見込むと約260円。**今回の参考見込みは月約800〜1,000円**。実際のManaged Functions課金区分・無料割当・実行量・帯域を公開後に確認する。256MiB・250msと転送26円/GBは性能・請求の実測値ではなく見積り仮定。

2時間/日1端末ならAPI要求216,000件で大幅に小さい。将来のMVP全体でも、OHLCの1分書込43,200件はTable約0.25円/月、日次gzip30ファイル/月と少量の保存・読取・退避が追加される。将来の履歴API・圧縮量は未計測なので予備枠を含め**1リソースMVPで月1,500円を目標**とする。100リソースの費用を先行して本実装の予算としない。

多数端末の24時間閲覧、応答時間・メモリ増、履歴の大量読取、ログ収集を加えれば増える。月3,000円は説明を伴う許容額であり通常目標ではない。月1,500円の予算通知と日次実績を監視し、通知を強制課金停止と扱わない。SWAの100GB帯域枠はsubscription全体の共有枠であり、超過時のFree制限にも注意する。

既存のsubscription予算`1500yen`（Monthly、1,500円）と75%超過通知の有効状態を読み取り確認した。既存設定は変更していない。

## 再現可能な構築・デプロイ手順

リポジトリルートのPowerShellから実施。Azure CLIを導入し、開発者として`az login`（必要なら`--tenant <tenant-id> --use-device-code`でMFA）後、`az account show`・`az group list`・`az resource list`・自身のロール割当を読み取り確認する。OwnerまたはRG構築・Storage・SWAとTableロール割当に必要な権限、Entraアプリ登録権限が必要。別PJのRGを指定してはいけない。初期設定スクリプトは既存RGのprojectタグが異なれば拒否する。

```powershell
uv sync --locked
uv run --locked python scripts/azure_setup.py --subscription <subscription-id> --storage <global-unique-storage-name>
npm.cmd --prefix web ci
npm.cmd install --prefix .cache/deploy-tools @azure/static-web-apps-cli@2.1.0 --cache .cache/npm
# 必ずcodex/stage1-local-workerへコミット・Pushし、originとの一致・クリーン状態を確認する
uv run --locked python scripts/deploy.py
uv run --locked python scripts/worker_control.py start
```

Azureの初期構築はBicepから再現し、デプロイは公式SWA CLIにローカルでビルドしたWebとLinux用APIパッケージを渡す。GitHub Actionsのmain自動デプロイを生成しない。`.secrets/deployment.json`に公開URL・ブランチ・デプロイSHAを残し、APIのBUILD_COMMITにも同じSHAを保存する。初期設定の再実行では既存ID・証明書を再利用する。SASを更新した場合はデプロイを再実行してBUILD_COMMITも再設定する。

API用のWindows検証環境を別途作る場合：

```powershell
uv python install 3.11.17 --install-dir .tools/python --no-bin --no-registry
uv venv api/.venv --python .tools/python/cpython-3.11.17-windows-x86_64-none/python.exe
uv pip install --python api/.venv/Scripts/python.exe -r api/requirements.txt
api/.venv/Scripts/python.exe -m unittest discover -s api/tests -v
```

Workerの停止は`uv run --locked python scripts/worker_control.py stop`。ログは`.secrets/worker.log`（1MB×現行＋過去3本）、起動時コンソールは`.secrets/worker-console.log`。同じDBでの二重起動はロックで防止する。証明書・SAS・デプロイトークンをコンソールや共有設定へ貼り付けない。`.secrets`とSQLite一式はGitへ入れず、バックアップはSQLite Backup APIで取得する。

今回の最終公開URL：https://polite-plant-0eb01a400.3.azurestaticapps.net 。デプロイSHA：`cc721d2b4b5573c92545dd1644d3feda1fad7328`。作成したリソースは上記専用RG・Storage・SWAとWorkerのEntra ID／証明書・Table限定ロールだけで、別PJのリソースを変更していない。
