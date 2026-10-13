#!/usr/bin/env python3
"""
check.py <slug> [--repo-name apps] - test an app folder the way GitHub Pages will serve it.

Serves the repo at /<repo-name>/ on localhost and, in headless Chromium:
  - loads the app and records console errors, page errors and failed requests
  - confirms the manifest parses and Chrome reports no installability errors
  - confirms the service worker takes control
  - goes offline, reloads, and confirms the app still renders
  - saves screenshots to /tmp/pwa-check-<slug>-{online,offline}.png
Exit code 0 = pass, 1 = fail.
"""
import argparse, functools, http.server, json, os, socketserver, sys, tempfile, threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def serve(repo_name):
    tmp = Path(tempfile.mkdtemp())
    (tmp / repo_name).symlink_to(ROOT, target_is_directory=True)
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a): pass
    handler = functools.partial(Quiet, directory=str(tmp))
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.ThreadingTCPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('slug')
    ap.add_argument('--repo-name', default='apps')
    a = ap.parse_args()
    if not (ROOT / a.slug / 'index.html').exists():
        sys.exit(f'{a.slug}/index.html not found')

    from playwright.sync_api import sync_playwright
    httpd, port = serve(a.repo_name)
    url = f'http://127.0.0.1:{port}/{a.repo_name}/{a.slug}/'
    problems, notes = [], []
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={'width': 390, 'height': 844}, device_scale_factor=2)
        page = ctx.new_page()
        console_errors, failed = [], []
        page.on('console', lambda m: m.type == 'error' and console_errors.append(m.text))
        page.on('pageerror', lambda e: console_errors.append('pageerror: ' + str(e)))
        page.on('requestfailed', lambda r: failed.append(f'{r.url} ({r.failure})'))
        page.on('response', lambda r: r.status >= 400 and failed.append(f'{r.url} (HTTP {r.status})'))

        page.goto(url, wait_until='load')
        page.wait_for_timeout(1500)

        cdp = ctx.new_cdp_session(page)
        man = cdp.send('Page.getAppManifest')
        if man.get('errors'):
            problems.append('manifest errors: ' + json.dumps(man['errors']))
        if not man.get('url'):
            problems.append('no manifest linked')
        inst = cdp.send('Page.getInstallabilityErrors').get('installabilityErrors', [])
        if inst:
            problems.append('installability: ' + json.dumps(inst))

        try:
            page.wait_for_function('navigator.serviceWorker && navigator.serviceWorker.ready.then(() => true)', timeout=10000)
            page.evaluate('navigator.serviceWorker.ready')
        except Exception as e:
            problems.append(f'service worker never became ready: {e}')
        page.reload(wait_until='load')
        page.wait_for_timeout(1000)
        controlled = page.evaluate('!!navigator.serviceWorker.controller')
        if not controlled:
            problems.append('service worker is not controlling the page after reload')
        page.screenshot(path=f'/tmp/pwa-check-{a.slug}-online.png')
        online_text = page.evaluate('document.body.innerText.trim().length')

        ctx.set_offline(True)
        failed_before = len(failed)
        try:
            page.reload(wait_until='load')
            page.wait_for_timeout(4000)  # let intro animations finish before judging the screen
            offline_text = page.evaluate('document.body.innerText.trim().length')
            page.screenshot(path=f'/tmp/pwa-check-{a.slug}-offline.png')
            if offline_text == 0 or offline_text < online_text * 0.5:
                problems.append(f'offline render looks empty (text length {offline_text} vs {online_text} online)')
            off_failed = failed[failed_before:]
            if off_failed:
                problems.append('requests failed offline: ' + '; '.join(off_failed))
        except Exception as e:
            problems.append(f'offline reload failed: {e}')
        ctx.set_offline(False)

        storage_keys = page.evaluate('Object.keys(localStorage)')
        bad_keys = [k for k in storage_keys if not k.startswith(a.slug)]
        if bad_keys:
            notes.append(f'localStorage keys without the "{a.slug}" prefix (collide across apps on this origin): {bad_keys}')

        if console_errors:
            problems.append('console errors: ' + ' | '.join(console_errors[:10]))
        online_failed = failed[:failed_before]
        if online_failed:
            problems.append('failed requests: ' + '; '.join(online_failed[:10]))
        b.close()
    httpd.shutdown()

    for n in notes: print('NOTE:', n)
    if problems:
        print('FAIL'); [print(' -', x) for x in problems]; sys.exit(1)
    print(f'PASS  manifest ok, installable, SW controlling, works offline  ({url})')

if __name__ == '__main__':
    main()
