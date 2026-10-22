# 最新値先行公開の検証記録

版：0.1／2026-10-22。基準コミット：51ba06ce415374ca71e8b040e64cf3310a3a7d00。

## ローカルの確認済み結果

- Windows 11、Worker Python 3.13.16、API検証Python 3.11.17、Azure CLI 2.91.0。固定環境を使用。
- 既存32件＋同期・APIの7件＝Python 39テスト成功。通信失敗中の計算継続、回復後の最新値、ETag競合による遅延step／旧runの拒否、同世代のUUID衝突拒否、同stepの公開日時再更新防止、公開項目の制限を確認。
- Webの5テスト成功。即時初回取得、自動更新、要求の非重複、タブ復帰での即時取得と旧応答破棄、API障害からの自動復帰、step／世代の順序を確認。
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

この節はデプロイ後の実測結果で更新する。公開画面・APIの一致、停止時の保持・停滞表示、モバイル幅、ブラウザの復帰動作はまだこの記録では合格扱いにしない。

## 再現コマンド

```powershell
uv run --locked python -m unittest discover -s tests -v
npm.cmd --prefix web ci
npm.cmd --prefix web test
npm.cmd --prefix web run build
```

AC-07は最新値同期部分、AC-16は最新値の巻戻り防止、AC-19〜20は自動取得部分が対象。OHLC再送・履歴API・期間選択・グラフ・退避・料金実測の月次評価は後続。実スマートフォンの実機操作は未実施。
