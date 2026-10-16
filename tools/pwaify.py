#!/usr/bin/env python3
"""
pwaify.py - turn a single-page HTML app into an installable PWA folder in this repo.

Usage (from the repo root):
  python3 tools/pwaify.py --src app.html --slug so-fast --name "So Fast" \
      [--short-name "So Fast"] [--theme "#FFFFFF"] [--bg "#FFFFFF"] \
      [--icon-html icon.html | --icon-text "SF" --icon-fg "#fff" --icon-bg "#1A73E8"] \
      [--assets dir]   # extra files (fonts, images, libs) copied into the app folder

What it does:
  1. Extracts the real document if the HTML is wrapped (e.g. a claude.ai artifact export).
  2. Refuses (exit 2) if the page still loads anything from another origin, because
     that breaks offline use. Bundle those files with --assets and point the HTML at them.
  3. Writes <slug>/index.html with manifest, icons, iOS tags and service-worker registration.
  4. Renders icons (any + maskable + apple-touch + favicon) with headless Chromium.
  5. Writes manifest.webmanifest and a versioned sw.js scoped to the app folder.
  6. Updates apps.json and the root index.html (the launcher page listing every app).
Then run tools/check.py <slug> before committing.
"""
import argparse, hashlib, html, json, os, re, shutil, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ---------- HTML helpers ----------
def extract_document(src: str) -> str:
    """claude.ai artifact exports wrap the app in a second <html> skeleton. Keep the inner doc."""
    starts = [m.start() for m in re.finditer(r'<!doctype html', src, re.I)]
    if len(starts) >= 2:
        inner = src[starts[1]:]
        end = inner.lower().rfind('</html>')
        # the wrapper's own </html> is the last one; the app's is the one before it
        ends = [m.end() for m in re.finditer(r'</html>', inner, re.I)]
        if len(ends) >= 2:
            return inner[:ends[-2]]
        return inner[:end + 7] if end != -1 else inner
    return src

EXTERNAL_RE = re.compile(
    r'''<(?:script|link|img|source|audio|video|iframe)\b[^>]*?\b(?:src|href)\s*=\s*["']((?:https?:)?//[^"']+)["']''',
    re.I)
CSS_URL_RE = re.compile(r'''(?:url\(\s*["']?|@import\s+["'])((?:https?:)?//[^"')\s]+)''', re.I)

def external_refs(doc: str):
    refs = set(EXTERNAL_RE.findall(doc)) | set(CSS_URL_RE.findall(doc))
    # preconnect hints are harmless but pointless offline; report them separately
    return sorted(refs)

def strip_existing_pwa_tags(doc: str) -> str:
    doc = re.sub(r'<link[^>]+rel=["\']manifest["\'][^>]*>\s*', '', doc, flags=re.I)
    doc = re.sub(r'<link[^>]+rel=["\'](?:apple-touch-icon|icon|shortcut icon)["\'][^>]*>\s*', '', doc, flags=re.I)
    doc = re.sub(r'<meta[^>]+name=["\'](?:theme-color|apple-mobile-web-app-[^"\']+|mobile-web-app-capable)["\'][^>]*>\s*', '', doc, flags=re.I)
    doc = re.sub(r'<link[^>]+rel=["\']preconnect["\'][^>]*>\s*', '', doc, flags=re.I)
    doc = re.sub(r'<!-- pwa:head -->.*?<!-- /pwa:head -->\n?', '', doc, flags=re.S)
    doc = re.sub(r'<script>/\* PWA: service worker registration \(added by tools/pwaify.py\) \*/.*?</script>\n?', '', doc, flags=re.S)
    return doc

