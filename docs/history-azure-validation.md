# 履歴版：Azure最終検証・切替リハーサル

版0.1、2026-10-24〜26。対象`codex/ohlc-history`、実装起点`f22b2e6`。**本番DB・稼働中の本番Worker・本番Azure設定/公開先・本番ブランチは変更していない。** Azure検証用リソースを別に作成し、合成入力を実モデルと実SQLiteに保存した検証データだけを使用した。本番DBのコピーは取得していない。

## 判断と適用範囲

今回の履歴経路は本番切替の準備ができたと判断する。計算DBは版3のままで移行不要。配送確認DBの障害と再送、Azure実通信、同じDBでの旧→新→旧Worker、3か月閲覧と更新量を確認した。適用は別途ユーザー指示後とし、MVP全体の完成や月次請求/実スマートフォンの確認済みを意味しない。

日次Blob/圧縮退避/pruneなしでも通常3か月の閲覧は成立する。元OHLC/outboxを削除しないため容量は増え続ける。長期容量抑制は後続。3か月初回表示に約40秒かかる点と、実請求/実端末未検証は運用上の制約として残す。

## 検証環境と分離

| 種類 | 対象・方針 |
| --- | --- |
| Resource group | `rg-resource-simulator-history-test`、Japan East、purpose=history-validation |
| Storage | `rsimhist20261024`、Standard LRS、Japan East、非公開`CurrentState` Table |
| Web/API | SWA Free `resource-simulator-history-test`、East Asia、https://mango-wave-0fef6ca00.1.azurestaticapps.net |
| Worker認証 | 新規Entra app/SP `resource-simulator-history-validation`、検証TableだけのData Contributor、30日証明書 |
| API認証 | 検証Storageだけのcurrent/history別read SAS、HTTPS・7日期限。signatureは記録しない |
| ローカル | `.cache/history-azure/rehearsal.sqlite3`と専用配送DB。資格情報はACL付き`.secrets/history-test/` |

既存グループを列挙した時点で検証環境がなかったため新設した。`infra/history-test.bicep`と`scripts/history_test_environment.py`は対象名・設定先・ブランチ・clean/pushed一致を検査し、本番deploy.pyのガードを緩めない。CLIの`--env production`は**検証SWA内のスロット名**であり、本番SWAではない。

検証API/Webの公開SHAは`9da61575e83be6e94e025d0bf95ba52003259c00`。以後の修正はWorker配送、検証スクリプト、文書であり、API/Web配布コードは同SHAと同一。Workerの最終配送方式を検証Workerに適用して実通信した。最終ブランチSHAとpush一致は完了報告およびGitで確認する。

Windows GitはHTTPS資格情報がなく対話待ちになったため停止し、非対話再試行でも認証不能を確認した。接続済みGitHubで**ローカルと同一tree SHA**を公開し、元の`f22b2e6`/ローカル修正履歴を`codex/ohlc-history-local-preserved`へ保全した。公開コミットのSHAは再作成により変わる。ファイル内容一致を検査後、ローカル参照を公開コミットへ揃えた。本番ブランチのmerge/force pushは行っていない。

## 実データと照合（AC-27〜29）

検証設定は係数0.02（本番の設定/計算コードは変更しない）。実Engine→Storeで暦92日/132,480分、各観測分3サンプルを作成し、16分の実欠測を挿入。Betaは30日後の開始step129,600とその時点の初期値を保持し、それ以前の足を生成しない。初期step397,392、確定足221,728件。各runの復元不変条件、物理/FK検査を確認した。

専用bulk loaderで検証Tableへ221,728足を投入した実測は127.89秒。これは初回データ準備用の専用account key/batch upsertであり、Worker配送の速度と混同しない。Workerは原本足をcreateのみで送り、既存足は厳密照合する。新Worker開始後、配送確認が222,276件/原本222,276件となる状態を確認し、既存OHLC/outboxは保持した。

