# -*- coding: utf-8 -*-
"""
MetStat — ساخت خودکار نقشه‌های مدل‌های عددی (ECMWF، GFS، ICON، UKMO، GEM، ARPEGE، ACCESS-G) برای «تحلیل نقشه‌های آنلاین»

منبع داده: داده‌های باز مدل‌ها در Open-Meteo روی Amazon S3 (s3://openmeteo/data_spatial، مجوز CC-BY 4.0) با بهترین
رزولوشن رایگان هر مدل. هر گام زمانی به‌محض انتشار خوانده می‌شود (فقط محدودهٔ ایران)، پارامترهای مشتق (شاخص K، GDI،
Lifted، Soaring، بازتاب، هوای مهم، انومالی دما…) محاسبه و نقشه با قاب و رنگ‌بندی weather.us (رنگ‌های گسسته، بدون رنگ
واسطه) ساخته و با HTTPS به هاست (online/nwp_upload.php) فرستاده می‌شود. با شروع اجرای جدید هر مدل، اجرای قبلی همان
مدل روی هاست پاک می‌شود.

اجرا:
    pip install "omfiles[fsspec]" s3fs requests numpy scipy pillow matplotlib
    python nwp_maps.py --host https://wtafkik.ir/online --key کلید_آپلود --loop --max-minutes 340
آزمون بدون اینترنت (دادهٔ ساختگی، خروجی در پوشهٔ out):  python nwp_maps.py --mock --out out --models ecmwf --max-steps 6
"""
import argparse, io, json, math, os, sys, time, threading, traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import numpy as np
from scipy.ndimage import map_coordinates, gaussian_filter
from PIL import Image, ImageDraw, ImageFont
import styles

UTC = timezone.utc
HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, 'assets') if os.path.isdir(os.path.join(HERE, 'assets')) else HERE   # پوشهٔ assets یا کنار همین فایل
def log(s): print(datetime.now(UTC).strftime('%H:%M:%S ') + s, flush=True)

# ---------------------------------------------------------------- مدل‌ها و شبکه‌ها
# (nx, ny, latMin, lonMin, dx, dy) — همان تعریف سرور Open-Meteo
GRIDS = {
    'ecmwf_ifs025': (1440, 721, -90, -180, 0.25, 0.25),
    'ncep_gfs013': (3072, 1536, -0.11714935 * 1535 / 2, -180, 360 / 3072, 0.11714935),
    'ncep_gfs025': (1440, 721, -90, -180, 0.25, 0.25),
    'dwd_icon': (2879, 1441, -90, -180, 0.125, 0.125),
    'dwd_icon_eu': (1377, 657, 29.5, -23.5, 0.0625, 0.0625),
    'ukmo_global_deterministic_10km': (2560, 1920, -90, -180, 360 / 2560, 180 / 1920),
    'cmc_gem_gdps_15km': (2400, 1201, -90, -180, 0.15, 0.15),
    'cmc_gem_gdps': (2400, 1201, -90, -180, 0.15, 0.15),
    'meteofrance_arpege_world025': (1440, 721, -90, -180, 0.25, 0.25),
    'bom_access_global': (2048, 1536, -89.941406, -179.912109, 360 / 2048, 180 / 1536),
}
BBOX = (41.0, 67.0, 22.5, 42.0)        # lon0, lon1, lat0, lat1 — پوشش هر دو کادر ایران و فارس با حاشیه
MODELS = {
    'ecmwf':  dict(name='ECMWF', label='ECMWF IFS 0.25°', dom=['ecmwf_ifs025'], prov='ECMWF', hz={'00': 240, '12': 240, '06': 144, '18': 144}),
    'gfs':    dict(name='GFS', label='GFS 0.11° (13 km)', dom=['ncep_gfs013'], upper='ncep_gfs025', prov='NOAA NCEP', hz=240),
    'icon':   dict(name='ICON', label='ICON-EU 7 km + ICON 13 km', dom=['dwd_icon'], hires='dwd_icon_eu', prov='DWD', hz={'00': 180, '12': 180, '06': 120, '18': 120}),
    'ukmo':   dict(name='UKMO', label='UKMO Global 10 km', dom=['ukmo_global_deterministic_10km'], prov='Met Office', hz=168),
    'gem':    dict(name='GEM', label='GEM GDPS 15 km', dom=['cmc_gem_gdps_15km', 'cmc_gem_gdps'], prov='ECCC', hz=240),
    'arpege': dict(name='ARPEGE', label='ARPEGE 0.25°', dom=['meteofrance_arpege_world025'], prov='Météo-France', hz=102),
    'access': dict(name='ACCESS', label='ACCESS-G (BoM)', dom=['bom_access_global'], prov='BoM', hz=240),
}
REGIONS = {'iran': dict(geo=(-1506.432, 35.07327, 1611.463, -40.47955), en='Iran', fa='ایران'),
           'fars': dict(geo=(-4387.15, 89.808, 3366.55, -103.991), en='Fars', fa='فارس')}
FX0, FX1, FY0, FY1 = 2, 757, 2, 614
W = H = 760

# ---------------------------------------------------------------- پارامترها
ALL = list(MODELS)
PR_WIN = [  # (id, ساعت پنجره، بازهٔ ساعت پایان، تا چه ساعتی، نوع نوار)
    ('p1', 1, 1, 72, 'h1'), ('p3', 3, 3, 144, 'h24'), ('p6', 6, 6, 999, 'h24'), ('p24', 24, 6, 999, 'h24'),
    ('p48', 48, 24, 999, 'acc'), ('p120', 120, 24, 999, 'acc'), ('pacc', 0, 6, 999, 'acc')]
SN_WIN = [('s1', 1, 1, 72, 'h1'), ('s3', 3, 3, 144, 'h24'), ('s6', 6, 6, 999, 'h24'), ('snow24', 24, 6, 999, 'h24'),
          ('s48', 48, 24, 999, 'acc'), ('s120', 120, 24, 999, 'acc'), ('snowacc', 0, 6, 999, 'acc')]
PARAMS = {}
FAD = str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹')
for pid, hh, ev, mx, v in PR_WIN:
    PARAMS[pid] = dict(kind='win', var='precip', h=hh, every=ev, maxlead=mx, sc='pr_' + v, wv=v, models=ALL, unit='mm',
                       en=(f'Precipitation, {hh}h (mm)' if hh else 'Total precipitation (mm)'),
                       fa=(f'بارش {hh} ساعته'.translate(FAD) if hh else 'بارش تجمعی از ابتدای اجرا'))
for pid, hh, ev, mx, v in SN_WIN:
    PARAMS[pid] = dict(kind='win', var='snow', h=hh, every=ev, maxlead=mx, sc='pr_' + v, wv=v, models=ALL, unit='mm', skip_empty=True,
                       en=(f'Snowfall (water equivalent), {hh}h (mm)' if hh else 'Total snowfall (water equivalent) (mm)'),
                       fa=(f'بارش برف {hh} ساعته (آب معادل)'.translate(FAD) if hh else 'برف تجمعی (آب معادل)'))
