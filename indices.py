#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""نمودار شاخص‌های اقلیمی (ENSO، QBO، NAO، IOD/DMI، PDO، AMO و ...) → PNG → هاست (idxput/idxend).
فقط تصویر نمودار روی هاست می‌رود؛ داده‌های عددی منتشر نمی‌شود.
اجرا:  python indices.py --host https://wtafkik.ir/online --key KEY   |   --mock --out out  (آزمون بدون شبکه)"""
import argparse, io, json, math, os, sys, time, datetime as dt
import numpy as np
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt

PSL = 'https://psl.noaa.gov/data/correlation/'
# id: (نام فارسی, نام انگلیسی, گروه, واحد, نوع نمودار, [آدرس‌ها به ترتیب اولویت], آستانه‌ها)
IDX = {
 'nino34': ('نینو ۳٫۴ (SST)', 'Niño 3.4 SST anomaly', 'ENSO', '°C', 'bar', [PSL + 'nina34.anom.data'], (0.5, -0.5)),
 'nino3':  ('نینو ۳ (SST)', 'Niño 3 SST anomaly', 'ENSO', '°C', 'bar', [PSL + 'nina3.anom.data'], (0.5, -0.5)),
 'nino4':  ('نینو ۴ (SST)', 'Niño 4 SST anomaly', 'ENSO', '°C', 'bar', [PSL + 'nina4.anom.data'], (0.5, -0.5)),
 'nino12': ('نینو ۱+۲ (SST)', 'Niño 1+2 SST anomaly', 'ENSO', '°C', 'bar', [PSL + 'nina1.anom.data'], (0.5, -0.5)),
 'oni':    ('ONI (سه‌ماههٔ متحرک نینو ۳٫۴)', 'Oceanic Niño Index (ONI)', 'ENSO', '°C', 'bar', [PSL + 'oni.data'], (0.5, -0.5)),
 'soi':    ('SOI نوسان جنوبی', 'Southern Oscillation Index (SOI)', 'ENSO', '', 'bar', [PSL + 'soi.data'], (1.0, -1.0)),
 'meiv2':  ('MEI.v2 (شاخص چندمتغیرهٔ انسو)', 'Multivariate ENSO Index v2', 'ENSO', '', 'bar', ['https://psl.noaa.gov/enso/mei/data/meiv2.data', PSL + 'meiv2.data'], (0.5, -0.5)),
 'qbo':    ('QBO (باد مداری ۳۰ هکتوپاسکال)', 'QBO (30 hPa equatorial wind)', 'QBO', 'm/s', 'line', [PSL + 'qbo.data'], ()),
 'nao':    ('NAO نوسان اطلس شمالی', 'North Atlantic Oscillation (NAO)', 'Atmosphere', '', 'bar', [PSL + 'nao.data', 'https://www.cpc.ncep.noaa.gov/products/precip/CWlink/pna/norm.nao.monthly.b5001.current.ascii.table'], (1.0, -1.0)),
 'ao':     ('AO نوسان قطبی', 'Arctic Oscillation (AO)', 'Atmosphere', '', 'bar', [PSL + 'ao.data', 'https://www.cpc.ncep.noaa.gov/products/precip/CWlink/daily_ao_index/monthly.ao.index.b50.current.ascii.table'], (1.0, -1.0)),
 'pna':    ('PNA الگوی اقیانوس آرام–آمریکای شمالی', 'Pacific–North American (PNA)', 'Atmosphere', '', 'bar', [PSL + 'pna.data'], (1.0, -1.0)),
 'ea':     ('EA اطلس شرقی', 'East Atlantic pattern (EA)', 'Atmosphere', '', 'bar', [PSL + 'ea.data'], (1.0, -1.0)),
 'wp':     ('WP اقیانوس آرام غربی', 'West Pacific pattern (WP)', 'Atmosphere', '', 'bar', [PSL + 'wp.data'], (1.0, -1.0)),
 'scand':  ('SCAND اسکاندیناوی', 'Scandinavia pattern', 'Atmosphere', '', 'bar', [PSL + 'scand.data', PSL + 'scand.long.data'], (1.0, -1.0)),
 'dmi':    ('DMI / IOD دوقطبی اقیانوس هند', 'Dipole Mode Index (IOD/DMI)', 'Indian Ocean', '°C', 'bar', ['https://psl.noaa.gov/gcos_wgsp/Timeseries/Data/dmi.had.long.data', PSL + 'dmi.data'], (0.4, -0.4)),
 'pdo':    ('PDO نوسان دههای اقیانوس آرام', 'Pacific Decadal Oscillation (PDO)', 'Ocean', '', 'bar', [PSL + 'pdo.data', 'https://www.ncei.noaa.gov/pub/data/cmb/ersst/v5/index/ersst.v5.pdo.dat'], (0.0,)),
 'amo':    ('AMO نوسان چندده‌ای اطلس', 'Atlantic Multidecadal Oscillation (AMO)', 'Ocean', '°C', 'bar', [PSL + 'amon.us.data'], (0.0,)),
 'tna':    ('TNA اطلس شمالی گرمسیری', 'Tropical North Atlantic (TNA)', 'Ocean', '°C', 'bar', [PSL + 'tna.data'], (0.0,)),
 'tsa':    ('TSA اطلس جنوبی گرمسیری', 'Tropical South Atlantic (TSA)', 'Ocean', '°C', 'bar', [PSL + 'tsa.data'], (0.0,)),
}
GRP_FA = {'ENSO': 'انسو (ENSO)', 'QBO': 'QBO', 'Atmosphere': 'الگوهای جوی', 'Indian Ocean': 'اقیانوس هند', 'Ocean': 'اقیانوسی'}
PERIODS = {'all': ('کل دوره', None), 'y30': ('۳۰ سال اخیر', 30), 'y10': ('۱۰ سال اخیر', 10), 'y3': ('۳ سال اخیر', 3)}
MISS = {-99.99, -9.99, -999.9, -99.9, -999.0, -9999.0, -99.0, -9.9, -999.99, -99999.0, 99.99, 999.9}

