# -*- coding: utf-8 -*-
"""ECMWF SEAS5 (Copernicus C3S) -> نقشهٔ انومالی ماهانه و ۳ماهه. از داخل lr_maps.py صدا زده می‌شود.
انومالی = میانگین گروهی پیش‌بینی - میانگین اقلیمی بازپیش‌بینی (hindcast) همان دیتاست.
کلید CDS از متغیر محیطی CDSAPI_KEY (GitHub Secret) خوانده می‌شود؛ شمارهٔ system با ECMWF_SYSTEM (پیش‌فرض 51)."""
import json, math, os, re, tempfile, time, zipfile
import numpy as np

DATASET = 'seasonal-monthly-single-levels'
SYSTEM = os.environ.get('ECMWF_SYSTEM', '51')
KEYS = {1: 'ecmwfseas', 3: 'ecmwfseass'}  # کلید روی هاست فقط حروف a-z (۲ تا ۱۰ حرف)؛ 'ecmwf' برای مدل روزانهٔ IFS است و نباید استفاده شود
LEADS = ['1', '2', '3', '4', '5', '6']

def fetch(c, ptype, y0, m0, td):
    req = {'originating_centre': 'ecmwf', 'system': SYSTEM, 'variable': ['total_precipitation', '2m_temperature'],
           'product_type': [ptype], 'year': [str(y0)], 'month': [f'{m0:02d}'], 'leadtime_month': LEADS, 'data_format': 'netcdf'}
    p = os.path.join(td, ptype + '.nc'); c.retrieve(DATASET, req, p)
    with open(p, 'rb') as f: head = f.read(2)
    if head == b'PK':                                   # گاهی CDS فایل را zip می‌دهد
        with zipfile.ZipFile(p) as z:
            nc = [n for n in z.namelist() if n.endswith('.nc')]
            if not nc: raise RuntimeError('zip بدون فایل nc')
            z.extract(nc[0], td); p = os.path.join(td, nc[0])
    return p

def read_cds(path, log=print):
    """خروجی: {'prate': (data[lead,lat,lon], lon, lat, units), 'tmp2m': (...)}"""
    import netCDF4
    ds = netCDF4.Dataset(path); vs = ds.variables; low = {k.lower(): k for k in vs}
    lonk = next(low[k] for k in ('longitude', 'lon', 'x') if k in low); latk = next(low[k] for k in ('latitude', 'lat', 'y') if k in low)
    lon = np.array(vs[lonk][:], float); lat = np.array(vs[latk][:], float); out = {}
    for name, alts in (('prate', ('tprate', 'tp', 'total_precipitation', 'total_precipitation_rate')), ('tmp2m', ('t2m', '2m_temperature'))):
        key = next((k for k in vs if k.lower() in alts), None)
        if not key: continue
        v = vs[key]; dims = [d.lower() for d in v.dimensions]; units = str(getattr(v, 'units', ''))
        raw = np.ma.filled(np.ma.asarray(v[:]).astype(float), np.nan); raw[np.abs(raw) > 1e15] = np.nan
        isl = lambda d: d in ('latitude', 'lat', 'y'); iso = lambda d: d in ('longitude', 'lon', 'x')
        lead = next((d for d in dims if d in ('forecastmonth', 'forecast_month', 'leadtime_month', 'leadtime', 'lead', 'step', 'valid_time')), None)
        if lead is None:                                 # محوری که اندازه‌اش ۶ است
            lead = next((d for d, n in zip(dims, raw.shape) if n == len(LEADS) and not isl(d) and not iso(d)), None)
        for ax in range(len(dims) - 1, -1, -1):
            d = dims[ax]
            if d == lead or isl(d) or iso(d): continue
            raw = np.nanmean(raw, axis=ax) if d in ('number', 'ensmem', 'member') else np.take(raw, -1 if 'time' in d else 0, axis=ax)
            dims.pop(ax)
        if lead is None: raw = raw[None]; dims = ['lead'] + dims; lead = 'lead'
        data = np.transpose(raw, [dims.index(lead), next(i for i, d in enumerate(dims) if isl(d)), next(i for i, d in enumerate(dims) if iso(d))])
        log(f'  ecmwf nc: {key} dims={v.dimensions} units="{units}" shape={tuple(data.shape)}')
        out[name] = (data, lon, lat, units)
    ds.close(); return out

