#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""نقشه‌های پیش‌بینی بلندمدت (ماهانه/فصلی): NMME از FTP عمومی CPC → کاشی «خاورمیانه» → هاست.
  NMME realtime anomalies: https://ftp.cpc.ncep.noaa.gov/NMME/realtime_anom/<MODEL>/<YYYYMM0800>/*.nc  (متغیرها prate و tmp2m؛ انومالی)
آزمایشی: ساختار پوشه‌ها با فهرست‌گیری کشف می‌شود و هر خطا فقط همان مدل را کنار می‌گذارد.
اجرا: python lr_maps.py --host https://wtafkik.ir/online --key KEY   |   --mock --host ... (آزمون بدون شبکه)"""
import argparse, io, json, math, os, re, sys, tempfile, time
import numpy as np
from scipy.ndimage import map_coordinates
import nwp_maps as N, styles

BASE = 'https://ftp.cpc.ncep.noaa.gov/NMME/realtime_anom/'
# کلید مدل روی هاست فقط حروف a-z (۲ تا ۱۰ حرف؛ رقم را PHP رد می‌کند → خطای {'error':'model'}): (نام پوشه در CPC — بدون حساسیت به حروف، برچسب فارسی, نام لاتین)
MODELS = {
    'nmme':    ('ENSMEAN', 'NMME میانگین چندمدلی', 'NMME ensemble mean'),
    'cfsvtwo': ('CFSv2', 'CFSv2 (NOAA)', 'CFSv2'),
    'ccsmfour':('CCSM4', 'NCAR CCSM4', 'NCAR CCSM4'),
    'gemnemo': ('GEM5_NEMO', 'GEM5-NEMO (ECCC)', 'GEM5-NEMO'),
    'nasa':    ('NASA', 'NASA GEOS-S2S', 'NASA GEOS-S2S'),
    'gfdl':    ('GFDL', 'GFDL SPEAR', 'GFDL SPEAR'),
    'cancm':   ('CanCM4i', 'CanCM4i (ECCC)', 'CanCM4i'),
}
VARS = {'prate': ('lrpa', 'انومالی بارش ماهانه (میلی‌متر در روز)', 'mm/d', 'pr', 'lrpa', 86400.0), 'tmp2m': ('lrta', 'انومالی دمای ۲ متری ماهانه (°C)', '°C', 'tp', 'lrta', 1.0)}
LON = np.float32(N.ME_EXT[0]) + (np.arange(N.MEW) + 0.5) / N.PXD
LAT = np.float32(N.ME_EXT[3]) - (np.arange(N.MEH) + 0.5) / N.PXD
LON2, LAT2 = np.meshgrid(LON, LAT)
log = N.log

def get(url, binary=False):
    import requests
    r = requests.get(url, timeout=120, headers={'User-Agent': 'MetStat-lr/1.0'}); r.raise_for_status()
    return r.content if binary else r.text

def links(url):
    return [h for h in re.findall(r'href="([^"?#]+)"', get(url)) if not h.startswith(('/', 'http', '..'))]

def find_dir(key):
    names = links(BASE); key = key.lower()
    c = [n for n in names if n.endswith('/') and key in n.lower()]
    return BASE + (sorted(c, key=len)[0]) if c else None

def regrid(arr, lon, lat):
    """میدان lat/lon منظم → شبکهٔ کاشی (درون‌یابی مکعبی؛ رنگ‌ها بعداً گسسته می‌شوند)"""
    lon = np.asarray(lon, float) % 360; lat = np.asarray(lat, float)
    o = np.argsort(lon); lon, arr = lon[o], arr[:, o]
    if lat[0] > lat[-1]: lat, arr = lat[::-1], arr[::-1]
    arr = np.ma.filled(np.ma.masked_invalid(arr).astype(float), np.nan)
    ix = np.interp(LON2 % 360, lon, np.arange(len(lon))); iy = np.interp(LAT2, lat, np.arange(len(lat)))
    a = np.where(np.isfinite(arr), arr, np.nanmean(arr))
    return map_coordinates(a, [iy, ix], order=3, mode='nearest').astype(np.float32)