I_EC = ['ecmwf']; I_ECA = ['ecmwf', 'access']; I_ECG = ['ecmwf', 'gfs']
PARAMS.update({
    't2m':   dict(kind='inst', sc='temp', models=I_ECA, unit='°C', en='Temperature 2m (°C)', fa='دمای ۲ متری', lapse=True),
    'tsoil': dict(kind='inst', sc='temp', models=I_ECA, unit='°C', en='Soil temperature near surface (°C)', fa='دمای خاک نزدیک سطح'),
    'tmax':  dict(kind='day', sc='temp', models=I_ECA, unit='°C', en='Max. temperature 2m, 24h (°C)', fa='دمای بیشینه ۲۴ ساعته', lapse=True),
    'tmin':  dict(kind='day', sc='temp', models=I_ECA, unit='°C', en='Min. temperature 2m, 24h (°C)', fa='دمای کمینه ۲۴ ساعته', lapse=True),
    'tanom': dict(kind='day', sc='tanom', models=I_ECA, unit='K', en='Temperature 2m anomaly, daily mean (K)', fa='انومالی دمای ۲ متری (میانگین روزانه)', lapse=True),
    'rh':    dict(kind='inst', sc='rh', models=I_EC, unit='%', en='Relative humidity 2m (%)', fa='رطوبت نسبی ۲ متری'),
    'pw':    dict(kind='inst', sc='pw', models=I_EC, unit='mm', en='Entire Atmosphere Precipitable Water (mm)', fa='آب قابل بارش'),
    'ki':    dict(kind='inst', sc='ki', models=I_ECG, unit='', en='K-Index', fa='شاخص K'),
    'cape':  dict(kind='inst', sc='cape', models=I_ECG, unit='J/kg', en='CAPE (J/kg)', fa='CAPE'),
    'si':    dict(kind='inst', sc='si', models=I_ECG, unit='', en='Soaring Index', fa='شاخص سورینگ'),
    'gdi':   dict(kind='inst', sc='gdi', models=I_ECG, unit='', en='Galvez-Davison Index (GDI)', fa='شاخص گالوز-داویسون'),
    'refl':  dict(kind='inst', sc='refl', models=I_ECG, unit='dBZ', en='Base reflectivity (dBZ), from precip. rate', fa='بازتاب پایه (برآورد از شدت بارش)'),
    'li':    dict(kind='inst', sc='li', models=I_ECG, unit='K', en='Lifted Index (surface)', fa='شاخص صعود (Lifted)'),
    'sigwx': dict(kind='inst', sc='sigwx', models=I_ECG, unit='', en='Significant Weather', fa='هوای مهم'),
    'cin':   dict(kind='inst', sc='cin', models=['gfs'], unit='J/kg', en='CIN (J/kg)', fa='CIN'),
    'gust':  dict(kind='inst', sc='gust', models=I_EC, unit='km/h', en='Wind gusts 10m (km/h)', fa='تندباد ۱۰ متری'),
    'sfc':   dict(kind='inst', sc='rh', models=I_EC, unit='%', en='MSLP (hPa), RH 2m (%) & wind 10m', fa='فشار سطح دریا، رطوبت و باد سطحی', cont='msl', barbs='10m'),
    'z850':  dict(kind='inst', sc='rh', models=I_EC, unit='%', en='Geopotential 850 hPa (gpdm) & RH 850 hPa (%)', fa='ارتفاع ژئوپتانسیل و رطوبت ۸۵۰ هکتوپاسکال', cont='gh850'),
    'z700':  dict(kind='inst', sc='rh', models=I_EC, unit='%', en='Geopotential 700 hPa (gpdm) & RH 700 hPa (%)', fa='ارتفاع ژئوپتانسیل و رطوبت ۷۰۰ هکتوپاسکال', cont='gh700'),
    'z500':  dict(kind='inst', sc='rh', models=I_EC, unit='%', en='Geopotential 500 hPa (gpdm) & RH 500 hPa (%)', fa='ارتفاع ژئوپتانسیل و رطوبت ۵۰۰ هکتوپاسکال', cont='gh500'),
    'w500':  dict(kind='inst', sc='windu', models=I_EC, unit='km/h', en='Wind 500 hPa (km/h)', fa='سرعت و جهت باد ۵۰۰ هکتوپاسکال', barbs='500hPa'),
    'w300':  dict(kind='inst', sc='windu', models=I_EC, unit='km/h', en='Wind 300 hPa (km/h)', fa='سرعت و جهت باد ۳۰۰ هکتوپاسکال', barbs='300hPa'),
})

# ---------------------------------------------------------------- فیزیک
def es_hpa(tc): return 6.112 * np.exp(17.67 * tc / (tc + 243.5))
def td_from_rh(tc, rh):
    rh = np.clip(rh, 1, 100); g = np.log(rh / 100) + 17.67 * tc / (tc + 243.5); return 243.5 * g / (17.67 - g)
def mixr(tc, rh, p): e = es_hpa(tc) * np.clip(rh, 0, 100) / 100; return 0.622 * e / np.maximum(p - e, 1)

def k_index(T, R):
    td850, td700 = td_from_rh(T[850], R[850]), td_from_rh(T[700], R[700])
    return (T[850] - T[500]) + td850 - (T[700] - td700)
def soaring_index(T, R):
    return (T[850] - T[500]) - (T[700] - td_from_rh(T[700], R[700]))
def gdi(T, R, psfc):
    """Gálvez & Davison (2016)؛ تراز ۹۵۰ با ۹۲۵ هکتوپاسکال جایگزین شده (در داده‌های باز ۹۵۰ نیست)."""
    K = {p: T[p] + 273.15 for p in T}; L0, cp = 2.69e6, 1005.7
    th = lambda p: K[p] * (1000.0 / p) ** (2 / 7)
    rA, rB, rC = mixr(T[925], R[925], 925), 0.5 * (mixr(T[850], R[850], 850) + mixr(T[700], R[700], 700)), mixr(T[500], R[500], 500)
    thA, thB, thC = th(925), 0.5 * (th(850) + th(700)), th(500)
    eA = thA * np.exp(L0 * rA / (cp * K[850])); eB = thB * np.exp(L0 * rB / (cp * K[850])); eC = thC * np.exp(L0 * rC / (cp * K[850])) - 10
    ME = eC - 303.0; LE = 0.5 * (eA + eB) - 303.0
    CBI = np.where(LE > 0, 6.5e-2 * ME * LE, 0.0)
    MWI = np.where(K[500] > 263.15, -7.0 * (K[500] - 263.15), 0.0)
    SD = (K[925] - K[700]) + (eB - eA); II = np.where(SD < 0, 1.5 * SD, 0.0)
    TC = 18.0 - 9000.0 / np.maximum(psfc - 500.0, 50.0)
    return CBI + MWI + II + TC
def lifted_index(t2, rh2, ps, t500):
    """ذرهٔ سطحی: صعود خشک تا LCL (Bolton) و سپس شبه‌بی‌دررو تا ۵۰۰ هکتوپاسکال."""
    T = t2 + 273.15; Td = td_from_rh(t2, rh2) + 273.15
    Tl = 1 / (1 / np.maximum(Td - 56, 1) + np.log(T / Td) / 800) + 56
    pl = ps * (Tl / T) ** 3.5
    t = Tl.copy(); p = pl.copy(); steps = 40; dp = (p - 500.0) / steps
    for _ in range(steps):
        tc = t - 273.15; r = mixr(tc, 100, p); Lv = 2.501e6
        g = (287.04 * t + Lv * r) / (1005.7 + Lv * Lv * r * 0.622 / (287.04 * t * t)) / p
        t = t - g * dp; p = p - dp
    li = (t500 + 273.15) - t
    return np.where(ps > 520, li, np.nan)
def refl_from_rate(rate_mmh, snow_frac):
    r = np.maximum(rate_mmh, 0); Zr = 200 * r ** 1.6; Zs = 2000 * r ** 2.0
    Z = Zr * (1 - snow_frac) + Zs * snow_frac
    return np.where(r >= 0.05, 10 * np.log10(np.maximum(Z, 1e-3)), np.nan)