同run/resource/minuteのローカル値と実HTTP APIで、open/high/low/close/sample_count/first_observed_at/last_observed_atが一致。投入履歴の2026-10-24T14:34Z（両3サンプル）と切替直後20:38Z（両42サンプル）、通信復旧後の両足を照合した。全量のcursor照合結果はローカル保全`full-compare.json`に記録する。3か月画面では実際にAlpha130,952足・Beta89,545足の描画射影を表示した（範囲は表示時の暦境界で移動する）。

1000行cursorの全走査は245ページ・118.741秒で成功。新Worker停止の2分前までを上端として固定し、下端は取得時の暦3か月境界。Alpha130,855足/408,328サンプル、Beta89,543足/284,392サンプルの**全フィールドが完全一致**した。HTTP bodyは合計65,236,092 bytes、ページ応答中央値はAlpha0.823秒/Beta0.815秒、最大1.082秒/2.062秒。これは原本全照合の所要時間であり、描画用射影の画面表示時間とは区別する。rollback後の旧Workerが新しく作る未同期足を、比較対象へ混入させていない。

## 配送・通信障害（D-04/05、AC-27/30）

実Azureへcreate/readback成功後、receipt保存直前で子プロセスを強制終了。再起動で同じ2足を照合し再記録し、その次の送信件数は0。クラウド足の重複や置換なし。配送DBの排他ロック、破損、欠落、別パスのDBへのreceiptコピー、3回の保全再作成でも検証した。元のOHLC/outboxと版3復元検査を維持した。

発見した問題と修正：旧receiptは送り先とrun/分だけを見ていたため、source絶対パスのownerと元JSON hashを照合するv2へ変更。古い形式は保全するが信用しない。また重複再送でも日別revisionを変更すると無変更日を大量再取得するため、足createと日別通知を同一Table transactionにし、同じ足の再送では通知を変えない。回帰テストで確認した。

検証Workerの送信点だけにServiceRequestErrorを75秒注入した。OS全体の回線や本番接続は操作していない。local step413,941→414,016の75ステップ、確定足222,284→222,286を保存。取得観測の中央値1.004秒（観測漏れを含む最大差2.050秒）で、停止/追いつき計算はしない。APIは同期前の最終値を保持しstale=true。解除後、最新値は17.379秒で追従、履歴は再試行後に元の全フィールドと一致。古いsnapshotのpublishはFalseを返し、実APIの新しいstepは後退しなかった。

## SASと秘密情報

履歴queryは成功（93日別通知）。history SASのwriteは403、期限切れも403。current・他partitionの読取りは404でデータを返さず、current限定SASから履歴も取得できなかった。範囲外に対する404を「対象が存在しないだけ」と解釈せず、別の資格情報では対象が存在することと合わせて判定した。

履歴policyはreadのみ、HTTPS、partition `ohlc|`〜`ohlc|~`、期限2026-10-31T14:34:06Z。APIにwrite/key/Worker証明書を渡さず、ブラウザのリクエストURL/配布物にSASを含めない。DB・資格情報・生ログはGit除外。Storageキーは分離した投入loaderだけで使用した。SASの期限切れ後は別途更新が必要。

## 性能と画面（AC-28/31、NF-01/02）

実SWA、Chromium、API差替えなしの計測。2カード合計、HTTP bodyの非圧縮サイズ。描画時間は要求開始〜選択期間の表示完了で、ネットワーク/取得/集約/Reactを含む。純粋なSVG描画時間とは区別する。

| 期間 | 表示完了 | OHLC API要求数 | 応答body合計 |
| --- | ---: | ---: | ---: |
| 当日初回 | 66.861秒 | 6 | 1,701 bytes |
| 1週間 | 3.499秒 | 18 | 234,080 bytes |
| 1か月 | 14.926秒 | 64 | 283,861 bytes |
| 3か月 | 39.607秒 | 159 | 267,052 bytes |
| 3か月の次の定期確認 | 60秒周期内 | 2（manifestのみ、本体0） | 8,754 bytes |