def log(*a): print(*a, flush=True)

def fetch(url):
    import requests
    r = requests.get(url, timeout=60, headers={'User-Agent': 'MetStat-indices/1.0'})
    r.raise_for_status(); return r.text

def parse(text):
    """جدول «سال + ۱۲ ماه» → (تاریخ‌ها, مقادیر). سطرهای دیگر (عنوان/پانویس) نادیده گرفته می‌شود."""
    t, v = [], []
    for ln in text.splitlines():
        p = ln.replace(',', ' ').split()
        if len(p) < 13 or not p[0].isdigit() or not (1800 <= int(p[0]) <= 2100): continue
        try: row = [float(x) for x in p[1:13]]
        except ValueError: continue
        y = int(p[0])
        for m, x in enumerate(row, 1):
            t.append(dt.date(y, m, 15)); v.append(np.nan if (round(x, 2) in MISS or abs(x) > 900) else x)
    while v and not np.isfinite(v[-1]): t.pop(); v.pop()           # ماههای آیندهٔ پرنشده
    return t, np.array(v, float)

def render(iid, t, v, per, meta):
    fa, en, grp, unit, kind, _, thr = IDX[iid]
    n = PERIODS[per][1]
    if n: k = max(1, len(t) - 12 * n); t, v = t[k:], v[k:]
    fig, ax = plt.subplots(figsize=(12, 6), dpi=100); fig.patch.set_facecolor('white')
    x = np.array([d.year + (d.month - .5) / 12 for d in t])
    w = 1 / 12 * 0.95
    if kind == 'bar':
        ax.bar(x, np.where(v >= 0, v, 0), width=w, color='#d73027', linewidth=0)
        ax.bar(x, np.where(v < 0, v, 0), width=w, color='#2c7bb6', linewidth=0)
    else:
        ax.plot(x, v, color='#333', lw=1.2)
    if len(v) >= 12:
        k = np.ones(12) / 12; ok = np.isfinite(v); vv = np.where(ok, v, 0)
        sm = np.convolve(vv, k, 'same') / np.maximum(np.convolve(ok.astype(float), k, 'same'), 1e-9)
        ax.plot(x[6:-6] if len(x) > 14 else x, sm[6:-6] if len(x) > 14 else sm, color='black', lw=1.1, alpha=.75, label='12-month mean')
    for th in thr:
        ax.axhline(th, color='#555', lw=.8, ls='--' if th else '-')
    ax.axhline(0, color='black', lw=.8)
    ax.grid(alpha=.25); ax.set_xlim(x[0] - .3, x[-1] + .3)
    ax.set_ylabel(unit or 'index'); ax.set_title(f'{en}', fontsize=15, weight='bold', loc='left')
    last = np.where(np.isfinite(v))[0]
    if len(last):
        i = last[-1]; ax.annotate(f'{v[i]:+.2f}  ({t[i]:%Y-%m})', (x[i], v[i]), xytext=(-10, 18 if v[i] >= 0 else -26), textcoords='offset points', ha='right', fontsize=11, weight='bold', arrowprops=dict(arrowstyle='-', color='#666'))
    fig.text(.01, .012, f'MetStat · source: {meta["src"]} · {PERIODS[per][1] and ("last %d yr" % PERIODS[per][1]) or "full record"} · updated {meta["upd"]}', fontsize=8.5, color='#666')
    fig.tight_layout(rect=(0, .03, 1, 1)); b = io.BytesIO(); fig.savefig(b, format='png'); plt.close(fig); return b.getvalue()