def snow_frac_t(t2): return np.clip((2.0 - t2) / 2.0, 0, 1)
def sig_weather(rate, sfrac, t2, rh2, cape, ptype=None, wind=None):
    out = np.full(rate.shape, np.nan)
    fog = (rh2 >= 97) & (rate < 0.1) & ((wind is None) | (wind < 3) if wind is not None else True)
    out[fog] = 0; out[fog & (t2 < 0)] = 1
    pr = rate >= 0.1
    lvl = np.where(rate < 1, 0, np.where(rate < 4, 1, 2))
    mix = pr & (sfrac > 0.2) & (sfrac < 0.8); sn = pr & (sfrac >= 0.8); rn = pr & (sfrac <= 0.2)
    if ptype is not None:
        ice = pr & np.isin(np.round(ptype), [3, 8, 12]); rn &= ~ice; sn &= ~ice; mix &= ~ice
        out[ice] = np.where(lvl[ice] == 0, 13, 14)
    out[rn] = 2 + lvl[rn]; out[mix] = np.where(lvl[mix] == 0, 8, 9); out[sn] = 10 + lvl[sn]
    if cape is not None:
        ts = (cape >= 300) & (rate >= 0.3) & ~sn
        out[ts & (cape < 1000)] = 6; out[ts & (cape >= 1000) & (rate >= 4)] = 7; out[ts & (cape >= 1000) & (rate < 4)] = 6
        near = (cape >= 500) & (rate >= 0.05) & (rate < 0.3) & np.isnan(out); out[near] = 5
    return out

# ---------------------------------------------------------------- خواندن داده
class S3Source:
    """یک «دامنه» (مثلاً ecmwf_ifs025) در s3://openmeteo/data_spatial — فقط زیرکادر ایران خوانده می‌شود."""
    def __init__(s, dom, fs):
        s.dom, s.fs = dom, fs
        nx, ny, la0, lo0, dx, dy = GRIDS[dom]
        s.nx, s.ny, s.la0, s.lo0, s.dx, s.dy = nx, ny, la0, lo0, dx, dy
        lon0, lon1, lat0, lat1 = BBOX
        s.i0 = max(0, int(math.floor((lon0 - lo0) / dx))); s.i1 = min(nx, int(math.ceil((lon1 - lo0) / dx)) + 1)
        s.j0 = max(0, int(math.floor((lat0 - la0) / dy))); s.j1 = min(ny, int(math.ceil((lat1 - la0) / dy)) + 1)
        s._hsurf = None
    def jget(s, path):
        try: return json.loads(s.fs.cat_file(f'openmeteo/data_spatial/{s.dom}/{path}'))
        except Exception: return None
    def runs(s):
        return [j for j in (s.jget('in-progress.json'), s.jget('latest.json')) if j and j.get('reference_time')]
    def tpath(s, run, vt):
        return (f'openmeteo/data_spatial/{s.dom}/{run:%Y/%m/%d/%H%M}Z/{vt:%Y-%m-%dT%H%M}.om')
    def exists(s, run, vt):
        try: return s.fs.exists(s.tpath(run, vt))
        except Exception: return False
    def _open(s, path):
        import fsspec, tempfile
        from omfiles import OmFileReader
        cache = os.path.join(tempfile.gettempdir(), 'omcache', s.dom)
        of = fsspec.open(f'blockcache::s3://{path}', mode='rb', s3={'anon': True, 'default_block_size': 262144},
                         blockcache={'cache_storage': cache, 'check_files': False})
        return OmFileReader(of), cache
    def read(s, run, vt, names):
        import shutil
        out = {}
        r, cache = s._open(s.tpath(run, vt))
        try:
            for n in names:
                try: c = r.get_child_by_name(n)
                except Exception: continue
                shp = tuple(c.shape)
                if shp != (s.ny, s.nx):
                    log(f'  ! {s.dom}/{n}: ابعاد {shp} با شبکهٔ تعریف‌شده {(s.ny, s.nx)} فرق دارد؛ رد شد'); continue
                out[n] = np.asarray(c[s.j0:s.j1, s.i0:s.i1], dtype=np.float32)
        finally:
            try: r.close()
            except Exception: pass
            shutil.rmtree(cache, ignore_errors=True)
        return out
    def hsurf(s):
        if s._hsurf is not None: return s._hsurf if s._hsurf is not False else None
        try:
            r, cache = s._open(f'openmeteo/data/{s.dom}/static/HSURF.om')
            node = r
            if not r.is_array:
                node = r.get_child_by_index(0)
            a = np.asarray(node[s.j0:s.j1, s.i0:s.i1], dtype=np.float32)
            a[a < -100] = 0; s._hsurf = a
        except Exception as e:
            log(f'  ارتفاع مدل {s.dom} خوانده نشد ({e}); اصلاح ارتفاعی دما انجام نمی‌شود'); s._hsurf = False
        return s._hsurf if s._hsurf is not False else None

class MockSource(S3Source):
    """دادهٔ ساختگی برای آزمون محلی (بدون اینترنت)."""
    def __init__(s, dom, seed=0):
        super().__init__(dom, None); s.seed = seed
    def runs(s):
        now = datetime.now(UTC); r = datetime(now.year, now.month, now.day, tzinfo=UTC)
        step = 1 if 'gfs' in s.dom or 'icon' in s.dom or 'ukmo' in s.dom or 'bom' in s.dom else 3
        vts = [(r + timedelta(hours=h)).strftime('%Y-%m-%dT%H:%MZ') for h in range(0, 145, step)]
        return [dict(reference_time=r.strftime('%Y-%m-%dT%H:%M:%SZ'), valid_times=vts, completed=True)]
    def exists(s, run, vt): return True
    def read(s, run, vt, names):
        ny, nx = s.j1 - s.j0, s.i1 - s.i0
        lat = s.la0 + (np.arange(s.j0, s.j1)) * s.dy; lon = s.lo0 + (np.arange(s.i0, s.i1)) * s.dx
        LON, LAT = np.meshgrid(lon, lat); h = (vt - run).total_seconds() / 3600
        rnd = np.random.default_rng(abs(hash((s.dom, h))) % 2**32)
        blob = np.exp(-((LON - 50 - h / 20) ** 2 + (LAT - 33) ** 2) / 8) + 0.5 * np.exp(-((LON - 58) ** 2 + (LAT - 28 + h / 40) ** 2) / 4)
        base = 30 - 0.9 * (LAT - 25) + 5 * np.sin(h / 24 * 2 * np.pi)
        out = {}
        for n in names:
            if n in ('precipitation', 'snowfall_water_equivalent'):
                v = np.maximum(0, 6 * blob - 0.8 + rnd.normal(0, .2, blob.shape)) * (0.4 if n.startswith('snow') else 1)
            elif n.startswith('temperature_2m'): v = base + (2 if n.endswith('max') else -2 if n.endswith('min') else 0)
            elif n.startswith('soil_temperature'): v = base + 3
            elif n.startswith('surface_temperature'): v = base + 4
            elif n.startswith('relative_humidity'): v = np.clip(30 + 60 * blob, 1, 100)
            elif n == 'cape': v = 2500 * blob
            elif n == 'convective_inhibition': v = -200 * blob
            elif n == 'lifted_index': v = 4 - 8 * blob
            elif n == 'total_column_integrated_water_vapour': v = 5 + 40 * blob
            elif n == 'pressure_msl': v = 1012 + 8 * np.sin(LON / 3) + 4 * np.cos(LAT / 2)
            elif n.startswith('wind_gusts'): v = 5 + 20 * blob
            elif n.startswith('wind_u'): v = 10 + 30 * blob * (2 if 'hPa' in n else 1)
            elif n.startswith('wind_v'): v = -5 + 10 * np.sin(LON / 4)
            elif n.startswith('wind_speed'): v = 8 + 10 * blob
            elif n.startswith('wind_direction'): v = 270 + 0 * blob
            elif n.startswith('geopotential_height_'):
                p = int(n.split('_')[-1][:-3]); v = {850: 1500, 700: 3100, 500: 5800, 300: 9400, 925: 800}.get(p, 1000) + 60 * np.sin(LON / 5) - 20 * (LAT - 30)
            elif n.startswith('temperature_') and n.endswith('hPa'):
                p = int(n.split('_')[-1][:-3]); v = base - (1000 - p) * 0.07 - 3
            elif n == 'precipitation_type': v = np.where(base < 2, 5, 1).astype(float)
            else: continue
            out[n] = v.astype(np.float32)
        return out
    def hsurf(s):
        lat = s.la0 + (np.arange(s.j0, s.j1)) * s.dy; lon = s.lo0 + (np.arange(s.i0, s.i1)) * s.dx
        LON, LAT = np.meshgrid(lon, lat); return (1200 * np.exp(-((LON - 52) ** 2 + (LAT - 32) ** 2) / 20)).astype(np.float32)

