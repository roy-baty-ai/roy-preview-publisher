# グリーンワークみやこ — private sales sample

非公式・私的な営業見本。公開・マージ・メール送信は未承認。外部送信や保存を行わない問い合わせデモのみ。変更はこのディレクトリ内に限定。

## Sources and scope

- https://jmty.jp/fukuoka/ser-gard/article-1r6ln7
- https://jmty.jp/profiles/6740808c4c0ac80a7c38cd7d
- Checked 2026-10-13: direct article retrieval failed in web tool; profile retrieved successfully and includes the listing text.
- Use only the delegated verified scope: small mowing/weeding, trees around 2–3 m, eastern Fukuoka, mainly weekends/holidays, trade name and public email. No phone, legal name, prices, disposal terms, testimonials, or claimed results.

## Generated images

Both images downloaded using the current official Library transfer helper, visually inspected, then converted to WebP (quality 85). Original bytes retain Library metadata under `/workspace/source-images/`.

| asset | Library ID | source file ID | dimensions | WebP bytes |
| --- | --- | --- | --- | --- |
| assets/hero.webp | libfile_bb7c077ee3848191a065e0d845272bc4 | file_000000002ff481f5928bae6db33909bd | 1672×941 | 386088 |
| assets/detail.webp | libfile_99e6a6bed130819191109c4b35862c9c | file_00000000c32081f5b378a2bfabb22d00 | 1448×1086 | 346776 |

Images are labeled generated illustrations, not actual work or portfolio photos.

## QA

Chromium via Playwright, 2026-10-13. All checks passed at 1440 / 393 / 320 px:

- No horizontal page overflow; both images loaded; full-page screenshots visually inspected.
- All three demo openers work; initial focus and Tab/Shift+Tab containment work.
- Escape and close button dismiss dialog and restore opener focus.
- Submission shows demo confirmation, makes no external requests, and uses no local/session storage.
- Closing/reopening clears entered text. No browser runtime errors.
- `git diff --check` passes.

`qa/check.cjs` is the executed environment-specific check; `qa/results.json` contains its results. Inspection screenshots remain in `/workspace/greenwork-qa/` and are not published.

Base preview-content: c81a8aac5a3a9dab199d0155a40bfae0ecb8dd74.