def read_nc(path, vname):
    """خروجی: data(lead,lat,lon), lon, lat, offs(ماه‌های پس از آغاز اجرا), units
    فایل‌های CPC/NMME متغیر دادهٔ عمومی «fcst» دارند (ابعاد: initial_time, ensmem, target, lat, lon)."""
    import netCDF4
    ds = netCDF4.Dataset(path); vs = ds.variables
    low = {k.lower(): k for k in vs}
    lonk = next(low[k] for k in ('lon', 'longitude', 'x') if k in low); latk = next(low[k] for k in ('lat', 'latitude', 'y') if k in low)
    coord = {lonk, latk} | {k for k in vs if k.lower() in ('target', 'ensmem', 'initial_time', 'time', 'lead', 'level', 'member')}
    key = next((k for k in vs if vname in k.lower() and k not in coord), None) or low.get('fcst')
    if not key:
        c = [k for k in vs if k not in coord and len(vs[k].dimensions) >= 3]
        key = c[0] if c else None
    if not key: raise RuntimeError(f'متغیر داده در فایل نیست ({list(vs)})')
    v = vs[key]; dims = [d.lower() for d in v.dimensions]; units = str(getattr(v, 'units', ''))
    raw = np.ma.filled(np.ma.asarray(v[:]).astype(float), np.nan)
    raw[np.abs(raw) > 1e15] = np.nan
    fv = getattr(v, 'missing_value', None)
    if fv is not None:
        try: raw[raw == float(np.ravel(fv)[0])] = np.nan
        except Exception: pass
    isl = lambda d: d in ('lat', 'latitude', 'y'); iso = lambda d: d in ('lon', 'longitude', 'x')
    tk = next((d for d in dims if d in ('target', 'lead')), None)
    for ax in range(len(dims) - 1, -1, -1):              # حذف محورهای اضافه
        d = dims[ax]
        if d == tk or isl(d) or iso(d): continue
        raw = np.nanmean(raw, axis=ax) if d in ('ensmem', 'member') else np.take(raw, -1 if d in ('initial_time', 'time') else 0, axis=ax)
        dims.pop(ax)
    if tk is None: raw = raw[None]; dims = ['target'] + dims; tk = 'target'
    data = np.transpose(raw, [dims.index(tk), next(i for i, d in enumerate(dims) if isl(d)), next(i for i, d in enumerate(dims) if iso(d))])
    lon = np.array(vs[lonk][:], float); lat = np.array(vs[latk][:], float)
    offs = None
    tn = next((k for k in vs if k.lower() == tk), None)
    if tn:
        t = np.array(vs[tn][:], float).ravel()
        if len(t) == data.shape[0]:
            mm = re.search(r'months since (\d{4})-(\d{1,2})', str(getattr(vs[tn], 'units', '')))
            by, bm = (int(mm.group(1)), int(mm.group(2))) if mm else (1960, 1)
            offs = [(by * 12 + bm - 1 + int(math.floor(x + 1e-6)), 'abs') for x in t]   # ماه مطلق (شاخص ماه)
    if offs is None: offs = [(None, 'rel')] * data.shape[0]
    log(f'  nc: var={key} dims={v.dimensions} units="{units}" shape={tuple(data.shape)} offs={offs[:4]}..')
    ds.close(); return data, lon, lat, offs, units

def first_month():
    """اولین ماه نمایش: تا قبل از روز ۱۵ ماه جاری؛ از روز ۱۵ به بعد ماه بعد."""
    t = time.gmtime(); y, m = t.tm_year, t.tm_mon
    return month_add(y, m, 1) if t.tm_mday >= 15 else (y, m)

def month_add(y, m, k): m += k; return y + (m - 1) // 12, (m - 1) % 12 + 1