# ---------------------------------------------------------------- کادرهای خروجی (هم‌هندسه با weather.us)
class Region:
    def __init__(s, key):
        s.key = key; R = REGIONS[key]; ax, bx, ay, by = R['geo']; s.geo = R['geo']; s.en = R['en']
        xs = np.arange(FX0, FX1 + 1); ys = np.arange(FY0, FY1 + 1)
        s.lon, s.lat = np.meshgrid((xs - ax) / bx, (ys - ay) / by)
        s.dem = np.load(os.path.join(ASSETS, f'dem_{key}.npz'))['dem'].astype(np.float32)
        from scipy.ndimage import distance_transform_edt
        s.taper = np.clip(distance_transform_edt(~np.isnan(s.dem)) / 25.0, 0, 1).astype(np.float32)   # اصلاح ارتفاعی کم‌کم تا مرز DEM صفر می‌شود
        ov = Image.open(os.path.join(ASSETS, f'overlay_{key}.png')).convert('RGBA')
        s.overlay = ov
        s._idx = {}
    def smooth_dem(s, dx):
        k = ('dem', round(dx, 4))
        if k not in s._idx:
            from scipy.ndimage import uniform_filter
            n = max(1, int(round(dx * abs(s.geo[1]))))
            v = (~np.isnan(s.dem)).astype(np.float32); z = np.nan_to_num(s.dem)
            s._idx[k] = (uniform_filter(z, n) / np.maximum(uniform_filter(v, n), 1e-3)).astype(np.float32)
        return s._idx[k]
    def idx(s, src):
        k = src.dom
        if k not in s._idx:
            fi = (s.lon - src.lo0) / src.dx - src.i0
            fj = (s.lat - src.la0) / src.dy - src.j0
            s._idx[k] = np.array([fj, fi])
        return s._idx[k]
    def interp(s, src, a, order=1):
        if a is None: return None
        c = s.idx(src); m = np.isnan(a)
        if m.any():
            out = map_coordinates(np.where(m, 0, a), c, order=order, mode='nearest', prefilter=False)
            w = map_coordinates((~m).astype(np.float32), c, order=order, mode='nearest', prefilter=False)
            out = np.where(w > 0.5, out / np.maximum(w, 1e-6), np.nan)
            return out.astype(np.float32)
        return map_coordinates(a, c, order=order, mode='nearest', prefilter=False).astype(np.float32)

# ---------------------------------------------------------------- رسم
FONT_DIR = os.path.join(os.path.dirname(__import__('matplotlib').__file__), 'mpl-data', 'fonts', 'ttf')
def font(sz, bold=False): return ImageFont.truetype(os.path.join(FONT_DIR, 'DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf'), sz)
F_T, F_S, F_L, F_XS = font(21, True), font(12), font(10), font(9)
LOGO = None
def logo():
    global LOGO
    if LOGO is None:
        im = Image.open(os.path.join(ASSETS, 'logo.png')).convert('RGBA'); k = 46 / im.height
        LOGO = im.resize((max(1, int(im.width * k)), 46), Image.LANCZOS)
    return LOGO
def fmt_edge(v):
    if v == int(v): return str(int(v))
    return ('%.2f' % v).rstrip('0').rstrip('.')
def edges_txt(e): return ', '.join('-inf' if v == -math.inf else '+' if v == math.inf else fmt_edge(v) for v in e)
def classify(field, edges):
    e = np.asarray(edges[1:-1], dtype=np.float64)
    k = np.digitize(field, e, right=False); k = np.where(np.isnan(field), -1, k)
    if edges[0] != -math.inf: k = np.where(field < edges[0], 0, k)
    return k.astype(np.int16)

MPL_LOCK = threading.Lock()
MARK = [(16 * k + 8, 0x3C, (16 * k + 8) ^ 0xA5) for k in range(16)]