def inject(doc: str, name: str, theme: str) -> str:
    head_tags = f'''<!-- pwa:head -->
<link rel="manifest" href="manifest.webmanifest">
<meta name="theme-color" content="{html.escape(theme)}">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="{html.escape(name)}">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<link rel="apple-touch-icon" href="icons/apple-touch-icon.png">
<link rel="icon" type="image/png" sizes="32x32" href="icons/favicon-32.png">
<!-- /pwa:head -->
'''
    sw_tag = '''<script>/* PWA: service worker registration (added by tools/pwaify.py) */
if ('serviceWorker' in navigator) {
  window.addEventListener('load', function () {
    navigator.serviceWorker.register('sw.js').then(function (reg) {
      reg.update && reg.update();
    }).catch(function () {});
  });
}
</script>
'''
    if not re.search(r'<meta[^>]+name=["\']viewport', doc, re.I):
        head_tags = '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">' + head_tags
    m = re.search(r'</head>', doc, re.I)
    if not m:
        raise SystemExit('No </head> found - is this a full HTML document?')
    doc = doc[:m.start()] + head_tags + doc[m.start():]
    idx = doc.lower().rfind('</body>')
    if idx == -1:
        doc = doc + sw_tag
    else:
        doc = doc[:idx] + sw_tag + doc[idx:]
    return doc

# ---------- icons ----------
def default_icon_html(text, fg, bg):
    return f'''<!doctype html><html><head><style>
html,body{{margin:0;width:100%;height:100%}}
body{{display:grid;place-items:center;background:{bg};color:{fg};
font:800 38vw/1 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;letter-spacing:-.02em}}
</style></head><body>{html.escape(text)}</body></html>'''

def render_icons(icon_html_path: Path, out_dir: Path, bg: str):
    from playwright.sync_api import sync_playwright
    out_dir.mkdir(parents=True, exist_ok=True)
    url = icon_html_path.resolve().as_uri()
    with sync_playwright() as p:
        b = p.chromium.launch()
        def shot(size, name, maskable=False):
            pg = b.new_page(viewport={'width': size, 'height': size}, device_scale_factor=1)
            pg.goto(url)
            pg.wait_for_timeout(300)
            if maskable:
                # maskable icons get cropped to as little as the central 80% circle:
                # draw the artwork at 76% on a full-bleed background
                pg.add_style_tag(content=f'''html{{background:{bg}}} body{{transform:scale(.76);transform-origin:50% 50%}}''')
                pg.wait_for_timeout(100)
            pg.screenshot(path=str(out_dir / name), omit_background=False)
            pg.close()
        shot(512, 'icon-512.png')
        shot(192, 'icon-192.png')
        shot(512, 'maskable-512.png', maskable=True)
        shot(192, 'maskable-192.png', maskable=True)
        shot(180, 'apple-touch-icon.png', maskable=True)  # iOS rounds corners itself; keep content inset
        shot(32, 'favicon-32.png')
        b.close()

# ---------- service worker ----------
SW_TEMPLATE = r'''/* Service worker for {name} - generated by tools/pwaify.py. Do not edit by hand; re-run the tool. */
const APP = {slug_json};
const VERSION = {version_json};
const CACHE = APP + '-' + VERSION;          // every app on this origin gets its own cache prefix
const PRECACHE = {files_json};

self.addEventListener('install', e => {{
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(PRECACHE)).then(() => self.skipWaiting()));
}});

self.addEventListener('activate', e => {{
  e.waitUntil((async () => {{
    for (const k of await caches.keys()) if (k.startsWith(APP + '-') && k !== CACHE) await caches.delete(k);
    await self.clients.claim();
  }})());
}});

self.addEventListener('fetch', e => {{
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;
  const scope = new URL(self.registration.scope);
  if (!url.pathname.startsWith(scope.pathname)) return;

  if (req.mode === 'navigate') {{
    // network first (so updates show up as soon as you are online), cache fallback for offline
    e.respondWith((async () => {{
      const cache = await caches.open(CACHE);
      try {{
        const ctrl = new AbortController();
        const t = setTimeout(() => ctrl.abort(), 4000);
        const res = await fetch(req, {{ signal: ctrl.signal, cache: 'no-store' }});
        clearTimeout(t);
        if (res.ok) cache.put('./', res.clone());
        return res;
      }} catch (err) {{
        return (await cache.match('./')) || (await cache.match('index.html')) || Response.error();
      }}
    }})());
    return;
  }}
  // everything else: cache first, then network (and remember it)
  e.respondWith((async () => {{
    const cache = await caches.open(CACHE);
    const hit = await cache.match(req, {{ ignoreSearch: true }});
    if (hit) return hit;
    const res = await fetch(req);
    if (res.ok) cache.put(req, res.clone());
    return res;
  }})());
}});
'''