当日初回は再開後その日の確定足がまだなく、約1分の**足の生成待ち**を含む。最新数値はその間も更新した。大量データ取得で66秒を要した値ではない。定期確認時に変更がある日はその日だけを取得する。初回3か月の多数の日別取得は残るが、毎分全期間を再取得する旧方式は廃止した。長期間の取得/描画は必要分割と集約で行い、原本1分足を変更しない。

PC1280/768同幅2列、320/390px1列、横溢れなし。足のタップ/キー選択、欠測・Beta開始前の空白、約1秒の最新値、履歴だけの失敗時の古いグラフ保持、最新値だけの取得失敗、通信復旧、タブ復帰を実Webで確認。3か月取得中の65秒観測でも最新stepが継続更新した。画面は`.cache/history-azure/azure-history-{1280,320,390}.png`と`azure-quarter-{1280,320,390}.png`。目視確認済み、実スマートフォンは未使用。

## 切替・復旧リハーサル

1. SHA547adcbの旧Workerを検証専用DB/証明書で稼働し、新API/Webを先行公開。実APIの旧Worker値が継続更新することを確認。
2. 旧Workerを正常停止し、Backup APIで`stopped-old-v3.sqlite3`を取得。別DBで全run/integrity/FKを確認し、全テーブルのrowid込み件数・内容hashが停止元と一致。
3. 新Workerは同じrun/世代のstep409,097→409,098から再開。初期化/移行なし。新規receipt DBで既存未送信を照合・同期し、最新値と履歴を実APIで確認。
4. 新Workerを正常停止し、`stopped-new-v3.sqlite3`を取得。旧SHA547adcbで**進行した同じ版3DB**を開き、boot step414,138が新Worker停止位置と一致して次step以降を計算。整合Backup APIコピー上で全runを再検査し、既存OHLC/outboxの欠落・変更なし、コミット済みstepの喪失0。
5. 古い停止バックアップを別パスへ独立復元し、全論理hash・run/世代/stepの一致を確認。検証時点でこれを現行へ上書きすれば6,723ステップを失うため、その復元を通常のrollbackとはしない。

復旧検査の初回は稼働中DBを複数の非transaction SELECTで検査した補助スクリプトが異なる時点を読み、不一致と判定した。稼働中DBの整合Backup APIコピーへ切替えると全検査が成功した。実装の保存transactionを弱めたり不一致を無視して合格としたものではない。旧Workerへ戻すと最新値は続くが履歴配送は停止する。この範囲を越える任意の旧コード/版1へのrollbackは保証しない。

検証終了時は旧コードの検証Workerも正常停止した。source/APIの最終step422,143と両resource全項目が一致し、11秒後にAPIのstale=true、実Webの更新停滞表示と最終値保持を確認した（`final-stop.json`、`azure-stopped-1280.png`）。本番Workerの起動/停止ではない。

## 保持量と費用（実測と推計）

実測：原本222,286足・outbox同数でsource185,241,600 bytes、配送DB177,930,240 bytes、原本JSON文字数合計65,247,600 bytes。WAL/バックアップは別。Tableの課金容量はこのJSON文字数そのものではなく、UTF-16文字列・キー・プロパティ等を含むため**約1KiB/足の推計**を使う。通常2リソースなら2,880足/日、source約2.4MB/日、配送DB約2.3MB/日、Table約2.8MiB/日。3か月フル稼働は約26.5万足、両ローカルDB合計約0.43GB、Table約0.27GB。日次化/pruneまでは原本を保持し続ける。