def run_model(up, mkey, init_dirs, a):
    """هر مدل دو کلید روی هاست می‌سازد: mkey (گام ۱ ماهه، tclass=monthly) و mkey+'s' (میانگین ۳ ماهه، tclass=seasonal)."""
    folder, label, en = MODELS[mkey]; d = find_dir(folder)
    if not d: log(f'  ✗ {mkey}: پوشه‌ای شبیه {folder} در CPC نیست'); return 0
    subs = sorted(x for x in links(d) if re.fullmatch(r'\d{8,10}/', x))
    if not subs: log(f'  ✗ {mkey}: پوشهٔ اجرا نیست'); return 0
    sub = subs[-1]; ym = sub[:6]; y0, m0 = int(ym[:4]), int(ym[4:6])
    if (time.gmtime().tm_year - y0) * 12 + time.gmtime().tm_mon - m0 > 3: log(f'  ✗ {mkey}: آخرین اجرا قدیمی است ({sub}) — کنار گذاشته شد'); return 0
    # شناسهٔ اجرا = تاریخ امروز (UTC): با هر روز جدید، پوشهٔ اجرای قبلی (و فایل‌های قدیمیِ تاریخ‌غلط) از هاست پاک می‌شود
    run = time.strftime('%Y%m%d', time.gmtime()) + '00'
    files = [f for f in links(d + sub) if f.endswith('.nc')]
    start = first_month(); keys = {1: mkey, 3: mkey + 's'}; made = {1: 0, 3: 0}; started = set()
    for vn, (pid, fa, unit, cat, sc, mul) in VARS.items():
        fs = [f for f in files if vn in f.lower()]
        if not fs: log(f'  ✗ {mkey}: فایل {vn} نیست؛ فایل‌های موجود: {files[:12]}'); continue
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, 'x.nc'); open(p, 'wb').write(get(d + sub + fs[0], True))
            data, lon, lat, offs, units = read_nc(p, vn)
            m_ = mul if (vn != 'prate' or not units or re.search(r'(s-1|/s|s\^-1)', units.replace(' ', ''), re.I)) else 1.0
        for agg, key in keys.items():
            if key in started: continue
            tc, cyc = ('monthly', 'ماهانه (گام ۱ ماهه)') if agg == 1 else ('seasonal', 'فصلی (میانگین ۳ ماهه)')
            up.call('begin', js={'model': key, 'run': run, 'name': key.upper(), 'label': label + ('' if agg == 1 else ' — ۳ ماهه'), 'info': dict(tclass=tc, res='1°', cyc=cyc, prov='NOAA CPC / NMME'),
                                 'cats': [dict(id=i, fa=f, o=o) for i, f, o in N.CATS],
                                 'scales': {k: dict(c=styles.SC[k]['colors'], e=[None if abs(x) == math.inf else x for x in styles.SC[k]['edges']], lo=True, hi=True) for k in ('lrta', 'lrpa')},
                                 'params': {v[0]: dict(fa=v[1], unit=v[2], cat=v[3], o=90 + i, sc=v[4], mu=v[2], alt='', me=1, ln=0) for i, v in enumerate(VARS.values())}}); started.add(key)
        fld = {}                                               # (سال، ماه) → میدان رگرید‌شده؛ ماه‌های تکراری حذف
        for k in range(len(data)):
            if offs[k][1] == 'abs': y, m = divmod(offs[k][0], 12); m += 1
            else: y, m = month_add(y0, m0, k)
            if (y, m) in fld: continue
            f = regrid(data[k] * m_, lon, lat)
            if np.isfinite(f).any(): fld[(y, m)] = f
        ms = sorted(fld)
        for agg, key in keys.items():
            n = 0
            for i, ym_ in enumerate(ms):
                if ym_ < start: continue
                if agg == 1: f = fld[ym_]
                else:
                    w = [month_add(ym_[0], ym_[1], j) for j in range(3)]
                    if not all(x in fld for x in w): continue   # ۳ ماه متوالی لازم است
                    f = np.mean([fld[x] for x in w], axis=0)
                dat, _ = N.render_me(None, f, sc)
                up.put(key, run, f'me/{pid}_{ym_[0]}{ym_[1]:02d}0100.webp', dat, dict(s=n)); n += 1; made[agg] += 1
    for agg, key in keys.items():
        if key in started: up.wait(); up.call('end', js={'model': key, 'run': run}); log(f'✓ {key} {run}: {made[agg]} نقشه')
    return made[1] + made[3]

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--host'); ap.add_argument('--key', default=''); ap.add_argument('--out'); ap.add_argument('--models', default=','.join(MODELS)); ap.add_argument('--mock', action='store_true')
    a = ap.parse_args(); up = N.Uploader(a.host, a.key, a.out); tot = 0
    if a.mock:
        global get, links, find_dir, read_nc
        rng = np.random.default_rng(1); la = np.arange(-89.5, 90); lo = np.arange(0.5, 360)
        def read_nc(path, vn): return rng.normal(0, 1.2 if vn == 'tmp2m' else 1.5e-5 if vn == 'prate' else 1, (7, 180, 360)).astype(np.float32), lo, la, [(None, 'rel')] * 7, 'kg m-2 s-1'
        find_dir = lambda k: BASE + k + '/'
        links = lambda u: (['202610/'] if False else []) or (['2026100800/'] if u.endswith('/') and u.count('/') < 7 else ['prate.nc', 'tmp2m.nc'])
        get = lambda u, b=False: b'x'
        a.models = 'nmme'
    for mk in a.models.split(','):
        try: tot += run_model(up, mk, None, a)
        except Exception as e: log(f'✗ {mk}: {type(e).__name__}: {e}')
    log(f'پایان: {tot} نقشه'); sys.exit(0)

if __name__ == '__main__': main()