def render(region, field, sc_key, title, valid, run, model, unit, marker, cont=None, cont_lvls=None, cont_fmt='%d', barbs=None, note=None):
    sc = styles.SC[sc_key]; cols = [styles.hex2rgb(c) for c in sc['colors']]; n = len(cols)
    k = classify(field, sc['edges'])
    pal = np.array(cols + [(255, 255, 255)], dtype=np.uint8)          # -1 → سفید (بدون داده / بدون پدیده)
    img = np.full((H, W, 3), 255, np.uint8)
    img[FY0:FY1 + 1, FX0:FX1 + 1] = pal[np.where(k < 0, n, k)]
    data_mask = np.zeros((H, W), bool); data_mask[FY0:FY1 + 1, FX0:FX1 + 1] = True
    im = Image.fromarray(img, 'RGB').convert('RGBA')
    # خطوط هم‌مقدار و بردار باد (matplotlib، فقط داخل کادر)
    if cont is not None or barbs is not None:
      with MPL_LOCK:
        import matplotlib; matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        fig = plt.figure(figsize=(W / 100, H / 100), dpi=100); ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, W); ax.set_ylim(H, 0); ax.axis('off')
        fig.patch.set_alpha(0); ax.patch.set_alpha(0)
        X, Y = np.meshgrid(np.arange(FX0, FX1 + 1), np.arange(FY0, FY1 + 1))
        if cont is not None:
            cs = ax.contour(X, Y, gaussian_filter(np.nan_to_num(cont, nan=np.nanmean(cont)), 2), levels=cont_lvls, colors='k', linewidths=0.9)
            ax.clabel(cs, fmt=cont_fmt, fontsize=7, inline=True)
        if barbs is not None:
            bu, bv = barbs; st = 34
            ax.barbs(X[st // 2::st, st // 2::st], Y[st // 2::st, st // 2::st], bu[st // 2::st, st // 2::st], -bv[st // 2::st, st // 2::st],
                     length=5.2, linewidth=0.7, color='#202020', barb_increments=dict(half=5, full=10, flag=50))
        buf = io.BytesIO(); fig.savefig(buf, format='png', dpi=100, transparent=True); plt.close(fig); buf.seek(0)
        layer = Image.open(buf).convert('RGBA').resize((W, H))
        clip = Image.new('L', (W, H), 0); ImageDraw.Draw(clip).rectangle([FX0, FY0, FX1, FY1], fill=255)
        la = np.array(layer); la[..., 3] = np.minimum(la[..., 3], np.array(clip)); im.alpha_composite(Image.fromarray(la))
    # مرزها و نام‌ها (لایهٔ MetStat)
    ov = np.array(region.overlay); ov[:FY0] = 0; ov[FY1 + 1:] = 0; ov[:, :FX0] = 0; ov[:, FX1 + 1:] = 0
    im.alpha_composite(Image.fromarray(ov))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, W - 1, FY1 + 1], outline=(0, 0, 0), width=2)
    # عنوان و زمان
    d.text((14, 630), title, font=F_T, fill=(0, 0, 0))
    vi = valid + timedelta(hours=3, minutes=30)
    t2 = vi.strftime('%a %Y-%m-%d, %H:%M') + ' GMT+0330'
    d.text((W - 10, 629), 'Valid for', font=F_S, fill=(0, 0, 0), anchor='ra'); d.text((W - 10, 645), t2, font=F_S, fill=(0, 0, 0), anchor='ra')
    # نوار رنگ: ردیف ۶۸۰ فقط رنگ‌های نوار (بدون خط جداکننده) — همان چیدمان weather.us
    bx0, bx1, by0, by1 = 3, 757, 671, 689
    xs = [round(bx0 + (bx1 + 1 - bx0) * j / n) for j in range(n + 1)]
    for j in range(n): d.rectangle([xs[j], by0, xs[j + 1] - 1, by1], fill=cols[j])
    d.line([bx0, by0 - 1, bx1, by0 - 1], fill=(0, 0, 0)); d.line([bx0, by1 + 1, bx1, by1 + 1], fill=(0, 0, 0))
    lastx = -99; e = sc['edges']
    for j in range(1, n):
        lab = fmt_edge(e[j]) if sc_key != 'sigwx' else None
        if lab is None: continue
        w = d.textlength(lab, font=F_L)
        if xs[j] - w / 2 < lastx + 4: continue
        d.line([xs[j], by1 + 1, xs[j], by1 + 3], fill=(0, 0, 0)); d.text((xs[j], by1 + 4), lab, font=F_L, fill=(0, 0, 0), anchor='ma'); lastx = xs[j] + w / 2
    if sc_key == 'sigwx':
        for j, nm in enumerate(styles.SIG_NAMES):
            cx = (xs[j] + xs[j + 1]) / 2; d.text((cx, by1 + 4), nm, font=F_XS, fill=(0, 0, 0), anchor='ma')
    # پانویس
    d.text((14, 718), region.en, font=F_S, fill=(0, 0, 0))
    d.text((14, 736), f"{model['label']} {run:%H}z from {run:%Y-%m-%d/%H}z", font=F_S, fill=(0, 0, 0))
    lg = logo(); im.alpha_composite(lg, (W - lg.width - 8, 708))
    d.text((W - lg.width - 16, 742), f"Data: {model['prov']} · Open-Meteo.com (CC BY 4.0)", font=F_XS, fill=(60, 60, 60), anchor='ra')
    if note: d.text((W - lg.width - 16, 728), note, font=F_XS, fill=(60, 60, 60), anchor='ra')
    rgb = np.array(im.convert('RGB'))
    # نشانهٔ ماشینی (دو ردیف پایین): مشخصات نقشه برای سایت
    raw = json.dumps(marker, separators=(',', ':'), ensure_ascii=True).encode()
    raw = b'MS2' + len(raw).to_bytes(2, 'big') + raw
    nib = [v for b in raw for v in (b >> 4, b & 15)]
    assert len(nib) <= 2 * W, 'marker too long'
    for i, v in enumerate(nib):                     # ۱۶ رنگ رزرو: هر پیکسل نیم‌بایت
        rgb[H - 2 + i // W, i % W] = MARK[v]
    return png_exact(rgb, cols + MARK)

def png_exact(rgb, data_cols):
    flat = rgb.reshape(-1, 3); key = (flat[:, 0].astype(np.int32) << 16) | (flat[:, 1].astype(np.int32) << 8) | flat[:, 2]
    uk, inv = np.unique(key, return_inverse=True)
    if len(uk) <= 256:
        pal = np.stack([(uk >> 16) & 255, (uk >> 8) & 255, uk & 255], 1).astype(np.uint8)
        im = Image.fromarray(inv.reshape(rgb.shape[:2]).astype(np.uint8), 'P'); im.putpalette(pal.ravel().tolist())
    else:
        dk = {(c[0] << 16) | (c[1] << 8) | c[2] for c in data_cols} | {0xFFFFFF, 0}
        isd = np.isin(key, list(dk)); dlist = sorted(dk); nd = len(dlist)
        rest = flat[~isd]; K = 256 - nd
        q = Image.fromarray(rest.reshape(1, -1, 3), 'RGB').quantize(colors=K, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
        qp = np.array(q.getpalette()[:3 * K], dtype=np.uint8).reshape(-1, 3); qi = np.array(q).ravel()
        idx = np.empty(len(key), np.int32)
        dmap = {k: i for i, k in enumerate(dlist)}; idx[isd] = np.vectorize(dmap.get)(key[isd]); idx[~isd] = nd + qi
        pal = np.concatenate([np.array([[(k >> 16) & 255, (k >> 8) & 255, k & 255] for k in dlist], np.uint8), qp])
        im = Image.fromarray(idx.reshape(rgb.shape[:2]).astype(np.uint8), 'P'); im.putpalette(pal.ravel().tolist())
    b = io.BytesIO(); im.save(b, 'PNG', optimize=True); return b.getvalue()

# ---------------------------------------------------------------- اقلیم (برای انومالی) و تبدیل تاریخ
CLIM = None
def jalali_month_pos(d):
    """جای روز در سال هجری خورشیدی بر حسب ماه (۰ = وسط فروردین)."""
    y = d.year if (d.month, d.day) >= (3, 21) else d.year - 1
    doy = (d - datetime(y, 3, 21, tzinfo=UTC)).total_seconds() / 86400
    if doy < 186: return doy / 31 - 0.5
    return 6 + (doy - 186) / 30 - 0.5
def clim_at(region, d):
    if CLIM is None: return None
    co = CLIM[region.key + '_coef']; rs = CLIM[region.key + '_res'].astype(np.float32)
    m = jalali_month_pos(d); a = int(math.floor(m)) % 12; b = (a + 1) % 12; u = m - math.floor(m)
    def one(k):
        c = co[k]; r = np.kron(rs[k], np.ones((4, 4), np.float32))[:region.dem.shape[0], :region.dem.shape[1]]
        return c[0] + c[1] * region.dem + c[2] * region.lat + c[3] * region.lon + r
    return one(a) * (1 - u) + one(b) * u

# ---------------------------------------------------------------- پردازش یک اجرای یک مدل
SFC_VARS = {'precipitation', 'snowfall_water_equivalent', 'temperature_2m', 'temperature_2m_max', 'temperature_2m_min', 'relative_humidity_2m'}
class RunState:
    def __init__(s, model, run, srcs):
        s.model, s.run, s.srcs = model, run, srcs
        s.cum = {d: {'precip': None, 'snow': None} for d in srcs}
        s.hist = {d: {} for d in srcs}          # zamān → (cum_p, cum_s)
        s.tser = {d: {} for d in srcs}          # zamān → (t2, tmax, tmin) برای مدل‌های دمای روزانه
        s.done_i = -1; s.prev_vt = None; s.wait_hi = {}

def needed_vars(mkey, pids, kind):
    v = {'precipitation', 'snowfall_water_equivalent'}
    if mkey in ('ecmwf', 'access', 'ukmo'): v |= {'temperature_2m'}
    if mkey == 'ecmwf': v |= {'temperature_2m_max', 'temperature_2m_min'}
    if kind == 'all':
        if mkey == 'ecmwf':
            v |= {'relative_humidity_2m', 'total_column_integrated_water_vapour', 'cape', 'pressure_msl', 'wind_gusts_10m', 'wind_u_component_10m', 'wind_v_component_10m',
                  'soil_temperature_0_to_7cm', 'precipitation_type'}
            for p in (925, 850, 700, 500, 300):
                v |= {f'temperature_{p}hPa', f'relative_humidity_{p}hPa', f'geopotential_height_{p}hPa', f'wind_u_component_{p}hPa', f'wind_v_component_{p}hPa'}
        if mkey == 'access': v |= {'soil_temperature_0_to_10cm'}
        if mkey == 'gfs': v |= {'cape', 'lifted_index', 'convective_inhibition', 'relative_humidity_2m', 'pressure_msl'}
    return v

class Uploader:
    def __init__(s, host, key, outdir):
        s.host, s.key, s.out = (host.rstrip('/') if host else None), key, outdir
        s.pool = ThreadPoolExecutor(4); s.fut = []; s.lock = threading.Lock(); s.n = 0
    def call(s, a, js=None, files=None, data=None):
        if not s.host: return {'have': [], 'ok': True}
        import requests
        u = f"{s.host}/nwp_upload.php?a={a}&key={requests.utils.quote(s.key)}"
        for k in range(5):
            try:
                r = requests.post(u, json=js, files=files, data=data, timeout=90)
                if r.status_code == 403: sys.exit('✗ کلید آپلود اشتباه است (upload_key در mx_config.json روی هاست)')
                try: return r.json()
                except Exception: raise RuntimeError(f'HTTP {r.status_code} [{a}] پاسخ: {r.text[:160]!r}')
            except SystemExit: raise
            except Exception as e:
                log(f'  … خطای ارتباط با هاست ({e}); تلاش دوباره'); time.sleep(5 * (k + 1))
        return {}
    def put(s, mkey, run, fname, png, meta):
        def job():
            if s.out:
                p = os.path.join(s.out, mkey, run, fname); os.makedirs(os.path.dirname(p), exist_ok=True); open(p, 'wb').write(png)
            if s.host:
                r = s.call('put', data={'model': mkey, 'run': run, 'file': fname, 'meta': json.dumps(meta)}, files={'png': (os.path.basename(fname), png, 'image/png')})
                if not r.get('ok'): log(f'  ✗ آپلود {mkey}/{fname}: {r}'); return
            with s.lock: s.n += 1
        s.fut.append(s.pool.submit(job))
    def wait(s):
        for f in s.fut:
            try: f.result()
            except Exception as e: log(f'  ✗ {e}')
        s.fut = []

def leads(vts, run): return [int(round((v - run).total_seconds() / 3600)) for v in vts]

class Engine:
    def __init__(s, a):
        s.a = a; s.state = {}; s.fs = None
        s.regions = {k: Region(k) for k in a.regions}
        s.up = Uploader(a.host, a.key, a.out)
        if not a.mock:
            import s3fs
            s.fs = s3fs.S3FileSystem(anon=True, default_block_size=65536)
    def src(s, dom):
        return MockSource(dom) if s.a.mock else S3Source(dom, s.fs)
    def pick_dom(s, mkey):
        for d in MODELS[mkey]['dom']:
            S = s.src(d); r = S.runs()
            if r: return S, r
        return None, []
    def tick(s, mkey):
        M = MODELS[mkey]; S, runs = s.pick_dom(mkey)
        if not S: log(f'{M["name"]}: اطلاعات اجرا در دسترس نیست'); return False
        meta = max(runs, key=lambda j: j['reference_time'])
        run = datetime.strptime(meta['reference_time'][:16], '%Y-%m-%dT%H:%M').replace(tzinfo=UTC)
        rid = run.strftime('%Y%m%d%H')
        hz = M['hz'][run.strftime('%H')] if isinstance(M['hz'], dict) else M['hz']
        hz = min(hz, s.a.horizon or hz)
        vts = sorted({datetime.strptime(v[:16], '%Y-%m-%dT%H:%M').replace(tzinfo=UTC) for v in meta.get('valid_times', [])})
        vts = [v for v in vts if 0 <= (v - run).total_seconds() / 3600 <= hz]
        st = s.state.get(mkey)
        if not st or st.run != run:
            srcs = {'main': S}
            if M.get('hires'): srcs['hires'] = s.src(M['hires'])
            if M.get('upper'): srcs['upper'] = s.src(M['upper'])
            st = s.state[mkey] = RunState(mkey, run, srcs)
            r = s.up.call('begin', js={'model': mkey, 'run': rid, 'name': M['name'], 'label': M['label'],
                                       'params': {k: dict(fa=v['fa'], unit=v['unit']) for k, v in PARAMS.items() if mkey in v['models']}})
            st.have = set(r.get('have', [])); log(f'▶ {M["name"]} اجرای {rid}: {len(vts)} گام زمانی، {len(st.have)} نقشه از قبل روی هاست')
        made = 0; t0 = time.time()
        for i, vt in enumerate(vts):
            if i <= st.done_i: continue
            if s.a.max_steps and i >= s.a.max_steps: break
            if not S.exists(run, vt): break                     # هنوز منتشر نشده؛ دور بعد
            hs_ = st.srcs.get('hires')
            if hs_ is not None and (vt - run).total_seconds() <= 120 * 3600 and not hs_.exists(run, vt):
                first = st.wait_hi.setdefault(vt, time.time())
                if time.time() - first < 5400: break              # ICON-EU هنوز این گام را منتشر نکرده؛ تا ۹۰ دقیقه صبر
                log(f'  ICON-EU برای {vt:%Y%m%d%H} نرسید؛ ادامه فقط با ICON جهانی'); st.srcs.pop('hires'); st.hist.pop('hires', None)
            try:
                made += s.step(st, i, vt, vts)
            except Exception as e:
                traceback.print_exc(); log(f'  ✗ {M["name"]} {vt:%Y%m%d%H}: {e}'); break
            st.done_i = i; st.prev_vt = vt
        s.up.wait()
        if made: s.up.call('sync', js={'model': mkey, 'run': rid})
        if made: log(f'■ {M["name"]} {rid}: +{made} نقشه ({time.time() - t0:.0f} ثانیه)')
        complete = st.done_i >= len(vts) - 1 and meta.get('completed', True)
        if complete and not getattr(st, 'ended', False):
            s.up.call('end', js={'model': mkey, 'run': rid}); st.ended = True
        return made > 0

    # ---------- یک گام زمانی
    def step(s, st, i, vt, vts):
        mkey = st.model; M = MODELS[mkey]; run = st.run; lead = int(round((vt - run).total_seconds() / 3600))
        dt_h = (vt - vts[i - 1]).total_seconds() / 3600 if i > 0 else 0
        want_all = (lead % 3 == 0 and lead <= 120) or lead % 6 == 0
        mine = [p for p, P in PARAMS.items() if mkey in P['models']]
        D = {}
        for name, src in st.srcs.items():
            vars_ = needed_vars(mkey, mine, 'all' if want_all else 'acc')
            if name == 'upper':
                if not want_all: continue
                vars_ = {f'{q}_{p}hPa' for q in ('temperature', 'relative_humidity') for p in (925, 850, 700, 500)}
            if name == 'hires':
                vars_ = {'precipitation', 'snowfall_water_equivalent', 'temperature_2m'}
                if not src.exists(run, vt): continue
            if name == 'upper':
                try: D[name] = src.read(run, vt, sorted(vars_))
                except Exception as e: log(f'  ترازهای فشاری {src.dom} {vt:%Y%m%d%H} خوانده نشد ({e})')
                continue
            D[name] = src.read(run, vt, sorted(vars_))
        # جمع‌های تجمعی روی شبکهٔ اصلی هر منبع
        for name, d in D.items():
            if name == 'upper': continue
            p = d.get('precipitation'); sn = d.get('snowfall_water_equivalent')
            if p is None: continue
            if sn is None: sn = p * snow_frac_t(d['temperature_2m']) if 'temperature_2m' in d else np.zeros_like(p)
            c = st.cum[name]
            if i == 0 or c['precip'] is None: c['precip'] = np.zeros_like(p); c['snow'] = np.zeros_like(p)
            if i > 0: c['precip'] = c['precip'] + np.nan_to_num(np.maximum(p, 0)); c['snow'] = c['snow'] + np.nan_to_num(np.maximum(sn, 0))
            st.hist[name][vt] = (c['precip'].copy(), c['snow'].copy())
            if 'temperature_2m' in d and mkey in ('ecmwf', 'access'):
                st.tser[name][vt] = (d['temperature_2m'], d.get('temperature_2m_max', d['temperature_2m']), d.get('temperature_2m_min', d['temperature_2m']), dt_h)
        made = 0
        for rk, R in s.regions.items():
            for pid in mine:
                P = PARAMS[pid]
                job = s.plan(P, st, vt, lead, dt_h, want_all)
                if not job: continue
                fname = f'{rk}/{pid}_{vt:%Y%m%d%H}.png'
                if fname in st.have: continue
                try:
                    out = s.make(P, pid, st, D, R, vt, lead, dt_h)
                except Exception as e:
                    log(f'  ! {M["name"]} {pid} {vt:%Y%m%d%H}: {e}'); continue
                if out is None: continue
                field, extra = out
                if P.get('skip_empty') and not (np.nanmax(field) >= 0.1): continue
                sc = styles.SC[P['sc']]
                mk = dict(v=1, p=pid, m=M['name'], r=f'{run:%Y%m%d%H}', t=f'{vt:%Y%m%d%H}', g=rk, h=P.get('h', 0) if P['kind'] == 'win' else (24 if P['kind'] == 'day' else 0),
                          s=P['sc'], u=P['unit'], n=P['fa'], e=edges_txt(sc['edges']))
                if P.get('wv'): mk['wv'] = P['wv']
                title = P['en']
                if P['kind'] == 'win' and P['h'] == 0: title += f', {lead}h'
                png = render(R, field, P['sc'], title, vt, run, M, P['unit'], mk, **extra)
                meta = dict(r=rk, m=mkey, p=pid, s=lead, v=f'{vt:%Y%m%d%H}')
                s.up.put(mkey, f'{run:%Y%m%d%H}', fname, png, meta); st.have.add(fname); made += 1
        return made

    def plan(s, P, st, vt, lead, dt_h, want_all):
        if lead == 0 and P['kind'] != 'inst': return False
        if P['kind'] == 'win':
            if lead > P['maxlead']: return False
            if P['h'] >= 48:                                  # ۴۸ و ۱۲۰ ساعته: پایان هر روز ساعت ۰۶ UTC (۰۹:۳۰ ایران)
                t0 = vt - timedelta(hours=P['h'])
                return vt.hour == 6 and lead >= P['h'] and (t0 == st.run or t0 in st.hist['main'])
            if lead % P['every']: return False
            if P['h'] == 0: return lead > 0
            if lead < P['h']: return False
            t0 = vt - timedelta(hours=P['h'])
            if P['h'] in (1, 3, 6) and dt_h > P['h'] + 1e-6: return False
            return t0 == st.run or t0 in st.hist['main']
        if P['kind'] == 'day':
            return vt.hour == 18 and lead >= 24
        return want_all

    def acc(s, st, name, var, vt, h):
        Hh = st.hist[name]
        if vt not in Hh: return None
        c1 = Hh[vt][0 if var == 'precip' else 1]
        if h == 0: return c1
        t0 = vt - timedelta(hours=h)
        if t0 == st.run: return c1
        if t0 not in Hh: return None
        return c1 - Hh[t0][0 if var == 'precip' else 1]

    def blend(s, st, R, name_field):
        """ICON: ICON-EU (۷ کیلومتر) در محدودهٔ خودش، ICON جهانی در بقیه؛ نوار ۱ درجه‌ای برای اتصال نرم مقدار (نه رنگ)."""
        g = name_field('main'); out = R.interp(st.srcs['main'], g)
        if 'hires' in st.srcs:
            h = name_field('hires')
            if h is not None:
                hi = R.interp(st.srcs['hires'], h)
                w = np.clip((R.lat - 29.6) / 1.0, 0, 1) * np.clip((62.4 - R.lon) / 1.0, 0, 1) * np.clip((R.lon + 23.4) / 1.0, 0, 1)
                out = np.where(np.isnan(hi), out, out * (1 - w) + np.nan_to_num(hi) * w)
        return out

    def lapse(s, st, R, field_native_fn, full=False):
        """اصلاح ارتفاعی دما با DEM ایران (۳۰ ثانیه): T + 6.5 K/km × (ارتفاع مدل − ارتفاع واقعی)."""
        src = st.srcs['main']; T = R.interp(src, field_native_fn())
        hs = src.hsurf()
        zm = R.interp(src, hs) if hs is not None else R.smooth_dem(src.dx)   # بدون فایل ارتفاع مدل: DEM هموارشده به اندازهٔ سلول مدل
        w = np.where(np.isnan(R.dem), 0, 1) if full else R.taper
        return T + w * 0.0065 * (zm - np.nan_to_num(R.dem))

    def make(s, P, pid, st, D, R, vt, lead, dt_h):
        mkey = st.model; d = D.get('main', {}); src = st.srcs['main']
        I = lambda a, order=1: R.interp(src, a, order)
        if P['kind'] == 'win':
            f = s.blend(st, R, lambda nm: s.acc(st, nm, P['var'], vt, P['h']) if nm in st.hist else None)
            return (None if f is None else (np.maximum(f, 0), {}))
        if P['kind'] == 'day':
            ser = st.tser['main']; ts = sorted(t for t in ser if vt - timedelta(hours=24) < t <= vt)
            if len(ts) < 3: return None
            if pid == 'tmax':
                f = lambda: np.nanmax(np.stack([ser[t][1] for t in ts]), 0)
            elif pid == 'tmin':
                f = lambda: np.nanmin(np.stack([ser[t][2] for t in ts]), 0)
            else:
                f = lambda: np.nanmean(np.stack([ser[t][0] for t in ts]), 0)
            T = s.lapse(st, R, f, full=(pid == 'tanom'))
            if pid == 'tanom':
                C = clim_at(R, vt - timedelta(hours=12))
                if C is None: return None
                return (T - C, dict(note='Anomaly vs. 1991–2020 IRIMO station normals (Iran only)'))
            return (T, {})
        if pid == 't2m': return (s.lapse(st, R, lambda: d['temperature_2m']), {}) if 'temperature_2m' in d else None
        if pid == 'tsoil':
            v = d.get('soil_temperature_0_to_7cm', d.get('soil_temperature_0_to_10cm'))
            return (I(v), {}) if v is not None else None
        if pid == 'rh': return (I(d['relative_humidity_2m']), {}) if 'relative_humidity_2m' in d else None
        if pid == 'pw': return (I(d['total_column_integrated_water_vapour']), {}) if 'total_column_integrated_water_vapour' in d else None
        if pid == 'cape': return (I(d['cape']), {}) if 'cape' in d else None
        if pid == 'cin': return (I(d['convective_inhibition']), {}) if 'convective_inhibition' in d else None
        if pid == 'gust': return (I(d['wind_gusts_10m']) * 3.6, {}) if 'wind_gusts_10m' in d else None
        if pid in ('ki', 'si', 'gdi'):
            U = D.get('upper', d); us = st.srcs.get('upper', src)
            T = {p: U.get(f'temperature_{p}hPa') for p in (925, 850, 700, 500)}; Rh = {p: U.get(f'relative_humidity_{p}hPa') for p in (925, 850, 700, 500)}
            if any(T[p] is None or Rh[p] is None for p in (850, 700, 500)): return None
            if pid == 'ki': f = k_index(T, Rh)
            elif pid == 'si': f = soaring_index(T, Rh)
            else:
                if T[925] is None: return None
                hs = us.hsurf(); ps = 1013.25 * np.exp(-(hs if hs is not None else 0) / 8434.0)
                if 'pressure_msl' in d and us is src: ps = d['pressure_msl'] * np.exp(-(hs if hs is not None else 0) / 8434.0)
                f = gdi(T, Rh, ps)
            return (R.interp(us, f), {})
        if pid == 'li':
            if 'lifted_index' in d: return (I(d['lifted_index']), {})
            if 'temperature_500hPa' not in d: return None
            hs = src.hsurf(); ps = d['pressure_msl'] * np.exp(-(hs if hs is not None else 0) / 8434.0)
            return (I(lifted_index(d['temperature_2m'], d['relative_humidity_2m'], ps, d['temperature_500hPa'])), {})
        if pid in ('refl', 'sigwx'):
            if 'precipitation' not in d or dt_h <= 0: return None
            rate = d['precipitation'] / dt_h
            sn = d.get('snowfall_water_equivalent')
            sf = np.clip(sn / np.maximum(d['precipitation'], 1e-3), 0, 1) if sn is not None else (snow_frac_t(d['temperature_2m']) if 'temperature_2m' in d else np.zeros_like(rate))
            if pid == 'refl': return (I(refl_from_rate(rate, sf)), dict(note='Simulated from model precipitation rate (Z = 200·R^1.6)'))
            if 'temperature_2m' not in d or 'relative_humidity_2m' not in d: return None
            ws = np.hypot(d['wind_u_component_10m'], d['wind_v_component_10m']) if 'wind_u_component_10m' in d else None
            g = lambda x, o=1: None if x is None else I(x, o)
            sw = sig_weather(np.maximum(g(rate), 0), np.clip(g(sf), 0, 1), g(d['temperature_2m']), g(d['relative_humidity_2m']), g(d.get('cape')),
                             g(d.get('precipitation_type'), 0), g(ws))
            return (sw, {})
        if pid == 'sfc':
            if 'pressure_msl' not in d: return None
            u, v = I(d['wind_u_component_10m']) * 1.94384, I(d['wind_v_component_10m']) * 1.94384
            msl = I(d['pressure_msl']); lv = np.arange(940, 1060, 2)
            return (I(d['relative_humidity_2m']), dict(cont=msl, cont_lvls=lv, barbs=(u, v)))
        if pid in ('z850', 'z700', 'z500'):
            p = int(pid[1:]); g = d.get(f'geopotential_height_{p}hPa'); rh = d.get(f'relative_humidity_{p}hPa')
            if g is None or rh is None: return None
            step = 6 if p == 500 else 3
            return (I(rh), dict(cont=I(g) / 10, cont_lvls=np.arange(0, 1200, step)))
        if pid in ('w500', 'w300'):
            p = int(pid[1:]); u = d.get(f'wind_u_component_{p}hPa'); v = d.get(f'wind_v_component_{p}hPa')
            if u is None: return None
            U, V = I(u), I(v)
            return (np.hypot(U, V) * 3.6, dict(barbs=(U * 1.94384, V * 1.94384)))
        return None

def main():
    global CLIM
    ap = argparse.ArgumentParser()
    ap.add_argument('--host'); ap.add_argument('--key', default=''); ap.add_argument('--out')
    ap.add_argument('--loop', action='store_true'); ap.add_argument('--max-minutes', type=int, default=0)
    ap.add_argument('--models', default=','.join(MODELS)); ap.add_argument('--regions', default='iran,fars')
    ap.add_argument('--params', default='', help='فقط این پارامترها (با کاما)'); ap.add_argument('--mock', action='store_true'); ap.add_argument('--probe', action='store_true'); ap.add_argument('--max-steps', type=int, default=0); ap.add_argument('--horizon', type=int, default=0)
    a = ap.parse_args(); a.regions = a.regions.split(','); t_end = time.time() + a.max_minutes * 60 if a.max_minutes else None
    if not a.host and not a.out: sys.exit('--host یا --out لازم است')
    if a.params:
        keep = set(a.params.split(','))
        for k in list(PARAMS):
            if k not in keep: PARAMS.pop(k)
    eng = Engine(a)
    # اقلیم ایستگاهی (برای انومالی) فقط از هاست و با کلید خوانده می‌شود؛ در مخزن عمومی نیست
    try:
        if a.host:
            import requests
            r = requests.post(f"{a.host.rstrip('/')}/nwp_upload.php?a=clim&key={requests.utils.quote(a.key)}", timeout=120)
            if r.ok and r.content[:2] == b'PK': CLIM = dict(np.load(io.BytesIO(r.content)))
        elif os.path.exists(os.path.join(HERE, 'clim_tmean.npz')): CLIM = dict(np.load(os.path.join(HERE, 'clim_tmean.npz')))
    except Exception as e: log(f'اقلیم خوانده نشد ({e}); نقشهٔ انومالی ساخته نمی‌شود')
    models = [m for m in a.models.split(',') if m in MODELS]
    if a.probe:                                   # آزمون سریع دسترسی به داده‌ها (برای گزارش خطا)
        for m in models:
            M = MODELS[m]; doms = M['dom'] + [d for d in (M.get('hires'), M.get('upper')) if d]
            for d in doms:
                S = eng.src(d); rs = S.runs()
                if not rs: log(f'✗ {m}/{d}: فایل latest/in-progress پیدا نشد'); continue
                j = max(rs, key=lambda q: q['reference_time']); vs = j.get('valid_times', []); var = j.get('variables', [])
                log(f'✓ {m}/{d}: اجرای {j["reference_time"]} · {len(vs)} گام · {len(var)} متغیر · کامل={j.get("completed")}')
                need = needed_vars(m, [], 'all') if d in M['dom'] else set()
                miss = sorted(v for v in need if var and v not in var)
                if miss: log(f'   متغیرهای ناموجود: {", ".join(miss)}')
                try:
                    run = datetime.strptime(j['reference_time'][:16], '%Y-%m-%dT%H:%M').replace(tzinfo=UTC)
                    vt = datetime.strptime(vs[min(2, len(vs) - 1)][:16], '%Y-%m-%dT%H:%M').replace(tzinfo=UTC)
                    t0 = time.time(); r = S.read(run, vt, ['temperature_2m', 'precipitation'])
                    log('   ' + ', '.join(f'{k}: {v.shape} {np.nanmin(v):.1f}…{np.nanmax(v):.1f}' for k, v in r.items()) + f' ({time.time() - t0:.1f}s)')
                    hs = S.hsurf(); log(f'   ارتفاع مدل: {"موجود" if hs is not None else "نیست (DEM هموارشده به کار می‌رود)"}')
                except Exception as e: log(f'   ✗ خواندن نمونه: {e}')
        return
    def worker(m):
        while True:
            if t_end and time.time() > t_end: return
            try: busy = eng.tick(m)
            except SystemExit: raise
            except Exception as e: traceback.print_exc(); log(f'✗ {m}: {e}'); busy = False
            if not a.loop: return
            if not busy:
                for _ in range(30):                     # ۵ دقیقه انتظار (با بررسی سقف زمان)
                    if t_end and time.time() > t_end: return
                    time.sleep(10)
    with ThreadPoolExecutor(len(models)) as ex:
        for f in [ex.submit(worker, m) for m in models]: f.result()
    log(f'پایان: {eng.up.n} نقشه ارسال شد')

if __name__ == '__main__':
    main()