def mock_series(iid):
    rng = np.random.default_rng(abs(hash(iid)) % 1000); n = 12 * 75
    t = [dt.date(1950 + i // 12, i % 12 + 1, 15) for i in range(n)]
    v = np.convolve(rng.normal(0, 1, n + 8), np.ones(9) / 4, 'valid')[:n]; return t, v

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--host'); ap.add_argument('--key'); ap.add_argument('--out'); ap.add_argument('--mock', action='store_true'); ap.add_argument('--only')
    a = ap.parse_args(); ok = fail = 0; import requests
    now = dt.datetime.utcnow().strftime('%Y-%m-%d')
    for iid, (fa, en, grp, unit, kind, urls, thr) in IDX.items():
        if a.only and iid not in a.only.split(','): continue
        t = v = None; src = ''
        if a.mock: t, v = mock_series(iid); src = 'mock'
        else:
            for u in urls:
                try:
                    t, v = parse(fetch(u)); src = u.split('/')[2]
                    if len(t) > 24: break
                    log(f'  ؟ {iid}: داده‌ٔ کم از {u}')
                except Exception as e: log(f'  ✗ {iid}: {u} → {e}'); t = None
        if not t or len(t) < 24: log(f'✗ {iid}: داده‌ای نشد'); fail += 1; continue
        last = [d for d, x in zip(t, v) if np.isfinite(x)][-1]
        meta = dict(fa=fa, en=en, grp=GRP_FA.get(grp, grp), src=src, last=f'{last:%Y-%m}', upd=now, unit=unit, kind=kind, note='')
        for per, (pfa, _) in PERIODS.items():
            png = render(iid, t, v, per, meta)
            if a.out: os.makedirs(a.out, exist_ok=True); open(os.path.join(a.out, f'{iid}_{per}.png'), 'wb').write(png)
            if a.host:
                u = f'{a.host.rstrip("/")}/nwp_upload.php?a=idxput&key={requests.utils.quote(a.key)}'
                for k in range(4):
                    try:
                        r = requests.post(u, data=dict(id=iid, per=per, meta=json.dumps(dict(meta, perfa=pfa))), files={'png': (f'{iid}_{per}.png', png, 'image/png')}, timeout=90)
                        if r.status_code == 403: sys.exit('✗ کلید آپلود اشتباه است')
                        if r.json().get('ok'): break
                        log(f'  ✗ پاسخ هاست {iid}/{per}: {r.text[:120]}')
                    except SystemExit: raise
                    except Exception as e: log(f'  … {e}'); time.sleep(4 * (k + 1))
        log(f'✓ {iid} ({len(t)} ماه، تا {last:%Y-%m}، منبع {src})'); ok += 1
    if a.host:
        requests.post(f'{a.host.rstrip("/")}/nwp_upload.php?a=idxend&key={requests.utils.quote(a.key)}', timeout=60)
    log(f'پایان: {ok} شاخص موفق، {fail} ناموفق'); sys.exit(0 if ok else 1)

if __name__ == '__main__': main()
