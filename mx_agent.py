# -*- coding: utf-8 -*-
"""
MetStat — دانلودکنندهٔ مرورگری نقشه‌های meteologix برای «تحلیل نقشه‌های آنلاین»

چرا؟ سرور meteologix (Akamai) درخواست‌های مستقیم هاست ایران و حتی Cloudflare را با «Access Denied» رد می‌کند.
این برنامه یک مرورگر واقعی (Chromium/Chrome) باز می‌کند، صفحهٔ هر نقشه را مثل یک کاربر می‌بیند، تصویر PNG همان نقشه را
از داخل همان مرورگر می‌گیرد (همان کاری که دکمهٔ دانلود تصویر انجام می‌دهد) و آن را با HTTPS به هاست شما
(online/mx_upload.php) می‌فرستد. سایت نقشه‌ها را از پوشهٔ هر اجرا/منطقه/پارامتر نشان می‌دهد.

اجرا روی کامپیوتر خودتان (ویندوز/لینوکس/مک):
    pip install playwright requests
    python -m playwright install chromium
    python mx_agent.py --host https://wtafkik.ir/online --key کلید_آپلود            ← یک نوبت کامل
    python mx_agent.py --host https://wtafkik.ir/online --key کلید_آپلود --loop     ← همیشه روشن: هر اجرای 00Z و 12Z را خودکار می‌گیرد
    گزینه‌ها:  --show (مرورگر دیده شود)   --chrome (به‌جای Chromium از Google Chrome نصب‌شده استفاده شود)   --test (فقط آزمون آدرس‌ها)

اجرا در GitHub Actions: فایل .github/workflows/metstat.yml (راهنما در README_fa.txt).
"""
import argparse, json, os, random, re, sys, time
from datetime import datetime, timedelta, timezone
import requests
from playwright.sync_api import sync_playwright

UTC = timezone.utc
OG = re.compile(r'https?://(img\d*\.meteologix\.com)/images/data/cache/model/complete_model_([a-z0-9]+)_(\d{10})_(\d+)_(\d+)_(\d+)\.png', re.I)

def log(s): print(datetime.now(UTC).strftime('%H:%M:%S ') + s, flush=True)

class Host:
    def __init__(s, base, key): s.base, s.key = base.rstrip('/'), key
    def call(s, a, js=None, files=None, data=None):
        u = f"{s.base}/mx_upload.php?a={a}&key={requests.utils.quote(s.key)}"
        for k in range(4):
            try:
                r = requests.post(u, json=js, files=files, data=data, timeout=60)
                if r.status_code == 403: sys.exit("✗ کلید آپلود اشتباه است (upload_key در mx_config.json)")
                return r.json()
            except (requests.RequestException, ValueError) as e:
                log(f"  … خطای ارتباط با هاست ({e}); تلاش دوباره"); time.sleep(5 * (k + 1))
        return {}

def run_start(C, run):
    h, m = map(int, C['download_start_utc'][run[8:10]].split(':'))
    return datetime(int(run[:4]), int(run[4:6]), int(run[6:8]), tzinfo=UTC) + timedelta(hours=h, minutes=m)

def active_run(C, now):
    d0 = datetime(now.year, now.month, now.day, tzinfo=UTC)
    for k in range(3):
        for hh in ('12', '00'):
            r = (d0 - timedelta(days=k)).strftime('%Y%m%d') + hh
            if run_start(C, r) <= now: return r
    return None

def next_run(C, now):
    for d in (0, 1):
        for hh in ('00', '12'):
            r = (now + timedelta(days=d)).strftime('%Y%m%d') + hh
            if run_start(C, r) > now: return r

