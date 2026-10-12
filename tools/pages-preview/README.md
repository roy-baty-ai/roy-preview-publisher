# Preview Publisher — 準備版・非稼働

専用 public repository `roy-baty-ai/roy-preview-publisher` に置く公開処理です。
既存 Cloudflare Pages Direct Upload project `roy-web-sales-previews` の
`glass-green` / `reamertec` のみを対象とし、native Git 接続や Workers へ移行しません。
`workflow.yml.example` は非稼働の提案です。token 設定・保護設定・Actions 実行・deploy は別途有効化するまで行いません。

## 信頼境界

- ユーザーと許可済み owner 実行系を、信頼する管理者とします。同じ owner ログインは独立した承認者ではありません。
- 成果物、外部の公開PR、fork、任意 branch 入力は信頼しません。PRのコードを秘密情報付きjobで実行しません。
- workflow、uploader、依存lock、テストを含む全実行可能ファイルは専用repoの main ルール下で管理します。
  実行は固定repo・main・workflow_dispatchのみ。成果物側のスクリプトや設定を実行しません。
- owner による意図的な workflow 書換え、保護ルール無効化、同一ownerの権限侵害までは防ぎません。
  新たな管理者や毎回の人間承認を導入して独立性があるようには扱いません。
- Cloudflare token は対象アカウントの **Pages Edit（Pages作成・変更・削除、Productionを含む）**。
  Preview専用権限・project限定権限ではありません。対象制限はこのプログラム側の検査です。

## 公開コンテンツの受け渡し

同じ専用public repoの固定 `preview-content` branch に、公開許可済みの完成した静的ファイルだけをコミットします。
固定パスは `preview-assets/glass-green/` と `preview-assets/reamertec/`、各直下に `index.html`。
**GitHub上でも誰でも読める内容だけ**を入れます。private repoの履歴、台帳、メール、内部方針、個人記録は持ち込みません。
private repoを読むPATやcross-repository credentialは不要です。

入力は許可Preview名と40桁commit SHAだけ。URL、ZIP、任意パス・コマンド・外部repoは受け取りません。
SHAがcheckout済みの `origin/preview-content` の履歴にあることを確認し、固定パスのGit blobを読みます。
公開PRそのもの・PRのコードをcheckoutして実行せず、信頼するownerが受け入れた静的成果物をデータとして扱います。
過去版を指定できるため、公開の許可範囲・版の選択は信頼する実行者の責任です。

通常ファイルのみ、最大2,000ファイル・各25MiB・合計100MiB。
symlink/submodule/実行可能mode、隠しファイル、`functions`、`_worker.js`、`wrangler.*`、
`package.json`、任意`_headers`/`_redirects`、未知拡張子、不正パスを拒否します。
Git LFSではなく画像本体を格納します。信頼側がnoindex/nofollow/noarchiveヘッダーを追加し全ファイルSHA-256を検査します。
ブラウザ用JSはCIで実行しません。公開ページの内容・リンク・非公式提案表示・画像権利・PC/mobile QAは別途確認します。

## 実行と確認

1. `workflow_dispatch` のみ。固定repository/mainチェックはworkflowとPythonの両方で行います。
   checkoutはdispatch時のmain commitに固定し、Git認証を残しません。
2. Cloudflare APIでproject名・subdomain・Git未接続・実 `production_branch` を検査。
   空/未知branch・Production一致・Production値欠落・API失敗で停止。Productionを`main`等と仮定しません。
3. `canonical_deployment`、Productionの全deployment履歴、`deployment_configs.production`オブジェクトを必須確認。
   Production未デプロイのみ許可し、既存Productionや設定欠落/null/不正型はupload前に拒否。
   直前・直後にbranch/canonical/全履歴/設定を比較します。
4. ロックファイル固定のWrangler **4.149.0**を使用。秘密なしで`npm ci --ignore-scripts`。
   最後の信頼できるuploader stepにのみCloudflare secretを渡し、リポジトリ外の新しい一時場所で固定`pages deploy`を実行。
   サイトのbuild/hooks/任意シェルは実行しません。子環境は最小限、GitHub token/NODE_OPTIONSは継承しません。
   `RUNNER_TRACKING_ID`だけはrunnerによる異常終了時のprocess cleanupのため保持します。
5. Linux supervisorが独立session/process groupで起動し、subreaperとして孤立した子孫を引き取ります。
   timeout、SIGINT/SIGTERM、wrapperだけの終了時にもTERM→KILLし、別sessionに移った子孫も回収。
   **全子孫の終了を確認→Production事後検証→ロック解除**の順序を守ります。
   wrapperの成功後に子孫が残った場合も成功にしません。終了できなければ待ち続け、ロックを先に解放しません。
6. Actions concurrencyは全対象共通、`cancel-in-progress: false`。同一ホストでは追加のflock。
   GitHub pending runの置換があり得るため、全要求の配送やFIFO順序は保証しません。