def write_sw(app_dir: Path, slug: str, name: str):
    files = sorted(p.relative_to(app_dir).as_posix() for p in app_dir.rglob('*')
                   if p.is_file() and p.name != 'sw.js' and not p.name.startswith('.') and p.name != 'icon-source.html')
    h = hashlib.sha256()
    for f in files:
        h.update(f.encode()); h.update((app_dir / f).read_bytes())
    version = h.hexdigest()[:10]
    precache = ['./'] + files
    (app_dir / 'sw.js').write_text(SW_TEMPLATE.format(
        name=name, slug_json=json.dumps(slug), version_json=json.dumps(version),
        files_json=json.dumps(precache, indent=2)))
    return version

# ---------- launcher page ----------
def update_registry(slug, name, description):
    reg_path = ROOT / 'apps.json'
    reg = json.loads(reg_path.read_text()) if reg_path.exists() else []
    reg = [a for a in reg if a['slug'] != slug]
    reg.append({'slug': slug, 'name': name, 'description': description})
    reg.sort(key=lambda a: a['name'].lower())
    reg_path.write_text(json.dumps(reg, indent=2) + '\n')
    cards = '\n'.join(
        f'''    <a class="app" href="{a['slug']}/"><img src="{a['slug']}/icons/icon-192.png" alt="" width="64" height="64"><span><b>{html.escape(a['name'])}</b><small>{html.escape(a.get('description') or '')}</small></span></a>'''
        for a in reg)
    (ROOT / 'index.html').write_text(f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Apps</title>
<style>
:root{{color-scheme:light dark;--bg:#fafafa;--card:#fff;--ink:#1d1d1f;--soft:#6e6e73;--line:#e5e5ea}}
@media (prefers-color-scheme:dark){{:root{{--bg:#111;--card:#1c1c1e;--ink:#f5f5f7;--soft:#a1a1a6;--line:#2c2c2e}}}}
body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.4 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}}
main{{max-width:640px;margin:0 auto;padding:32px 16px}}
h1{{font-size:28px;margin:0 0 20px}}
.app{{display:flex;gap:14px;align-items:center;padding:12px;margin-bottom:10px;background:var(--card);border:1px solid var(--line);border-radius:16px;color:inherit;text-decoration:none}}
.app img{{border-radius:14px;flex:none}}
.app span{{display:flex;flex-direction:column}} .app small{{color:var(--soft)}}
</style></head><body><main>
<h1>Apps</h1>
{cards}
</main></body></html>
''')
    (ROOT / '.nojekyll').touch()

# ---------- main ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', help='HTML to convert. Omit to rebuild an existing app from its deployed index.html')
    ap.add_argument('--slug', required=True)
    ap.add_argument('--name', required=True)
    ap.add_argument('--short-name')
    ap.add_argument('--description', default='')
    ap.add_argument('--theme', default='#FFFFFF')
    ap.add_argument('--bg', default='#FFFFFF')
    ap.add_argument('--icon-html')
    ap.add_argument('--icon-text')
    ap.add_argument('--icon-fg', default='#FFFFFF')
    ap.add_argument('--icon-bg', default='#1A73E8')
    ap.add_argument('--assets', help='folder whose contents are copied into the app folder')
    ap.add_argument('--orientation', default='any')
    ap.add_argument('--allow-external', action='store_true', help='skip the offline check (not recommended)')
    a = ap.parse_args()

    if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', a.slug):
        raise SystemExit('slug must be lowercase letters, digits and dashes')
    if a.slug in ('tools', 'icons'):
        raise SystemExit('reserved slug')

    app_dir = ROOT / a.slug
    stash = None
    if app_dir.exists():
        # keep the existing app's bundled assets and icon unless replacements are given
        stash = Path(tempfile.mkdtemp()) / 'prev'
        shutil.copytree(app_dir, stash)
    if not a.src:
        if not stash: raise SystemExit('--src is required for a new app')
        a.src = str(stash / 'index.html')
    doc = extract_document(Path(a.src).read_text(encoding='utf-8'))
    doc = strip_existing_pwa_tags(doc)
    ext = external_refs(doc)
    if ext and not a.allow_external:
        print('EXTERNAL RESOURCES (will break offline) - bundle them and re-run:', file=sys.stderr)
        for u in ext: print('  ' + u, file=sys.stderr)
        sys.exit(2)

    if app_dir.exists():
        shutil.rmtree(app_dir)
    app_dir.mkdir()
    GENERATED = {'index.html', 'manifest.webmanifest', 'sw.js', 'icon-source.html', 'icons'}
    if stash:
        for item in stash.iterdir():
            if item.name in GENERATED: continue
            (shutil.copytree if item.is_dir() else shutil.copy2)(item, app_dir / item.name)
    if a.assets:
        shutil.copytree(a.assets, app_dir, dirs_exist_ok=True)
    (app_dir / 'index.html').write_text(inject(doc, a.short_name or a.name, a.theme), encoding='utf-8')

    # icons
    tmp = Path(tempfile.mkdtemp())
    if a.icon_html:
        icon_src = Path(a.icon_html).resolve()
        # the app folder was just recreated; an icon given from inside it lives in the stash now
        if stash and icon_src.is_relative_to(app_dir.resolve()):
            icon_src = stash / icon_src.relative_to(app_dir.resolve())
    elif stash and (stash / 'icon-source.html').exists() and not a.icon_text:
        icon_src = stash / 'icon-source.html'
    else:
        icon_src = tmp / 'icon.html'
        icon_src.write_text(default_icon_html(a.icon_text or a.name[:1].upper(), a.icon_fg, a.icon_bg))
    # keep the icon source with the app so future edits can re-render it
    shutil.copy(icon_src, app_dir / 'icon-source.html')
    render_icons(app_dir / 'icon-source.html', app_dir / 'icons', a.icon_bg if not a.icon_html else a.bg)

    manifest = {
        'id': './',
        'name': a.name,
        'short_name': (a.short_name or a.name)[:12],
        'description': a.description,
        'start_url': './',
        'scope': './',
        'display': 'standalone',
        'orientation': a.orientation,
        'background_color': a.bg,
        'theme_color': a.theme,
        'icons': [
            {'src': 'icons/icon-192.png', 'sizes': '192x192', 'type': 'image/png', 'purpose': 'any'},
            {'src': 'icons/icon-512.png', 'sizes': '512x512', 'type': 'image/png', 'purpose': 'any'},
            {'src': 'icons/maskable-192.png', 'sizes': '192x192', 'type': 'image/png', 'purpose': 'maskable'},
            {'src': 'icons/maskable-512.png', 'sizes': '512x512', 'type': 'image/png', 'purpose': 'maskable'},
        ],
    }
    (app_dir / 'manifest.webmanifest').write_text(json.dumps(manifest, indent=2) + '\n')
    version = write_sw(app_dir, a.slug, a.name)
    update_registry(a.slug, a.name, a.description)
    print(f'built {a.slug}/ (sw version {version})')

if __name__ == '__main__':
    main()