def plan(C, run):
    t0 = datetime(int(run[:4]), int(run[4:6]), int(run[6:8]), int(run[8:10]), tzinfo=UTC); items = []
    for reg in C['regions']:
        for P in C['params']:
            for mod in P['models']:
                if P['step'] == 24:
                    d = datetime(t0.year, t0.month, t0.day, tzinfo=UTC)
                    steps = [int(((d + timedelta(days=k, hours=P.get('valid_hour', 6))) - t0).total_seconds() // 3600) for k in (1, 2, 3)]
                else:
                    steps = list(range(P['step'], C['horizon_h'] + 1, P['step']))
                for s in steps:
                    v = (t0 + timedelta(hours=s)).strftime('%Y%m%d%H')
                    items.append({'r': reg, 'm': mod, 'p': P['id'], 's': s, 'v': v, 'file': f"{reg}/{mod}_{P['id']}_{v}.png"})
    return items

class Browser:
    def __init__(s, pw, show, chrome):
        args = dict(headless=not show, args=['--disable-blink-features=AutomationControlled'])
        if chrome: args['channel'] = 'chrome'
        s.b = pw.chromium.launch(**args)
        s.ctx = s.b.new_context(locale='en-US', viewport={'width': 1366, 'height': 900},
                                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36')
        s.ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        s.page = s.ctx.new_page(); s.warm = False
        if os.environ.get('MX_MOCK'):   # فقط برای آزمون محلی
            png = open(os.environ['MX_MOCK'], 'rb').read()
            def fake(route):
                u = route.request.url
                if u.endswith('.png'): return route.fulfill(status=200, body=png, content_type='image/png')
                mm = re.search(r'/model-charts/([a-z]+)/(?:\d{10}/)?([a-z\-]+)/', u)
                return route.fulfill(status=200, content_type='text/html', body=f'<meta property="og:image" content="https://img3.meteologix.com/images/data/cache/model/complete_model_ec_2026100800_6_123_45.png">')
            s.ctx.route('**/*meteologix.com/**', fake)
    def html(s, url):
        r = s.page.goto(url, wait_until='domcontentloaded', timeout=60000)
        try: s.page.wait_for_timeout(1500)
        except Exception: pass
        return (r.status if r else 0), s.page.content()
    def png(s, url):
        """تصویر را خود مرورگر باز می‌کند (همان اثر «ذخیرهٔ تصویر»)."""
        try:
            r = s.page.goto(url, wait_until='load', timeout=60000)
            if not r: return 0, b''
            b = r.body(); return r.status, b
        except Exception as e:
            return 0, b''
    def close(s): s.b.close()

def polite(C): a, b = C.get('polite_delay_s', [3, 7]); time.sleep(random.uniform(a, b))
def is_png(b): return len(b) > 8000 and b[:8] == b'\x89PNG\r\n\x1a\n'

def discover(B, C, IDS, mod, reg, P, site):
    k = f"{mod}|{reg}|{P['id']}"
    if k in IDS: return IDS[k]
    for slug in P['slugs']:
        st, h = B.html(f"{site}/model-charts/{mod}/{reg}/{slug}.html"); polite(C)
        m = OG.search(h or '')
        if st == 200 and m and not (m.group(6) == '1' and P['id'] != 't2m'):
            IDS[k] = dict(host=m.group(1), code=m.group(2), rid=m.group(5), pid=m.group(6), slug=slug)
            log(f"  ✓ کشف {k} ← {slug}"); return IDS[k]
        log(f"  ✗ کشف {k} ← {slug}: HTTP {st}{' (Access Denied)' if 'Access Denied' in (h or '') else ''}")
    IDS[k] = None; return None

def one_run(B, H, C, run, test=False):
    site = C['site'].rstrip('/'); IDS = {}
    items = plan(C, run)
    have = set() if test else set(H.call('begin', js={'run': run, 'plan': items}).get('have', []))
    todo = [i for i in items if i['file'] not in have]
    log(f"▶ اجرای {run}: {len(items)} نقشه، {len(have)} از قبل روی هاست، {len(todo)} باقی")
    P_BY = {P['id']: P for P in C['params']}; got = fail = 0
    deadline = run_start(C, run) + timedelta(hours=C.get('download_deadline_h', 10))
    seen_test = set()
    for it in todo:
        if datetime.now(UTC) > deadline and not test: log("⏱ مهلت این اجرا تمام شد"); break
        P = P_BY[it['p']]; idd = discover(B, C, IDS, it['m'], it['r'], P, site)
        if not idd: fail += 1; continue
        if test:
            key = (it['r'], it['m'], it['p'])
            if key in seen_test: continue
            seen_test.add(key)
        u = f"https://{idd['host']}/images/data/cache/model/complete_model_{idd['code']}_{run}_{it['s']}_{idd['rid']}_{idd['pid']}.png"
        st, b = B.png(u); polite(C)
        if not is_png(b):   # راه دوم: صفحهٔ همان زمان → og:image
            vt = f"{it['v'][:8]}-{it['v'][8:]}00z"
            st2, h = B.html(f"{site}/model-charts/{it['m']}/{run}/{it['r']}/{idd['slug']}/{vt}.html"); polite(C)
            m = OG.search(h or '')
            if m and m.group(3) == run and int(m.group(4)) == it['s']: st, b = B.png(m.group(0)); polite(C)
        if not is_png(b): fail += 1; log(f"  ✗ {it['file']}: HTTP {st}"); continue
        if test: log(f"  ✓ {it['r']}/{it['m']}/{it['p']}: {len(b)//1024} KB"); continue
        r = H.call('put', data={'run': run, 'file': it['file']}, files={'png': (it['file'].split('/')[-1], b, 'image/png')})
        if r.get('ok'): got += 1; log(f"  ↑ {it['file']}  ({r.get('done')}/{r.get('expected')})")
        else: fail += 1; log(f"  ✗ آپلود {it['file']}: {r}")
    if not test: H.call('end', js={'run': run})
    log(f"■ اجرای {run}: +{got} آپلود، {fail} ناموفق")
    return fail

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', required=True, help='مثلاً https://wtafkik.ir/online')
    ap.add_argument('--key', required=True); ap.add_argument('--loop', action='store_true'); ap.add_argument('--test', action='store_true')
    ap.add_argument('--show', action='store_true'); ap.add_argument('--chrome', action='store_true')
    ap.add_argument('--max-minutes', type=int, default=0, help='سقف زمان کل (برای GitHub Actions)')
    a = ap.parse_args(); H = Host(a.host, a.key); t_end = time.time() + a.max_minutes * 60 if a.max_minutes else None
    C = H.call('config')
    if not C.get('params'): sys.exit('✗ تنظیمات از هاست خوانده نشد (mx_upload.php را بررسی کنید)')
    with sync_playwright() as pw:
        B = Browser(pw, a.show, a.chrome)
        try:
            done_runs = set()
            while True:
                now = datetime.now(UTC); run = active_run(C, now)
                if run and run not in done_runs:
                    fail = one_run(B, H, C, run, a.test)
                    if a.test: break
                    if fail == 0: done_runs.add(run)
                if not a.loop: break
                if t_end and time.time() > t_end: break
                wait = 600 if (run and run not in done_runs) else max(60, min(3600, (run_start(C, next_run(C, now)) - now).total_seconds()))
                log(f"… انتظار {int(wait//60)} دقیقه"); time.sleep(wait)
        finally:
            B.close()

if __name__ == '__main__':
    main()
