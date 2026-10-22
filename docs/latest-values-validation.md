# 最新値先行公開の検証記録

版：0.3／2026-10-22。基準コミット：51ba06ce415374ca71e8b040e64cf3310a3a7d00。

最終デプロイ：`cc721d2b4b5573c92545dd1644d3feda1fad7328`、ブランチ`codex/stage1-local-worker`、SWA production/default。URL：**https://polite-plant-0eb01a400.3.azurestaticapps.net**。APIのbuild_commitとデプロイスクリプトの記録が一致。以後の検証記録の更新コミットはアプリ再デプロイを要しない文書変更として区別する。

## Alpha/Beta追加のローカル検証（今回・Azure未適用）

[Alpha/Beta検証記録](alpha-beta-validation.md)に今回の全結果を記載。旧公開版1と新公開版2の互換、両リソース原子的保存・公開、境界/停止/移行失敗、PC/320pxカードを確認した。DB版3を使い、旧版2案の配送台帳は実装していない。以下のAzure結果は**旧1リソース版の過去の実績**であり、今回の変更のデプロイ成功を示さない。今回運用Workerを停止・再起動しておらず、運用DB・Table・SWAの設定も変更していない。

## ローカルの確認済み結果

- Windows 11、Worker Python 3.13.16、API検証Python 3.11.17、Azure CLI 2.91.0。固定環境を使用。
- 既存32件＋同期・APIの7件＝Python 39テスト成功。通信失敗中の計算継続、回復後の最新値、ETag競合による遅延step／旧runの拒否、同世代のUUID衝突拒否、同stepの公開日時再更新防止、公開項目の制限を確認。
- Webの6テスト成功。即時初回取得、自動更新、要求の非重複、タブ復帰での即時取得と旧応答破棄、API障害からの自動復帰、step／世代の順序、浮動小数の微小差を下限制限と誤表示しないことを確認。
- API HTTPハンドラーの3テストをPython 3.11.17で別途実行し全件成功。200の公開項目制限とno-store、未作成404、設定・内部障害503の秘匿を確認。
- Web productionビルド成功。API固定依存はPython 3.11.17でimport成功。npm production依存監査0件。ビルド依存のViteも修正版8.3.5へ更新。
- 版1のpersistence.py・OHLC・outboxは変更なし。全確定足の未送信検査・count検査を維持。DB版2への移行は実行していない。

## Azureの構築済み内容

- Subscription：167018da-cd98-490d-9d4f-ecf252ff676a（Azure subscription 1）。開発者Ownerを確認。
- 今回専用RG：rg-resource-simulator（Japan East）。既存NetworkWatcherRGは変更なし。
- Standard LRS Storage：rsim16701820261022（Japan East）、非公開CurrentStateテーブル。
- Static Web Apps Free：resource-simulator-web（East Asia）。ソースブランチはcodex/stage1-local-worker。
- Worker用Entraアプリと証明書、CurrentStateだけのStorage Table Data Contributor。API用read-only・固定Entity・HTTPS SASをサーバー側設定。
- URL：https://polite-plant-0eb01a400.3.azurestaticapps.net
- PC→Storageの実読み取りで、同じrun・step=3997の一致を確認した（確認時点のサンプルであり固定データではない）。

## 公開後の確認

以下を**実Azure・公開URL**で確認した。ブラウザはChromium 141、390×844のモバイル幅。実スマートフォンの実機操作は行っていない。

| 検証 | 結果と範囲 |
| --- | --- |
| PC→Storage→API | 正常停止後のstep=21554でrun_id・run_generation・step・R・P・実増減量・計算日時が完全一致。StorageのサービスTimestampをAPIの公開日時に使用 |
| API→Web | step=21554、R=137.35927614068837（画面137.36）等、同じ保存状態と表示丸めの一致を確認。APIはHTTP 200・Cache-Control:no-store、build_commit=cc721d2… |
| 初回・自動更新 | 初回表示がタイマー待ちなしで成功し、前景でstepが自動増加。公開画面に操作ボタンなし、履歴未実装の文言あり |
| タブ復帰 | ブラウザ内でvisibilitychangeを再現し、復帰直後のAPI要求を確認。実端末OSでのタブ休止ではなくイベントによる再現試験 |
| API障害復帰 | ブラウザのAPI要求へ503を注入。最後の値を残して取得失敗を表示し、介入を解除すると公開APIから自動復帰。実Azureを停止した試験ではない |
| 停止中 | ローカルの停止ファイルで正常停止。APIのstep・published_atが不変で最終値を保持し、画面で約10秒後に更新停滞を表示。確認後にWorkerを再起動 |
| 遅延step・run切替 | 実Tableの隔離した検証Entityで、更新直前に別クライアントで新step／新世代を保存。旧ETagの更新をサービスが拒否し、読直し後も巻戻りなし。検証Entityは削除 |
| 通信断・計算継続 | ローカルの固定入力試験で通信例外を注入しても4ステップ保存し、回復後にstep=4のみを同期。実回線を物理切断する試験ではない |
| モバイル表示 | 390px幅で横スクロールなし、主要数値・日時・モデル・履歴未実装を確認。スクリーンショットを`.cache/validation/mobile-stopped.png`へ保存し目視確認 |
| 認証と非公開 | 同一REST要求が匿名では404（データ非公開）、SAS付きで200。証明書側で存在を確認した範囲外EntityはAPI用SASでは読めず、SASのsp=r・HTTPS・固定Partition/Row制限も確認。検証Entityは削除 |
| DB保持 | 読取り確認時点でuser_version=1、ローカル確定OHLC=379件・outbox=379件。送信・削除なし。既存32テストの復元検査を維持 |
| 予算 | 既存subscription予算1500yen、Monthly 1500円と75%通知が有効。設定変更なし。予算は課金停止機能ではない |

初回のSWA CLIデプロイではAPI依存のビルド省略によりAPIを確認できなかったため、Python v1形式の明示的HTTP定義とLinux CPython 3.11用wheelの梱包に修正し再デプロイした。WindowsのCLI絵文字出力もUTF-8へ修正。初回画面QAで浮動小数の微小差による下限制限の誤注記を修正した。上表はこれらを直した最終デプロイで再確認した結果。

## 再現コマンド

```powershell
uv run --locked python -m unittest discover -s tests -v
npm.cmd --prefix web ci
npm.cmd --prefix web test
npm.cmd --prefix web run build
api/.venv/Scripts/python.exe -m unittest discover -s api/tests -v
uv run --locked python scripts/verify_azure.py
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path (Get-Location) '.cache/pw-browsers'
node web/tests/browser.mjs https://polite-plant-0eb01a400.3.azurestaticapps.net
```

AC-07は最新値同期部分、AC-16は最新値の巻戻り防止、AC-19〜20は自動取得部分が対象。OHLC再送・履歴API・期間選択・グラフ・退避・料金実測の月次評価は後続。実スマートフォンの実機操作は未実施。

AC-05/06はブラウザと計算の分離・停止中の閲覧部分、AC-17はモバイル幅の最新値閲覧部分、AC-18は今回のGit対象・静的配布物の公開項目制限を確認。履歴が必要なAC全体の合格とは扱わない。

残作業：実スマートフォンと実際のOSタブ休止、物理的な通信断・停電、月次料金実測、タスクスケジューラでの起動時常駐、SAS（90日）と証明書（1年）の更新運用、OHLCクラウド同期・DB版2移行・日次Blob・退避・履歴UI。実機検証後のWorkerは現在ユーザーで常駐中だが、PC再起動後の自動起動は未登録。
