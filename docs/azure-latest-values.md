# 最新値公開の構築・運用

版：0.1／2026-10-22。基準：51ba06ce415374ca71e8b040e64cf3310a3a7d00。

## 今回の範囲

PC→非公開Table→SWA Managed Functions→匿名閲覧Webの最新値だけを公開する。OHLCはローカル保存・outbox保持を継続し、クラウド送信・日次化・退避・削除を行わない。従ってDB版2は不要で、版1と従来の復元検査を維持する。

既存subscriptionの接続先を読み取り、専用の`rg-resource-simulator`へStandard LRS StorageとStatic Web Apps Freeを構築する。既存NetworkWatcherRGは変更しない。設計したBicepは`infra/main.bicep`、初期設定は`scripts/azure_setup.py`。SWAのrepositoryUrlとbranchは本リポジトリと`codex/stage1-local-worker`を指定し、mainをデプロイしない。

## 現行仕様・依存

2026-10-22に[公式ランタイム表](https://learn.microsoft.com/en-us/azure/static-web-apps/languages-runtimes)でManaged Functionsのpython:3.11を確認。[API制約](https://learn.microsoft.com/en-us/azure/static-web-apps/apis-functions)に従いHTTPのみを使用し、Managed Identityは仮定しない。

Workerは既定Pythonを変えず3.13.16を維持し、Azure SDKはuv.lockで固定。APIは独立したPython 3.11。最新azure-functions 2系はPython>=3.13のため採用せず、3.11対応の1.25.0を選び、全依存をapi/requirements.txtへ固定する。WebはReact・TypeScript・Viteの固定版とpackage-lock.jsonを使う。

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

SWA Freeのサイト固定費は0円。APIを別の有料常駐プランにしない。標準の無料割当や特別クレジットを見込まず、Functionsの参考従量単価（0.20 USD/100万要求、0.000016 USD/GB秒）、為替160円/USD、256MB・平均250msとして保守的に計算すると、24時間1端末のAPIは要求約83円＋計算約425円/月。10GBの転送を26円/GBとして別に見込むと約260円。**今回の参考見込みは月約800〜1,000円**。実際のManaged Functions課金区分・無料割当・実行量・帯域を公開後に確認する。256MB・250msは性能計測値ではなく見積り仮定。

2時間/日1端末ならAPI要求216,000件で大幅に小さい。将来のMVP全体でも、OHLCの1分書込43,200件はTable約0.25円/月、日次gzip30ファイル/月と少量の保存・読取・退避が追加される。将来の履歴API・圧縮量は未計測なので予備枠を含め**1リソースMVPで月1,500円を目標**とする。100リソースの費用を先行して本実装の予算としない。

多数端末の24時間閲覧、応答時間・メモリ増、履歴の大量読取、ログ収集を加えれば増える。月3,000円は説明を伴う許容額であり通常目標ではない。月1,500円の予算通知と日次実績を監視し、通知を強制課金停止と扱わない。SWAの100GB帯域枠はsubscription全体の共有枠であり、超過時のFree制限にも注意する。
