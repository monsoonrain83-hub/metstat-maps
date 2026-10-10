#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""نمودار شاخص‌های اقلیمی (ENSO، QBO، NAO، IOD/DMI، PDO، AMO و ...) و پیش‌بینی روزانهٔ GEFS (AO/NAO/PNA/AAO) → PNG → هاست (idxput/idxend).
فقط تصویر نمودار و چند عدد خلاصه (آخرین مقدار، ماه قبل، آغاز فاز، چند نقطهٔ پیش‌بینی) روی هاست می‌رود؛ سری عددی منتشر نمی‌شود.
اجرا:  python indices.py --host https://wtafkik.ir/online --key KEY   |   --mock --out out  (آزمون بدون شبکه)
       --only nao,nao_gefs   یا   --only gefs   (فقط پیش‌بینی‌های GEFS)"""
import argparse, csv, io, json, math, os, sys, time, datetime as dt
import numpy as np
import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.patheffects as pe
from matplotlib.path import Path
from matplotlib.patches import PathPatch, FancyBboxPatch
from matplotlib.lines import Line2D
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.transforms import ScaledTranslation

plt.rcParams['font.family'] = 'DejaVu Sans'
plt.rcParams['axes.unicode_minus'] = True

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
PHASE_EN = {'ENSO': ('Warm (El Niño)', 'Cool (La Niña)')}
PERIODS = {'all': ('کل دوره', None), 'y30': ('۳۰ سال اخیر', 30), 'y10': ('۱۰ سال اخیر', 10), 'y3': ('۳ سال اخیر', 3)}
PERIOD_EN = {'all': 'Full record', 'y30': 'Last 30 years', 'y10': 'Last 10 years', 'y3': 'Last 3 years'}
MISS = {-99.99, -9.99, -999.9, -99.9, -999.0, -9999.0, -99.0, -9.9, -999.99, -99999.0, 99.99, 999.9}

# ───────────── پیش‌بینی روزانهٔ GEFS (CPC) ─────────────
# پوشهٔ «mingyue/dailyTeleconMonitorDataCwlinks» از فوریهٔ ۲۰۲۴ دیگر به‌روز نمی‌شود؛ فقط پوشهٔ زندهٔ cwlinks استفاده می‌شود.
CPC_LIVE = 'https://ftp.cpc.ncep.noaa.gov/cwlinks/'
# کلید: (شناسهٔ هاست, نام فارسی, نام انگلیسی, فایل, نام ستون, تراز)
GEFS = {
 'nao': ('nao_gefs', 'NAO — پیش‌بینی روزانهٔ GEFS', 'NAO', 'norm.daily.nao.gefs.z500.120days.csv', 'nao_index', 'Z500'),
 'ao':  ('ao_gefs',  'AO — پیش‌بینی روزانهٔ GEFS',  'AO',  'norm.daily.ao.gefs.z1000.120days.csv', 'ao_index', 'Z1000'),
 'pna': ('pna_gefs', 'PNA — پیش‌بینی روزانهٔ GEFS', 'PNA', 'norm.daily.pna.gefs.z500.120days.csv', 'pna_index', 'Z500'),
 'aao': ('aao_gefs', 'AAO — پیش‌بینی روزانهٔ GEFS', 'AAO', 'norm.daily.aao.gefs.z700.120days.csv', 'aao_index', 'Z700'),
}
GEFS_GRP = 'پیش‌بینی روزانه (GEFS)'
GEFS_PERIODS = {'d60': 60, 'd30': 30, 'd120': 120}          # ترتیب آپلود = ترتیب پیش‌فرض در منو
GEFS_PHASE_W = 0.5                                           # آستانهٔ فاز (هم‌خوان با سایت)
GEFS_STALE_WARN, GEFS_STALE_FAIL = 7, 14                     # روز

# ───────────── ابزار فارسی ─────────────
FA_DIG = str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹')
JM = ['فروردین', 'اردیبهشت', 'خرداد', 'تیر', 'مرداد', 'شهریور', 'مهر', 'آبان', 'آذر', 'دی', 'بهمن', 'اسفند']

def fa(x): return str(x).translate(FA_DIG)

def g2j(gy, gm, gd):
    gdm = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy + 1 if gm > 2 else gy
    days = 355666 + 365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400 + gd + gdm[gm - 1]
    jy = -1595 + 33 * (days // 12053); days %= 12053
    jy += 4 * (days // 1461); days %= 1461
    if days > 365: jy += (days - 1) // 365; days = (days - 1) % 365
    if days < 186: jm = 1 + days // 31; jd = 1 + days % 31
    else: jm = 7 + (days - 186) // 30; jd = 1 + (days - 186) % 30
    return jy, jm, jd

def fa_date(d): jy, jm, jd = g2j(d.year, d.month, d.day); return f'{fa(jd)} {JM[jm - 1]} {fa(jy)}'
def fa_month(d): jy, jm, _ = g2j(d.year, d.month, d.day); return f'{JM[jm - 1]} {fa(jy)}'

def log(*a): print(*a, flush=True)

def fetch(url):
    import requests
    r = requests.get(url, timeout=60, headers={'User-Agent': 'MetStat-indices/2.0'})
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

# ───────────── خلاصهٔ عددی کوچک برای جدول و تحلیل سایت ─────────────
def phase_sign(x, w):
    if not np.isfinite(x): return None
    return 1 if x > w else -1 if x < -w else 0

def phase_since(t, v, w, fmt):
    """آغاز فازِ فعلی (آخرین ران پیوستهٔ هم‌علامت). اگر از اول پنجره ادامه داشته باشد «(یا زودتر)» می‌افتد."""
    idx = [i for i, x in enumerate(v) if np.isfinite(x)]
    if not idx: return None
    cur = phase_sign(v[idx[-1]], w); j = len(idx) - 1
    while j > 0 and phase_sign(v[idx[j - 1]], w) == cur: j -= 1
    s = fmt(t[idx[j]])
    return s + ' (یا زودتر)' if j == 0 else s

def summary_monthly(iid, t, v):
    thr = IDX[iid][6]; w = abs(thr[0]) if thr else 0.0
    idx = [i for i, x in enumerate(v) if np.isfinite(x)]
    if not idx: return {}
    out = {'val': round(float(v[idx[-1]]), 2), 'since': phase_since(t, v, w, fa_month)}
    if len(idx) > 1: out['prev'] = round(float(v[idx[-2]]), 2)
    return out

# ───────────── طراحی نمودار: گرادیان، سایه، درخشش ─────────────
BG0, BG1 = '#ffffff', '#e9f0fb'
INK, MUT, GRID = '#0f172a', '#64748b', '#d3dcec'
POS = ('#ffc9bb', '#e11d48')      # روشن → پررنگ (فاز مثبت)
NEG = ('#b9dcff', '#1d4ed8')      # روشن → پررنگ (فاز منفی)
VIO = '#5b21b6'

def frame(title, subtitle, foot, size=(12, 6.4)):
    W, H = size
    fig = plt.figure(figsize=size, dpi=100)
    bg = fig.add_axes([0, 0, 1, 1], zorder=-5); bg.axis('off')
    bg.imshow(np.linspace(0, 1, 64)[:, None], extent=[0, 1, 0, 1], cmap=LinearSegmentedColormap.from_list('bg', [BG0, BG1]), aspect='auto', origin='upper')
    bg.set_xlim(0, 1); bg.set_ylim(0, 1)
    ma = W / H
    bg.add_patch(FancyBboxPatch((0.016, 0.012), 0.972, 0.968, boxstyle='round,pad=0,rounding_size=0.018', mutation_aspect=ma, fc='#0f172a', ec='none', alpha=.08))
    bg.add_patch(FancyBboxPatch((0.012, 0.018), 0.972, 0.968, boxstyle='round,pad=0,rounding_size=0.018', mutation_aspect=ma, fc='white', ec='#d9e2f2', lw=1))
    fig.text(0.045, 0.935, title, fontsize=20, weight='bold', color=INK, va='top')
    fig.text(0.045, 0.872, subtitle, fontsize=11.5, color=MUT, va='top')
    fig.text(0.955, 0.925, ' MetStat ', fontsize=11, weight='bold', color='white', ha='right', va='top', bbox=dict(boxstyle='round,pad=0.35,rounding_size=0.6', fc=INK, ec='none'))
    fig.text(0.045, 0.045, foot, fontsize=8.6, color=MUT, va='center')
    ax = fig.add_axes([0.075, 0.125, 0.895, 0.645])
    ax.set_facecolor('none')
    for s in ('top', 'right', 'left'): ax.spines[s].set_visible(False)
    ax.spines['bottom'].set_color('#9fb0cc'); ax.spines['bottom'].set_linewidth(1.1)
    ax.tick_params(colors=MUT, labelsize=10.5, length=0)
    ax.grid(axis='y', color=GRID, lw=.9, ls=(0, (4, 4)), alpha=.9); ax.set_axisbelow(True)
    return fig, ax

def date_axis(ax):
    loc = mdates.AutoDateLocator(minticks=4, maxticks=9)
    ax.xaxis.set_major_locator(loc); ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(loc))

def grad_bars(ax, x, v, w, z=3):
    """میله‌های گرادیانی با سایهٔ نرم؛ هرچه میله بلندتر، رنگ پررنگ‌تر."""
    x = np.asarray(x, float); v = np.asarray(v, float); ok = np.isfinite(v)
    for sign, cols in ((1, POS), (-1, NEG)):
        m = ok & (v * sign > 0)
        if not m.any(): continue
        verts, codes = [], []
        for xi, vi in zip(x[m], v[m]):
            verts += [(xi - w / 2, 0), (xi + w / 2, 0), (xi + w / 2, vi), (xi - w / 2, vi), (xi - w / 2, 0)]
            codes += [Path.MOVETO, Path.LINETO, Path.LINETO, Path.LINETO, Path.CLOSEPOLY]
        path = Path(verts, codes)
        sh = PathPatch(path, fc=INK, ec='none', alpha=.17, zorder=z - 1)
        sh.set_transform(ax.transData + ScaledTranslation(1.8 / 72, -1.8 / 72, ax.figure.dpi_scale_trans)); ax.add_patch(sh)
        clip = PathPatch(path, fc='none', ec='white', lw=.35, alpha=.9, zorder=z + 1); ax.add_patch(clip)
        top = float(np.max(np.abs(v[m])))
        im = ax.imshow(np.linspace(0, 1, 256)[:, None], extent=[x.min() - w, x.max() + w, 0 if sign > 0 else -top, top if sign > 0 else 0],
                       origin='lower' if sign > 0 else 'upper', cmap=LinearSegmentedColormap.from_list('b', cols), aspect='auto', zorder=z, interpolation='bilinear')
        im.set_clip_path(clip)

def glow_line(ax, x, y, color, lw=2.2, z=6):
    for a, k in ((.06, 3.4), (.11, 2.2), (.2, 1.5)):
        ax.plot(x, y, color=color, lw=lw * k, alpha=a, solid_capstyle='round', zorder=z - 1)
    return ax.plot(x, y, color=color, lw=lw, solid_capstyle='round', zorder=z)[0]

def badge(ax, xy, text, color, dx=-12, dy=26, ha='right'):
    ax.annotate(text, xy, xytext=(dx, dy), textcoords='offset points', ha=ha, va='bottom', fontsize=11, weight='bold', color='white', zorder=12,
                bbox=dict(boxstyle='round,pad=0.4,rounding_size=0.7', fc=color, ec='white', lw=1.2),
                arrowprops=dict(arrowstyle='-', color=color, lw=1.3, shrinkA=0, shrinkB=2))

def bands(ax, thr, lo, hi):
    ts = sorted({abs(x) for x in thr if x})
    for th in ts:
        ax.axhspan(th, hi, color=POS[1], alpha=.045, lw=0, zorder=1); ax.axhspan(lo, -th, color=NEG[1], alpha=.045, lw=0, zorder=1)
        ax.axhline(th, color='#94a3b8', lw=.9, ls=(0, (5, 4)), zorder=2); ax.axhline(-th, color='#94a3b8', lw=.9, ls=(0, (5, 4)), zorder=2)
    ax.axhline(0, color=INK, lw=1.1, zorder=4)

def sym_limits(vals, floor):
    m = float(np.nanmax(np.abs(vals))) if np.size(vals) and np.isfinite(vals).any() else floor
    m = max(floor, math.ceil(m * 1.18 * 2) / 2)
    return -m, m

def legend_pills(ax, handles):
    lg = ax.legend(handles=handles, loc='lower left', bbox_to_anchor=(0, 1.015), ncol=len(handles), frameon=True, fancybox=True, framealpha=.92, edgecolor='#d9e2f2', fontsize=9.5, borderpad=.6, columnspacing=1.4, handlelength=1.6)
    lg.set_zorder(20)

def save_png(fig):
    b = io.BytesIO(); fig.savefig(b, format='png'); plt.close(fig); return b.getvalue()

# ───────────── نمودار شاخص‌های ماهانه ─────────────
def render(iid, t, v, per, meta):
    fa_, en, grp, unit, kind, _, thr = IDX[iid]
    n = PERIODS[per][1]
    if n: k = max(1, len(t) - 12 * n); t, v = t[k:], v[k:]
    pos_l, neg_l = PHASE_EN.get(grp, ('Positive', 'Negative'))
    sub = f'{unit + " · " if unit else ""}Monthly values · {PERIOD_EN[per]} · last data {meta["last"]}'
    foot = f'MetStat · source: {meta["src"]} · updated {meta["upd"]}'
    fig, ax = frame(en, sub, foot)
    x = mdates.date2num(t); v = np.asarray(v, float)
    lo, hi = sym_limits(v, max([abs(a) for a in thr] + [1.0]) * 1.2 if kind == 'bar' else 1.0)
    if kind == 'bar':
        bands(ax, thr, lo, hi)
        grad_bars(ax, x, v, 27 if len(t) < 700 else 31 * (700 / len(t)) ** .3)
    else:
        lo, hi = float(np.nanmin(v)) * 1.12, float(np.nanmax(v)) * 1.12
        ax.axhline(0, color=INK, lw=1.1, zorder=4)
        ax.fill_between(x, 0, v, where=v >= 0, color=POS[1], alpha=.16, lw=0, zorder=2); ax.fill_between(x, 0, v, where=v < 0, color=NEG[1], alpha=.16, lw=0, zorder=2)
        glow_line(ax, x, v, '#334155', 1.8)
    if len(v) >= 14:
        sm = np.full(len(v), np.nan)
        for i in range(6, len(v) - 5):
            w = v[i - 6:i + 6]; w = w[np.isfinite(w)]
            if len(w) >= 9: sm[i] = w.mean()
        if kind == 'bar': glow_line(ax, x, sm, INK, 1.9, z=7)
    last = np.where(np.isfinite(v))[0]
    if len(last):
        i = last[-1]; c = POS[1] if v[i] >= 0 else NEG[1]
        badge(ax, (x[i], v[i]), f'{v[i]:+.2f}  ·  {t[i]:%Y-%m}', c, dy=26 if v[i] >= 0 else -34)
    ax.set_xlim(x[0] - 40, x[-1] + 40); ax.set_ylim(lo, hi); date_axis(ax)
    if unit: ax.set_ylabel(unit, color=MUT, fontsize=10.5)
    if kind == 'bar':
        hs = [Line2D([0], [0], color=POS[1], lw=7, solid_capstyle='butt'), Line2D([0], [0], color=NEG[1], lw=7, solid_capstyle='butt')]
        labs = [pos_l, neg_l]
        if len(v) >= 14: hs.append(Line2D([0], [0], color=INK, lw=2)); labs.append('12-month mean')
        ax.legend(hs, labs, loc='lower left', bbox_to_anchor=(0, 1.015), ncol=len(hs), frameon=True, fancybox=True, framealpha=.92, edgecolor='#d9e2f2', fontsize=9.5, borderpad=.6, columnspacing=1.4, handlelength=1.6).set_zorder(20)
    return save_png(fig)

# ───────────── GEFS: خواندن CSV و ساخت خلاصه ─────────────
def parse_gefs(text, col):
    """CSV سازمان CPC با ستون‌های lead,member,time,<col>,valid_time → فهرست (lead, member, valid, init, value).
    تاریخ اعتبار از ستون valid_time گرفته می‌شود و init = valid − lead (ساختار فایل با داده‌ی واقعی بررسی و در لاگ چاپ می‌شود)."""
    head = None; rows = []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln or ln.startswith('---'): continue
        p = ln.split(',')
        if head is None:
            low = [s.strip().lower() for s in p]
            if 'member' in low and 'lead' in low and col in low: head = {k: i for i, k in enumerate(low)}
            continue
        try:
            lead = int(float(p[head['lead']])); mem = int(float(p[head['member']])); val = float(p[head[col]])
            tt = dt.date.fromisoformat(p[head['time']].strip()[:10])
            vt = dt.date.fromisoformat(p[head['valid_time']].strip()[:10]) if 'valid_time' in head else tt + dt.timedelta(days=lead)
        except (ValueError, IndexError, KeyError): continue                 # سطر ناقص/عنوان
        if not math.isfinite(val) or abs(val) > 50: continue
        rows.append((lead, mem, vt, vt - dt.timedelta(days=lead), val))
    if head is None: raise ValueError('ستون‌های lead/member/time/%s در فایل پیدا نشد' % col)
    return rows

def gefs_struct(rows, today=None):
    """خروجی: obs (تحلیل: میانگین اعضا در lead=0 به‌تفکیک روز)، init (آخرین ران با طول کامل)، fc (اعضا × lead) و آمار ساختار."""
    if not rows: raise ValueError('فایل خالی است')
    today = today or dt.datetime.now(dt.timezone.utc).date()
    leads = sorted({r[0] for r in rows}); maxlead = leads[-1]
    ob = {}
    for lead, mem, vt, init, v in rows:
        if lead == 0: ob.setdefault(vt, []).append(v)
    if not ob: raise ValueError('lead=0 در فایل نیست')
    obs_t = sorted(ob); obs_v = np.array([np.mean(ob[d]) for d in obs_t])
    G = dict(obs_t=obs_t, obs_v=obs_v, obs_end=obs_t[-1], init=None, lead=None, fc=None, members=0, leads=(leads[0], maxlead))
    if maxlead >= 1:
        inits = [r[3] for r in rows if r[0] == maxlead]
        init = max(inits)
        sel = [r for r in rows if r[3] == init]
        mems = sorted({r[1] for r in sel}); ls = sorted({r[0] for r in sel})
        F = np.full((len(mems), len(ls)), np.nan)
        mi = {m: i for i, m in enumerate(mems)}; li = {l: i for i, l in enumerate(ls)}
        for lead, mem, vt, i0, v in sel: F[mi[mem], li[lead]] = v
        G.update(init=init, lead=np.array(ls), fc=F, members=len(mems))
    ref = G['init'] or G['obs_end']
    G['age'] = (today - ref).days
    return G

def gefs_summary(G):
    """چند عدد خلاصه برای جدول و تحلیل سایت (نه سری کامل)."""
    obs_t, obs_v = G['obs_t'], G['obs_v']
    out = {'val': round(float(obs_v[-1]), 2), 'since': phase_since(obs_t, obs_v, GEFS_PHASE_W, fa_date)}
    pv = [v for d, v in zip(obs_t, obs_v) if (obs_t[-1] - d).days >= 30]
    if pv: out['prev'] = round(float(pv[-1]), 2)
    if G['fc'] is not None:
        F, L = G['fc'], G['lead']; fc = []
        for want in (1, 3, 5, 7, 10, 14, 16):
            if want > L[-1]: continue
            j = int(np.argmin(np.abs(L - want))); c = F[:, j]; c = c[np.isfinite(c)]
            if not len(c): continue
            fc.append({'l': f'{fa(int(L[j]))} روز بعد', 'v': round(float(c.mean()), 2), 'lo': round(float(np.percentile(c, 10)), 2), 'hi': round(float(np.percentile(c, 90)), 2),
                       'pp': int(round(100 * float((c > GEFS_PHASE_W).mean()))), 'pn': int(round(100 * float((c < -GEFS_PHASE_W).mean())))})
        if fc: out['fc'] = fc
    return out

def render_gefs(key, G, days, meta):
    iid, fa_, en, _, col, lev = GEFS[key]
    obs_t, obs_v = G['obs_t'], G['obs_v']; ref = G['init'] or G['obs_end']
    t0 = ref - dt.timedelta(days=days)
    sel = [i for i, d in enumerate(obs_t) if d >= t0]
    ox = mdates.date2num([obs_t[i] for i in sel]); ov = obs_v[sel]
    F = G['fc']
    sub = (f'Daily index (normalized) · {lev} · GEFS {G["members"]}-member ensemble · run {G["init"]:%Y-%m-%d}' if F is not None
           else f'Daily index (normalized) · {lev} · GEFS analysis · to {G["obs_end"]:%Y-%m-%d}')
    foot = f'MetStat · source: NOAA CPC / GEFS · last {days} days + forecast · updated {meta["upd"]}'
    fig, ax = frame(f'{en} · GEFS ensemble forecast', sub, foot)
    allv = [ov]
    if F is not None: allv.append(F.ravel())
    lo, hi = sym_limits(np.concatenate(allv), 2.5)
    bands(ax, (1.0,), lo, hi)
    grad_bars(ax, ox, ov, 0.78)
    hs = [Line2D([0], [0], color=POS[1], lw=7, solid_capstyle='butt'), Line2D([0], [0], color=NEG[1], lw=7, solid_capstyle='butt')]; labs = ['Analysis (+)', 'Analysis (−)']
    xmax = ox[-1] + 1
    if F is not None:
        fx = mdates.date2num([G['init'] + dt.timedelta(days=int(l)) for l in G['lead']])
        ax.axvspan(fx[0], fx[-1] + .6, color=VIO, alpha=.055, lw=0, zorder=1)
        ax.axvline(fx[0], color=VIO, lw=1.1, ls=(0, (3, 3)), alpha=.8, zorder=5)
        for r in F: ax.plot(fx, r, color='#6366f1', lw=.8, alpha=.22, zorder=4)
        with np.errstate(all='ignore'):
            p10, p90, mn, mx, mean = (np.nanpercentile(F, 10, 0), np.nanpercentile(F, 90, 0), np.nanmin(F, 0), np.nanmax(F, 0), np.nanmean(F, 0))
        ax.fill_between(fx, mn, mx, color=VIO, alpha=.07, lw=0, zorder=3)
        ax.fill_between(fx, p10, p90, color='#8b5cf6', alpha=.22, lw=0, zorder=4)
        glow_line(ax, fx, mean, VIO, 2.4, z=8)
        ax.plot(fx[::2], mean[::2], 'o', ms=5, mfc='white', mec=VIO, mew=1.7, zorder=9)
        c = POS[1] if mean[-1] >= 0 else NEG[1]
        badge(ax, (fx[-1], mean[-1]), f'{mean[-1]:+.2f}', c, dx=-8, dy=24 if mean[-1] >= 0 else -34)
        ax.text(fx[0] + .4, hi - (hi - lo) * .035, 'FORECAST', color=VIO, fontsize=9.5, weight='bold', va='top', alpha=.9, zorder=6)
        xmax = fx[-1] + 1.5
        hs += [Line2D([0], [0], color='#6366f1', lw=1.2, alpha=.5), Line2D([0], [0], color='#8b5cf6', lw=7, alpha=.35, solid_capstyle='butt'), Line2D([0], [0], color=VIO, lw=2.4)]
        labs += ['Members', 'P10–P90', 'Ensemble mean']
    ax.set_xlim(ox[0] - 1, xmax); ax.set_ylim(lo, hi); date_axis(ax)
    ax.set_ylabel('Normalized index (σ)', color=MUT, fontsize=10.5)
    ax.legend(hs, labs, loc='lower left', bbox_to_anchor=(0, 1.015), ncol=len(hs), frameon=True, fancybox=True, framealpha=.92, edgecolor='#d9e2f2', fontsize=9.2, borderpad=.55, columnspacing=1.2, handlelength=1.6).set_zorder(20)
    return save_png(fig)

# ───────────── آزمون بدون شبکه ─────────────
def mock_series(iid):
    rng = np.random.default_rng(abs(hash(iid)) % 1000); n = 12 * 75
    t = [dt.date(1950 + i // 12, i % 12 + 1, 15) for i in range(n)]
    v = np.convolve(rng.normal(0, 1, n + 8), np.ones(9) / 4, 'valid')[:n]; return t, v

def mock_gefs_csv(key, today=None, nm=31, maxlead=16, ndays=120):
    """CSV ساختگی هم‌شکل فایل CPC: lead,member,time,<col>,valid_time (time = تاریخ ران، valid_time = time + lead)."""
    col = GEFS[key][4]; rng = np.random.default_rng(abs(hash(key)) % 977)
    today = today or dt.datetime.now(dt.timezone.utc).date(); init = today - dt.timedelta(days=2)
    days = [init - dt.timedelta(days=ndays - 1 - i) for i in range(ndays)]
    a = np.zeros(ndays + maxlead + 1)
    for i in range(1, len(a)): a[i] = .93 * a[i - 1] + rng.normal(0, .55)
    out = io.StringIO(); out.write('---\n' + f'lead,member,time,{col},valid_time\n')
    for lead in range(0, maxlead + 1):
        for m in range(nm):
            nz = rng.normal(0, .12 + .24 * math.sqrt(lead))
            for i, d in enumerate(days):
                v = a[i + lead] * (1 - .02 * lead) + (rng.normal(0, .08) if lead == 0 else nz + rng.normal(0, .1))
                out.write(f'{lead},{m},{d},{v},{d + dt.timedelta(days=lead)}\n')
    return out.getvalue()

def post(a, iid, per, png, meta, requests):
    u = f'{a.host.rstrip("/")}/nwp_upload.php?a=idxput&key={requests.utils.quote(a.key)}'
    for k in range(4):
        try:
            r = requests.post(u, data=dict(id=iid, per=per, meta=json.dumps(meta)), files={'png': (f'{iid}_{per}.png', png, 'image/png')}, timeout=90)
            if r.status_code == 403: sys.exit('✗ کلید آپلود اشتباه است')
            if r.json().get('ok'): return True
            log(f'  ✗ پاسخ هاست {iid}/{per}: {r.text[:120]}')
        except SystemExit: raise
        except Exception as e: log(f'  … {e}'); time.sleep(4 * (k + 1))
    return False

def run_monthly(a, now, requests):
    ok = fail = 0
    for iid, (fa_, en, grp, unit, kind, urls, thr) in IDX.items():
        if a.only and iid not in a.only: continue
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
        meta = dict(fa=fa_, en=en, grp=GRP_FA.get(grp, grp), src=src, last=f'{last:%Y-%m}', upd=now, unit=unit, kind=kind, note='')
        meta.update(summary_monthly(iid, t, v))
        for per, (pfa, _) in PERIODS.items():
            png = render(iid, t, v, per, meta)
            if a.out: os.makedirs(a.out, exist_ok=True); open(os.path.join(a.out, f'{iid}_{per}.png'), 'wb').write(png)
            if a.host: post(a, iid, per, png, dict(meta, perfa=pfa), requests)
        log(f'✓ {iid} ({len(t)} ماه، تا {last:%Y-%m}، منبع {src})'); ok += 1
    return ok, fail

def run_gefs(a, now, requests):
    ok = fail = 0
    for key, (iid, fa_, en, fname, col, lev) in GEFS.items():
        if a.only and iid not in a.only and key not in a.only: continue
        try:
            text = mock_gefs_csv(key) if a.mock else fetch(CPC_LIVE + fname)
            src = 'mock' if a.mock else 'NOAA CPC · GEFS'
            G = gefs_struct(parse_gefs(text, col))
        except Exception as e: log(f'✗ {iid}: {e}'); fail += 1; continue
        log(f'  {iid}: lead {G["leads"][0]}..{G["leads"][1]}، اعضا {G["members"]}، آخرین تحلیل {G["obs_end"]}، ران {G["init"]}، سن {G["age"]} روز')
        if G['age'] > GEFS_STALE_FAIL: log(f'✗ {iid}: داده {G["age"]} روز کهنه است (منبع CPC به‌روز نشده)؛ آپلود نشد'); fail += 1; continue
        if G['age'] > GEFS_STALE_WARN: log(f'  ؟ {iid}: داده {G["age"]} روز قدیمی است')
        if G['fc'] is None: log(f'  ؟ {iid}: فایل فقط lead=0 دارد؛ فقط تحلیل رسم می‌شود')
        ref = G['init'] or G['obs_end']
        meta = dict(fa=fa_, en=f'{en} · GEFS ensemble forecast', grp=GEFS_GRP, src=src, last=f'{ref:%Y-%m-%d}', upd=now, unit='', kind='plume',
                    note=f'GEFS {lev}، {G["members"]} عضو')
        meta.update(gefs_summary(G))
        for per, days in GEFS_PERIODS.items():
            pfa = f'{fa(days)} روز اخیر + پیش‌بینی'
            png = render_gefs(key, G, days, meta)
            if a.out: os.makedirs(a.out, exist_ok=True); open(os.path.join(a.out, f'{iid}_{per}.png'), 'wb').write(png)
            if a.host: post(a, iid, per, png, dict(meta, perfa=pfa), requests)
        log(f'✓ {iid} (ران {ref}، {G["members"]} عضو)'); ok += 1
    return ok, fail

def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--host'); ap.add_argument('--key'); ap.add_argument('--out'); ap.add_argument('--mock', action='store_true'); ap.add_argument('--only')
    a = ap.parse_args(); import requests
    a.only = set(a.only.split(',')) if a.only else None
    now = dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d')
    only_gefs = bool(a.only) and ('gefs' in a.only)
    if only_gefs: a.only = None
    ok = fail = 0
    if not only_gefs:
        o, f = run_monthly(a, now, requests); ok += o; fail += f
    if only_gefs or not a.only or any(k in a.only or GEFS[k][0] in a.only for k in GEFS):
        o, f = run_gefs(a, now, requests); ok += o; fail += f
    if a.host:
        requests.post(f'{a.host.rstrip("/")}/nwp_upload.php?a=idxend&key={requests.utils.quote(a.key)}', timeout=60)
    log(f'پایان: {ok} شاخص موفق، {fail} ناموفق'); sys.exit(0 if ok else 1)

if __name__ == '__main__': main()