公式[Retail Prices API](https://learn.microsoft.com/rest/api/cost-management/retail-prices/azure-retail-prices)をJPY・Japan East・Tablesで取得した。Standard LRSのRead/Write/List/Scan/Batch Writeは各**0.0567円/10,000要求**、容量**7.0897円/GB月**（確認時点）。[Table暗号化キーの区分](https://learn.microsoft.com/azure/storage/common/account-encryption-key-create)によりAccount-scopedは異なる単価になる。検証/本番はStandard_LRS、TableのAccountキー指定はなく既定service-scopedとして推計する（`tableEncryption`の省略/nullは未暗号化を意味しない）。適用前に設定が変わっていないことを再確認する。

推計の前提：30日、Worker24時間、最新値のTable read+writeを各最大1回/秒、閲覧1人×2時間/日、3か月画面を毎日1回開く。最新値同期の約518万要求は約29.4円/月、履歴配送は概算8要求/分として約2円/月、閲覧中の最新値APIによるTable read約21.6万は約1.2円/月、履歴manifest/変更日readは1円未満〜数円、0.27GBは約1.9円/月。Storage要求・容量小計は概ね35〜45円/月。

帯域は初回3か月取得でAPI内部が原本を読む分も計上する。ブラウザへの射影だけで見積もらない。日本Storage→APIのリージョン間転送等を含め、2時間/日で月5GB、常時1人閲覧で月40GB程度を保守的に置く。**20円/GBは料金断定ではなく余裕を持った予算枠**として加算し、通常前提は月100〜200円程度、余裕込み500円以内、常時1人は概ね月900円以内を見込む。SWA Freeの[帯域枠100GB/月](https://learn.microsoft.com/azure/static-web-apps/quotas)は本番と検証で共有される点に注意。利用者増/頻繁な再訪/多run/Account暗号化キーでは再計算する。

月1,500円の通常目標内を見込む。説明付き許容3,000円や予算通知を課金停止と扱わない。これは月次請求の実測ではない。実請求は公開後に要求/帯域/容量と照合する。[Azure帯域料金](https://azure.microsoft.com/pricing/details/bandwidth/)も適用時に再確認する。

検証環境は再閲覧・証拠照合のため残す。Worker停止後の保管だけならSWA FreeとTable約0.2GBで数円/月＋実際の閲覧/転送を見込む。今回の投入・照合・試験の一時要求/転送は別途数十円以内を想定（請求未確定）。確認後に専用RG・Entra app/SP・証明書を整理できるが、今回は削除していない。

## テスト・コマンド・保全

Worker系78件、API10件、Web16件、TypeScript/Vite本番ビルド、既存/履歴ローカルブラウザ回帰に成功。SDK/ランタイムの固定依存は変更していない。Table transaction上限100件/4MiBとSDK1000行ページ、[SWA APIの45秒制限](https://learn.microsoft.com/azure/static-web-apps/apis-overview)を確認し、日別要求とcursorで分割する。

主な再現入口は`scripts/history_test_environment.py setup/deploy`、`scripts/history_validation_data.py seed/bulk`、`web/tests/azure-history.mjs`。障害・cutover/full-compare/rollback補助はGit除外`.cache`に保全し、結果JSON・backup・manifest・ログ・画面も`.cache/history-azure`に保存。helperの形式/UTF-8/CLI探索設定の修正は本番コードやデータの不具合と混同しない。

## 本番で追加するものと残事項

本番にはHISTORY_READ_SASだけをAPIへ追加し、current限定SASを維持。API/Web先行→旧Worker互換確認→正常停止・新規Backup APIコピー/manifest/全検査→同じ版3DBで新Worker→配送確認・同run/resource/minute照合→各幅/復旧確認の順。本番操作は未実施。手順は[履歴適用・復旧](history-operations.md)。

未検証：実スマートフォン、長期の実請求/多数閲覧者、OS停電/実ディスクI/O障害/ディスク故障、任意の旧バージョンへのrollback。本番実データ量・実回線による初回同期所要時間は本番切替時に測定する。日次Blob/圧縮退避/安全なprune・自動起動登録は後続。これらを実施済みと読み替えない。