7. APIで新deployment・run固有marker・commit SHA・branch・`environment=preview`・deploy successを検査。
   固定projectの一意deployment URLに加え、API応答`aliases`に正規の
   `https://<branch>.roy-web-sales-previews.pages.dev`があることを必須確認。
   **両方のURL**を認証・redirectなしでGETし、HTTP200と`index.html`のSHA-256一致を検証します。
   alias欠落/別branch/旧版/誤内容は成功にしません。成功結果に一意URLと`alias_url`を返します。
8. Production不変を再確認し、限定した成功情報のみを出力。API本文・例外本文・Wrangler出力/debug logは公開しません。

別の管理者・Cloudflare dashboard・外部uploaderがproduction_branchを検査とuploadの間に変更する競合は残ります。
このworkflowの排他は外部操作を止めません。APIには原子的なPreview限定条件付きuploadがないため、絶対安全とは言えません。
同じprojectの他経路の公開・設定変更を同時に行わない運用が必要です。

失敗・timeout・job取消・事後API/URL検証失敗は「公開状態未確定」として扱います。
自動再deploy/delete/rollbackはしません。SIGKILL・runner喪失ではプロセス終了や事後確認を完了できない場合があります。
runnerのtrackingは補助であり保証ではありません。当該run markerとProductionを読取確認してから判断します。

## 将来の一度だけの設定（この準備では変更しない）

GitHub Freeのpublic repoではbranch protection/environmentを利用できますが、
**そのrepoで実際にルールが適用・強制されることを確認するまで有効化不可**です。
設定画面に存在するだけでは保護済みとしません。課金upgradeや既存private repoの公開化は不要です。

1. 専用repoのfresh履歴へ、このコードと公開許可済みの静的資材だけを配置。
   初回導入の承認後、雛形を `.github/workflows/pages-preview.yml` としてmainに配置します。
   dispatchはdefault branchにworkflowが必要です。準備段階では`.example`のままにします。
2. Settingsのbranch rulesでmainとpreview-contentにforce push禁止・削除禁止を適用。
   mainの変更はPR経由とし、全実行ファイル・依存lock・workflowを例外にしない運用を採用。
   毎回の独立reviewer必須にはせず、信頼するownerの受入れを用います。
   個人repoにorganization専用の「Restrict who can push」設定があるとは仮定しません。
   管理者にも適用する設定と実際の強制結果を確認。ただしowner自身の設定変更は阻止できません。
   新しい管理者を招待しません。
3. Settings → Environments → `cloudflare-preview-publish`を作成。
   Selected branches and tags → **Branch `main`のみ**（tagなし）。毎回のrequired reviewerは追加しません。
   効力確認前にsecretを保存しません。公開PR/fork/別branchのjobにこのEnvironmentを許可しません。
4. 本人がCloudflare API Tokens → Custom Tokenで対象1アカウントの **Account / Cloudflare Pages / Edit** を作成。
   本人が上記GitHub Environment secret **`CLOUDFLARE_API_TOKEN`**へ直接保存。
   Environment variable **`CLOUDFLARE_ACCOUNT_ID`**も設定。tokenをチャット・サイト・repoに貼らない。
   tokenを失効/期限切れにした場合の更新も本人が行います。
5. 実行者は許可範囲内で、Preview名と公開資材commit SHAを選び、workflow ref=mainでdispatchします。
   本人のMac操作や毎回のtoken再入力は不要です。実行・runner利用の許可は有効化前に確認します。

この文書は設定提案であり、repo作成・保護適用・権限付与・token保存・実行済みという記録ではありません。

## ローカル検証

```sh
python3 -m unittest discover -s tools/pages-preview -v
npm ci --prefix tools/pages-preview --ignore-scripts --no-audit --no-fund
```

テストは偽token・APIモック・一時Git・ネットワークなしの実プロセスfixtureを使います。
実Cloudflare API/schema/null挙動、実権限、live CDN、runner cancellationは未検証です。
`publisher.py publish`は実操作なのでテストとして呼びません。

## 公式資料

- [Direct Upload CI](https://developers.cloudflare.com/pages/how-to/use-direct-upload-with-continuous-integration/)
- [Direct Upload](https://developers.cloudflare.com/pages/get-started/direct-upload/)
- [Wrangler pages deploy](https://developers.cloudflare.com/workers/wrangler/commands/pages/#pages-deploy)
- [Project API](https://developers.cloudflare.com/api/resources/pages/subresources/projects/methods/get/)
- [Deployment API](https://developers.cloudflare.com/api/resources/pages/subresources/projects/subresources/deployments/methods/list/)
- [GitHub branch protection](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)
- [GitHub environments](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments)
- [GitHub manual workflow](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)
- [Python subprocess](https://docs.python.org/3/library/subprocess.html)
