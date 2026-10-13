# apps — PWA hosting repo

Each folder is one installable PWA served by GitHub Pages at
`https://myquickapp.github.io/apps/<slug>/`. The root `index.html` lists every app
(generated from `apps.json` — never edit either by hand).

## Launching or updating an app
1. Get a single self-contained HTML file (inline CSS/JS). Anything loaded from another
   origin (CDN scripts, Google Fonts, remote images) must be bundled locally: get the
   files from npm (`npm i <pkg>@<exact version>` in a scratch dir; e.g. `@fontsource/*`
   for Google Fonts, the library's own package for CDN scripts), put them in an assets
   folder, and point the HTML at relative paths.
2. localStorage / IndexedDB / cache names must be prefixed with the slug — every app
   shares one origin.
3. `python3 tools/pwaify.py --src <html> --slug <slug> --name "<Name>" --description "..." \
    [--icon-html <icon.html> | --icon-text "X" --icon-fg "#fff" --icon-bg "#hex"] \
    [--assets <dir>] [--theme "#hex"] [--bg "#hex"] [--orientation portrait]`
   The tool exits 2 and lists URLs if external resources remain.
   Re-running for an existing slug rebuilds it; the service worker version changes
   automatically so installed copies update on next open.
4. `python3 tools/check.py <slug>` must print PASS. Look at
   `/tmp/pwa-check-<slug>-offline.png` and the icons before shipping.
5. Commit and push to `main`. Pages deploys in ~1 minute.
6. Verify with WebFetch on the live URL (the shell cannot reach *.github.io).

Slugs: lowercase, digits, dashes. `tools` is reserved. This repo is public.