def pr_mul(units):
    """تبدیل بارش به mm/d"""
    u = units.replace(' ', '').lower()
    if 'kg' in u: return 86400.0
    if re.search(r's\*?\*?\^?-1|/s', u): return 86400.0 * 1000.0      # m s**-1 (tprate)
    return 1000.0 / 30.4                                 # مجموع ماهانه بر حسب متر (احتیاط)

def run(up, a, L):
    log = L.log
    if not os.environ.get('CDSAPI_KEY'): log('✗ ecmwf: CDSAPI_KEY تنظیم نشده'); return 0
    t = time.gmtime(); y0, m0 = (t.tm_year, t.tm_mon) if t.tm_mday >= 6 else L.month_add(t.tm_year, t.tm_mon, -1)   # انتشار ECMWF روز ۵ ماه
    run_id = f'{y0}{m0:02d}0100'
    if a.host:                                           # اگر این ماه قبلاً کامل ساخته شده، دوباره درخواست ندهیم
        try:
            mod = json.loads(L.get(a.host.rstrip('/') + '/data/manifest.json?' + str(int(time.time())))).get('models', {})
            if all(mod.get(k, {}).get('run') == run_id and mod.get(k, {}).get('status') == 'complete' for k in KEYS.values()):
                log(f'✓ ecmwf {run_id}: قبلاً کامل است'); return 0
        except Exception as e: log(f'  (manifest خوانده نشد: {e})')
    import cdsapi
    c = cdsapi.Client()
    with tempfile.TemporaryDirectory() as td:
        fc = read_cds(fetch(c, 'ensemble_mean', y0, m0, td), log)
        cl = read_cds(fetch(c, 'hindcast_climate_mean', y0, m0, td), log)
    fld = {}
    for vn, (pid, fa, unit, cat, sc, mul0) in L.VARS.items():
        if vn not in fc or vn not in cl: log(f'  ✗ ecmwf: {vn} در فایل نیست؛ متغیرهای موجود: fc={list(fc)} cl={list(cl)}'); continue
        d, lon, lat, un = fc[vn]; mul = pr_mul(un) if vn == 'prate' else 1.0
        fld[vn] = {}
        for k in range(min(len(d), len(cl[vn][0]))):
            f = L.regrid((d[k] - cl[vn][0][k]) * mul, lon, lat)
            if np.isfinite(f).any(): fld[vn][L.month_add(y0, m0, k)] = f
    if not fld: log('✗ ecmwf: داده‌ای ساخته نشد'); return 0
    start = L.first_month(); made = 0
    for agg, key in KEYS.items():
        tc, cyc = ('monthly', 'ماهانه (گام ۱ ماهه)') if agg == 1 else ('seasonal', 'فصلی (میانگین ۳ ماهه)')
        up.call('begin', js={'model': key, 'run': run_id, 'name': 'ECMWF', 'label': 'ECMWF SEAS5 (C3S)' + ('' if agg == 1 else ' — ۳ ماهه'),
                             'info': dict(tclass=tc, res='1°', cyc=cyc, prov='ECMWF SEAS5 / Copernicus C3S'),
                             'cats': [dict(id=i, fa=f, o=o) for i, f, o in L.N.CATS],
                             'scales': {k: dict(c=L.styles.SC[k]['colors'], e=[None if abs(x) == math.inf else x for x in L.styles.SC[k]['edges']], lo=True, hi=True) for k in ('lrta', 'lrpa')},
                             'params': {v[0]: dict(fa=v[1], unit=v[2], cat=v[3], o=90 + i, sc=v[4], mu=v[2], alt='', me=1, ln=0) for i, v in enumerate(L.VARS.values())}})
        n_tot = 0
        for vn, (pid, fa, unit, cat, sc, mul0) in L.VARS.items():
            fl = fld.get(vn); n = 0
            if not fl: continue
            for ym_ in sorted(fl):
                if ym_ < start: continue
                if agg == 1: f = fl[ym_]
                else:
                    w = [L.month_add(ym_[0], ym_[1], j) for j in range(3)]
                    if not all(x in fl for x in w): continue
                    f = np.mean([fl[x] for x in w], axis=0)
                dat, _ = L.N.render_me(None, f, sc)
                up.put(key, run_id, f'me/{pid}_{ym_[0]}{ym_[1]:02d}0100.webp', dat, dict(s=n)); n += 1; n_tot += 1
        up.wait(); up.call('end', js={'model': key, 'run': run_id}); log(f'✓ {key} {run_id}: {n_tot} نقشه'); made += n_tot
    return made
