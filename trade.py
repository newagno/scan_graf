# -*- coding: utf-8 -*-
# crypto_scanner_v12.py
# SMC Full Toolkit | Multi-exchange
# Fixes: swing overflow, premium/discount clamp, EQ threshold per TF, pivot age limit

import ccxt
import pandas as pd
import numpy as np
from datetime import datetime, timezone
import pandas_ta as ta
import subprocess
import sys
import threading

# ──────────────────────────────────────────────────────────────────────────────
# EXCHANGE CONFIG
# ──────────────────────────────────────────────────────────────────────────────
EXCHANGES = {
    '1': ('binance',     ccxt.binance,     {'options': {'defaultType': 'future'}}),
    '2': ('binanceusdm', ccxt.binanceusdm, {}),
    '3': ('okx',         ccxt.okx,         {'options': {'defaultType': 'swap'}}),
    '4': ('bybit',       ccxt.bybit,       {'options': {'defaultType': 'linear'}}),
    '5': ('bitget',      ccxt.bitget,      {'options': {'defaultType': 'swap'}}),
    '6': ('whitebit',    ccxt.whitebit,    {}),
}

print("=" * 65)
print("  Crypto Scanner v12 | SMC Full Toolkit | Multi-Exchange")
print("=" * 65)
print("\nSelect exchange:")
for k, (name, _, _) in EXCHANGES.items():
    print(f"  {k}. {name}")

ex_input = input("\nEnter number (default 2 = binanceusdm): ").strip()
if ex_input not in EXCHANGES:
    ex_input = '2'

ex_name, ex_class, ex_opts = EXCHANGES[ex_input]
exchange = ex_class({'enableRateLimit': True, **ex_opts})
print(f"\nExchange: {ex_name}\n")

# ──────────────────────────────────────────────────────────────────────────────
# PAIR
# ──────────────────────────────────────────────────────────────────────────────
default_symbol = 'BTC/USDT:USDT'
symbol_input = input(f"Enter pair (e.g. ETH or SOL) or Enter for {default_symbol}: ").strip().upper()
if not symbol_input:
    symbol = default_symbol
elif '/' not in symbol_input:
    symbol = f"{symbol_input}/USDT" if ex_name == 'whitebit' else f"{symbol_input}/USDT:USDT"
else:
    symbol = symbol_input
print(f"Pair: {symbol}\n")

# ──────────────────────────────────────────────────────────────────────────────
# TIMEFRAMES
# ──────────────────────────────────────────────────────────────────────────────
AVAILABLE_TFS = ['1m','3m','5m','15m','30m','1h','4h','1d','3d','1w','1M']
print(f"Available TFs: {', '.join(AVAILABLE_TFS)}")
tf_input   = input("Enter TFs comma-separated or Enter for 15m,1h,4h: ").strip()
timeframes = [tf.strip() for tf in tf_input.split(',')] if tf_input else ['15m','1h','4h']
timeframes = [tf for tf in timeframes if tf in AVAILABLE_TFS] or ['15m','1h','4h']

limit_candles = 400
MIN_CANDLES   = 30
OB_IMPULSE_MULT = 1.5

# ── Per-TF config ────────────────────────────────────────────────────────────
# pivot_n    : candles window for pivot search
# confirm    : candles each side to confirm pivot
# smc_lb     : SMC lookback (FVG, BOS, OB)
# eq_lb      : Equal H/L lookback
# eq_thresh  : % tolerance for Equal Highs/Lows (FIX #3: larger for higher TFs)
# fvg_gap    : minimum FVG gap size in %
# pivot_age  : max candles ago a pivot can be to stay valid (FIX #4)
TF_CONFIG = {
    '1m':  {'pivot_n':30,  'confirm':1, 'smc_lb':50,  'eq_lb':30,  'eq_thresh':0.03, 'fvg_gap':0.03, 'pivot_age':25},
    '3m':  {'pivot_n':40,  'confirm':1, 'smc_lb':60,  'eq_lb':40,  'eq_thresh':0.03, 'fvg_gap':0.04, 'pivot_age':30},
    '5m':  {'pivot_n':50,  'confirm':2, 'smc_lb':80,  'eq_lb':50,  'eq_thresh':0.04, 'fvg_gap':0.05, 'pivot_age':40},
    '15m': {'pivot_n':60,  'confirm':2, 'smc_lb':100, 'eq_lb':60,  'eq_thresh':0.05, 'fvg_gap':0.08, 'pivot_age':None},
    '30m': {'pivot_n':70,  'confirm':2, 'smc_lb':100, 'eq_lb':60,  'eq_thresh':0.05, 'fvg_gap':0.10, 'pivot_age':None},
    '1h':  {'pivot_n':80,  'confirm':2, 'smc_lb':120, 'eq_lb':80,  'eq_thresh':0.07, 'fvg_gap':0.15, 'pivot_age':None},
    '4h':  {'pivot_n':100, 'confirm':1, 'smc_lb':150, 'eq_lb':100, 'eq_thresh':0.10, 'fvg_gap':0.20, 'pivot_age':None},
    '1d':  {'pivot_n':120, 'confirm':1, 'smc_lb':200, 'eq_lb':120, 'eq_thresh':0.20, 'fvg_gap':0.30, 'pivot_age':None},
    '3d':  {'pivot_n':60,  'confirm':1, 'smc_lb':100, 'eq_lb':60,  'eq_thresh':0.40, 'fvg_gap':0.50, 'pivot_age':None},
    '1w':  {'pivot_n':52,  'confirm':1, 'smc_lb':80,  'eq_lb':52,  'eq_thresh':0.60, 'fvg_gap':1.00, 'pivot_age':None},
    '1M':  {'pivot_n':24,  'confirm':1, 'smc_lb':36,  'eq_lb':24,  'eq_thresh':1.00, 'fvg_gap':2.00, 'pivot_age':None},
}

TF_SECONDS = {
    '1m':60,'3m':180,'5m':300,'15m':900,'30m':1800,
    '1h':3600,'4h':14400,'1d':86400,'3d':259200,'1w':604800,'1M':2592000
}

def cfg(tf):
    return TF_CONFIG.get(tf, TF_CONFIG['15m'])

# ──────────────────────────────────────────────────────────────────────────────
# VECTORIZED PIVOT DETECTION
# ──────────────────────────────────────────────────────────────────────────────
def find_pivots_vectorized(series: pd.Series, confirm: int, mode: str) -> pd.Series:
    window = confirm * 2 + 1
    ref = series.rolling(window=window, center=True).max() if mode == 'high' \
          else series.rolling(window=window, center=True).min()
    return series == ref

def market_structure(df: pd.DataFrame, tf: str) -> dict:
    c      = cfg(tf)
    n      = min(c['pivot_n'], len(df))
    conf   = c['confirm']
    max_age = c['pivot_age']  # FIX #4: age limit for small TFs
    close  = df.iloc[-1]['close']
    total  = len(df)

    recent = df.tail(n).copy().reset_index(drop=True)
    recent['is_ph'] = find_pivots_vectorized(recent['high'], conf, 'high')
    recent['is_pl'] = find_pivots_vectorized(recent['low'],  conf, 'low')

    # FIX #4: filter pivots by age (index distance from last candle)
    last_idx = len(recent) - 1
    if max_age is not None:
        ph_rows_chron = [(recent.at[i,'high'], last_idx - i)
                         for i in recent[recent['is_ph']].index if (last_idx - i) <= max_age]
        pl_rows_chron = [(recent.at[i,'low'],  last_idx - i)
                         for i in recent[recent['is_pl']].index if (last_idx - i) <= max_age]
    else:
        ph_rows_chron = [(recent.at[i,'high'], last_idx - i)
                         for i in recent[recent['is_ph']].index]
        pl_rows_chron = [(recent.at[i,'low'],  last_idx - i)
                         for i in recent[recent['is_pl']].index]

    ph_vals = [v for v, _ in ph_rows_chron]
    pl_vals = [v for v, _ in pl_rows_chron]

    # HH/LL in chronological order
    hh = ("HH" if ph_vals[-1] > ph_vals[-2] else "LH") if len(ph_vals) >= 2 else "N/A"
    ll = ("HL" if pl_vals[-1] > pl_vals[-2] else "LL") if len(pl_vals) >= 2 else "N/A"
    trend = "BULLISH" if hh=="HH" and ll=="HL" else "BEARISH" if hh=="LH" and ll=="LL" else "MIXED"

    all_h = sorted(set(ph_vals), reverse=True)
    all_l = sorted(set(pl_vals))

    # FIX #1: validated swings must satisfy high > close, low < close
    # If no valid candidates found, fallback to recent absolute high/low
    valid_h = [v for v in all_h if v > close]
    valid_l = [v for v in all_l if v < close]

    if not valid_h:
        # Fallback: use absolute high of the recent window
        fallback_h = float(recent['high'].max())
        valid_h = [fallback_h] if fallback_h > close else []

    if not valid_l:
        # Fallback: use absolute low of the recent window
        fallback_l = float(recent['low'].min())
        valid_l = [fallback_l] if fallback_l < close else []

    swing_high = valid_h[0] if valid_h else None
    swing_low  = valid_l[0] if valid_l else None
    prev_sh    = valid_h[1] if len(valid_h) >= 2 else (all_h[1] if len(all_h) >= 2 else None)
    prev_sl    = valid_l[1] if len(valid_l) >= 2 else (all_l[1] if len(all_l) >= 2 else None)

    return {
        "structure":  f"{hh}/{ll}",
        "trend":      trend,
        "swing_high": swing_high,
        "swing_low":  swing_low,
        "prev_sh":    prev_sh,
        "prev_sl":    prev_sl,
        "all_highs":  all_h[:5],
        "all_lows":   all_l[:5],
        "ph_chron":   ph_vals,
        "pl_chron":   pl_vals,
    }

# ──────────────────────────────────────────────────────────────────────────────
# FVG
# ──────────────────────────────────────────────────────────────────────────────
def calc_fvg(df: pd.DataFrame, tf: str) -> dict:
    c        = cfg(tf)
    lookback = min(c['smc_lb'], len(df))
    min_gap  = c['fvg_gap'] / 100.0
    recent   = df.tail(lookback).reset_index(drop=True)
    close    = df.iloc[-1]['close']
    bull_fvg, bear_fvg = [], []

    for i in range(2, len(recent)):
        h_i2 = recent.at[i-2, 'high']
        l_i2 = recent.at[i-2, 'low']
        l_i  = recent.at[i,   'low']
        h_i  = recent.at[i,   'high']

        if l_i > h_i2:
            gap = (l_i - h_i2) / h_i2
            if gap >= min_gap and close > h_i2 and not (h_i2 <= close <= l_i):
                bull_fvg.append((round(h_i2,4), round(l_i,4)))

        if h_i < l_i2:
            gap = (l_i2 - h_i) / l_i2
            if gap >= min_gap and close < l_i2 and not (h_i <= close <= l_i2):
                bear_fvg.append((round(h_i,4), round(l_i2,4)))

    bull_fvg_s = sorted(bull_fvg, key=lambda x: abs(close - x[0]))[:3]
    bear_fvg_s = sorted(bear_fvg, key=lambda x: abs(close - x[1]))[:3]
    return {'bullish': bull_fvg_s, 'bearish': bear_fvg_s}

# ──────────────────────────────────────────────────────────────────────────────
# BOS / CHoCH with age tags
# ──────────────────────────────────────────────────────────────────────────────
def calc_bos_choch(df: pd.DataFrame, ms: dict, tf: str) -> dict:
    ph = ms['ph_chron']
    pl = ms['pl_chron']
    if len(ph) < 2 or len(pl) < 2:
        return {'bos': [], 'choch': [], 'last_bos': None, 'last_tag': None}

    lookback = min(cfg(tf)['smc_lb'], len(df))
    recent   = df.tail(lookback).reset_index(drop=True)
    total    = len(recent)
    trend    = ms['trend']
    prev_sh  = ph[-2]
    prev_sl  = pl[-2]
    events   = []

    for i in range(1, total):
        c = recent.at[i,   'close']
        p = recent.at[i-1, 'close']
        age = total - i

        if p <= prev_sh < c:
            kind = 'CHoCH' if trend == 'BEARISH' else 'BOS'
            events.append({'type': kind, 'dir': 'BULLISH', 'level': round(prev_sh,4), 'age': age})

        if p >= prev_sl > c:
            kind = 'CHoCH' if trend == 'BULLISH' else 'BOS'
            events.append({'type': kind, 'dir': 'BEARISH', 'level': round(prev_sl,4), 'age': age})

    def tag(e):
        t = "FRESH" if e['age'] <= 5 else ("RECENT" if e['age'] <= 20 else "STALE")
        return f"{e['dir']} {e['type']} @ {e['level']} ({e['age']} candles ago — {t})"

    bos_ev   = [e for e in events if e['type'] == 'BOS']
    choch_ev = [e for e in events if e['type'] == 'CHoCH']
    return {
        'bos':      [tag(e) for e in bos_ev[-2:]],
        'choch':    [tag(e) for e in choch_ev[-2:]],
        'last_bos': bos_ev[-1] if bos_ev else None,
        'last_tag': tag(bos_ev[-1]) if bos_ev else None,
    }

# ──────────────────────────────────────────────────────────────────────────────
# ORDER BLOCKS
# ──────────────────────────────────────────────────────────────────────────────
def calc_order_blocks(df: pd.DataFrame, ms: dict, tf: str) -> dict:
    lookback = min(cfg(tf)['smc_lb'], len(df))
    recent   = df.tail(lookback).reset_index(drop=True)
    avg_body = (recent['close'] - recent['open']).abs().rolling(20).mean()
    prev_sh  = ms['prev_sh']
    prev_sl  = ms['prev_sl']
    bull_obs, bear_obs = [], []

    for i in range(2, len(recent) - 1):
        body  = abs(recent.at[i,'close'] - recent.at[i,'open'])
        avg_b = avg_body.at[i] if pd.notna(avg_body.at[i]) else 0

        if (recent.at[i,'close'] > recent.at[i,'open'] and
                prev_sh and recent.at[i,'close'] > prev_sh and
                body > OB_IMPULSE_MULT * avg_b):
            for j in range(i-1, max(i-10, 0), -1):
                if recent.at[j,'close'] < recent.at[j,'open']:
                    lo = min(recent.at[j,'open'], recent.at[j,'close'])
                    hi = max(recent.at[j,'open'], recent.at[j,'close'])
                    bull_obs.append({'bot': round(lo,4), 'top': round(hi,4)})
                    break

        if (recent.at[i,'close'] < recent.at[i,'open'] and
                prev_sl and recent.at[i,'close'] < prev_sl and
                body > OB_IMPULSE_MULT * avg_b):
            for j in range(i-1, max(i-10, 0), -1):
                if recent.at[j,'close'] > recent.at[j,'open']:
                    lo = min(recent.at[j,'open'], recent.at[j,'close'])
                    hi = max(recent.at[j,'open'], recent.at[j,'close'])
                    bear_obs.append({'bot': round(lo,4), 'top': round(hi,4)})
                    break

    def dedup(lst):
        seen, out = set(), []
        for ob in lst:
            key = (ob['bot'], ob['top'])
            if key not in seen:
                seen.add(key); out.append(ob)
        return out

    return {'bullish': dedup(bull_obs)[-3:], 'bearish': dedup(bear_obs)[-3:]}

# ──────────────────────────────────────────────────────────────────────────────
# BREAKER BLOCKS
# ──────────────────────────────────────────────────────────────────────────────
def calc_breaker_blocks(df: pd.DataFrame, obs: dict) -> dict:
    close = df.iloc[-1]['close']
    return {
        'bullish': [ob for ob in obs.get('bearish',[]) if close > ob['top']],
        'bearish': [ob for ob in obs.get('bullish',[]) if close < ob['bot']],
    }

# ──────────────────────────────────────────────────────────────────────────────
# EQUAL HIGHS/LOWS — FIX #3: per-TF threshold + strict close validation
# ──────────────────────────────────────────────────────────────────────────────
def calc_equal_hl(df: pd.DataFrame, tf: str) -> dict:
    c        = cfg(tf)
    lookback = min(c['eq_lb'], len(df))
    thresh   = c['eq_thresh']   # FIX #3: TF-specific threshold
    recent   = df.tail(lookback)
    close    = df.iloc[-1]['close']
    highs    = recent['high'].values
    lows     = recent['low'].values
    eq_highs, eq_lows = [], []

    for i in range(len(highs)):
        for j in range(i+1, len(highs)):
            diff = abs(highs[i] - highs[j]) / highs[i] * 100
            if diff <= thresh:
                level = round((highs[i]+highs[j])/2, 4)
                if level > close:
                    if level not in [e['level'] for e in eq_highs]:
                        eq_highs.append({'level': level, 'diff': round(diff,3)})

    for i in range(len(lows)):
        for j in range(i+1, len(lows)):
            diff = abs(lows[i] - lows[j]) / lows[i] * 100
            if diff <= thresh:
                level = round((lows[i]+lows[j])/2, 4)
                if level < close:
                    if level not in [e['level'] for e in eq_lows]:
                        eq_lows.append({'level': level, 'diff': round(diff,3)})

    return {
        'equal_highs': sorted(eq_highs, key=lambda x: x['level'])[:3],
        'equal_lows':  sorted(eq_lows,  key=lambda x: x['level'], reverse=True)[:3],
    }

# ──────────────────────────────────────────────────────────────────────────────
# PREMIUM / DISCOUNT — FIX #2: clamp pct to 0-100, label out-of-range
# ──────────────────────────────────────────────────────────────────────────────
def calc_premium_discount(df: pd.DataFrame, ms: dict, tf: str) -> dict:
    small_tfs = {'1m','3m','5m'}
    if tf in small_tfs:
        n      = min(cfg(tf)['pivot_n'], len(df))
        recent = df.tail(n)
        sh     = float(recent['high'].max())
        sl     = float(recent['low'].min())
    else:
        sh = ms['swing_high']
        sl = ms['swing_low']

    if not sh or not sl or sh <= sl:
        return {'zone':'N/A','eq':None,'premium_min':None,'discount_max':None,'pct':None}

    close        = df.iloc[-1]['close']
    rng          = sh - sl
    eq           = round(sl + rng * 0.5,  4)
    premium_min  = round(sl + rng * 0.75, 4)
    discount_max = round(sl + rng * 0.25, 4)
    raw_pct      = (close - sl) / rng * 100

    # FIX #2: detect out-of-range and clamp
    if raw_pct > 100:
        zone = f"ABOVE RANGE ({raw_pct:.1f}%) — price broke above swing high, range stale"
        pct  = 100.0
    elif raw_pct < 0:
        zone = f"BELOW RANGE ({raw_pct:.1f}%) — price broke below swing low, range stale"
        pct  = 0.0
    else:
        pct = round(raw_pct, 1)
        if pct >= 75:
            zone = f"PREMIUM ({pct}%) — SM SELLS"
        elif pct <= 25:
            zone = f"DISCOUNT ({pct}%) — SM BUYS"
        elif pct >= 50:
            zone = f"UPPER EQ ({pct}%) — lean short"
        else:
            zone = f"LOWER EQ ({pct}%) — lean long"

    return {
        'zone': zone, 'eq': eq,
        'premium_min': premium_min, 'discount_max': discount_max,
        'pct': pct, 'sh': sh, 'sl': sl,
    }

# ──────────────────────────────────────────────────────────────────────────────
# FTA
# ──────────────────────────────────────────────────────────────────────────────
def calc_fta(df: pd.DataFrame, ms: dict, fvg: dict, obs: dict) -> dict:
    close = df.iloc[-1]['close']
    res, sup = [], []
    if ms['swing_high'] and ms['swing_high'] > close:
        res.append(('Swing High', ms['swing_high']))
    for b,t in fvg.get('bearish',[]):
        if t > close: res.append(('Bear FVG', t))
    for ob in obs.get('bearish',[]):
        if ob['top'] > close: res.append(('Bear OB', ob['top']))

    if ms['swing_low'] and ms['swing_low'] < close:
        sup.append(('Swing Low', ms['swing_low']))
    for b,t in fvg.get('bullish',[]):
        if b < close: sup.append(('Bull FVG', b))
    for ob in obs.get('bullish',[]):
        if ob['bot'] < close: sup.append(('Bull OB', ob['bot']))

    return {
        'resistance': min(res, key=lambda x: x[1]) if res else None,
        'support':    max(sup, key=lambda x: x[1]) if sup else None,
    }

# ──────────────────────────────────────────────────────────────────────────────
# DAILY VWAP with pagination
# ──────────────────────────────────────────────────────────────────────────────
def fetch_daily_vwap(exchange, symbol, page_size=500):
    try:
        now   = datetime.now(timezone.utc)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        since = int(start.timestamp() * 1000)
        end   = int(now.timestamp() * 1000)
        rows, cur = [], since

        while cur < end:
            batch = exchange.fetch_ohlcv(symbol, '1m', since=cur, limit=page_size)
            if not batch: break
            rows.extend(batch)
            last_ts = batch[-1][0]
            if last_ts >= end or len(batch) < page_size: break
            cur = last_ts + 60_000

        if not rows: return None
        d   = pd.DataFrame(rows, columns=['ts','o','h','l','c','v'])
        typ = (d['h'] + d['l'] + d['c']) / 3
        return round(float((typ * d['v']).sum() / d['v'].sum()), 4)
    except:
        return None

# ──────────────────────────────────────────────────────────────────────────────
# PARALLEL FETCH
# ──────────────────────────────────────────────────────────────────────────────
results, lock = {}, threading.Lock()

def fetch_tf(tf):
    try:
        data = exchange.fetch_ohlcv(symbol, tf, limit=limit_candles)
        with lock: results[tf] = data
    except Exception as e:
        with lock: results[tf] = e

vwap_r = [None]; gen_r = [None]; gen_err = [None]

def th_vwap():
    vwap_r[0] = fetch_daily_vwap(exchange, symbol)

def th_general():
    try:
        t = exchange.fetch_ticker(symbol)
        fr, oi = None, None
        try: fr = exchange.fetch_funding_rate(symbol)
        except: pass
        try: oi = exchange.fetch_open_interest(symbol)
        except: pass
        gen_r[0] = (t, fr, oi)
    except Exception as e: gen_err[0] = e

print("\nFetching all data in parallel...")
threads = [threading.Thread(target=th_general), threading.Thread(target=th_vwap)]
for tf in timeframes:
    threads.append(threading.Thread(target=fetch_tf, args=(tf,)))
for t in threads: t.start()
for t in threads: t.join()
print("Done.\n")

# ──────────────────────────────────────────────────────────────────────────────
# GENERAL DATA
# ──────────────────────────────────────────────────────────────────────────────
if gen_err[0]:
    print(f"ERROR: {gen_err[0]}"); input("Press Enter..."); sys.exit()

ticker, fr_data, oi_data = gen_r[0]
daily_vwap = vwap_r[0]
price      = ticker['last']

fr, fr_bias = 0.0, "N/A"
if fr_data:
    fr = fr_data['fundingRate'] * 100
    fr_bias = "BEARISH (longs->shorts)" if fr > 0.05 else \
              "BULLISH (shorts->longs)" if fr < -0.05 else "NEUTRAL"

oi_line = ""
if oi_data:
    oi_usd  = oi_data['openInterestAmount'] * price
    oi_line = f"OI: {oi_data['openInterestAmount']:.2f} {symbol.split('/')[0]} (${oi_usd:,.0f})\n"

hdr  = f"EXCHANGE: {ex_name}\n"
hdr += f"PAIR: {symbol} | PRICE: {price:.4f} USDT\n"
hdr += f"24h Volume: {ticker.get('baseVolume',0):,.2f} {symbol.split('/')[0]} (${ticker.get('quoteVolume',0):,.0f})\n"
hdr += f"Funding Rate: {fr:.4f}% -> {fr_bias}\n"
hdr += oi_line
hdr += f"Daily VWAP (UTC session): {daily_vwap if daily_vwap else 'N/A'}\n"
hdr += f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC\n\n"
print(hdr)
output_text = hdr

# ──────────────────────────────────────────────────────────────────────────────
# FORMATTERS
# ──────────────────────────────────────────────────────────────────────────────
def fmt_fvg(lst):
    return " | ".join([f"[{b:.4f}-{t:.4f}]" for b,t in lst]) if lst else "None"

def fmt_ob(lst):
    return " | ".join([f"[{o['bot']:.4f}-{o['top']:.4f}]" for o in lst]) if lst else "None"

def fmt_bos(lst):
    return " | ".join(lst) if lst else "None"

def fmt_eq(lst):
    return " | ".join([f"{e['level']:.4f} (d{e['diff']}%)" for e in lst]) if lst else "None"

# ──────────────────────────────────────────────────────────────────────────────
# PER-TF ANALYSIS
# ──────────────────────────────────────────────────────────────────────────────
for tf in timeframes:
    print(f"{'='*65}\nTF: {tf}\n{'='*65}")

    raw = results.get(tf)
    if isinstance(raw, Exception):
        err = f"Error {tf}: {raw}\n\n"; print(err); output_text += err; continue
    if not raw or len(raw) < MIN_CANDLES:
        err = f"[TF:{tf}] Not enough candles. Skipped.\n\n"
        print(err); output_text += err; continue

    df = pd.DataFrame(raw, columns=['timestamp','open','high','low','close','volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
    df['time']      = df['timestamp'].dt.strftime('%Y-%m-%d %H:%M')

    # Indicators
    df['RSI']      = ta.rsi(df['close'], length=14)
    df['ATR']      = ta.atr(df['high'], df['low'], df['close'], length=14)
    df['EMA9']     = ta.ema(df['close'], length=9)
    df['EMA21']    = ta.ema(df['close'], length=21)
    df['EMA50']    = ta.ema(df['close'], length=50)
    df['EMA200']   = ta.ema(df['close'], length=200)
    df['VolumeMA'] = ta.sma(df['volume'], length=20)
    df['ROC5']     = df['close'].pct_change(5) * 100

    macd_df         = ta.macd(df['close'], fast=12, slow=26, signal=9)
    df['MACD']      = macd_df['MACD_12_26_9']
    df['MACD_Sig']  = macd_df['MACDs_12_26_9']
    df['MACD_Hist'] = macd_df['MACDh_12_26_9']

    stoch_df      = ta.stoch(df['high'], df['low'], df['close'], k=14, d=3, smooth_k=3)
    df['Stoch_K'] = stoch_df['STOCHk_14_3_3']
    df['Stoch_D'] = stoch_df['STOCHd_14_3_3']

    bb      = ta.bbands(df['close'], length=20, std=2)
    bb_cols = list(bb.columns)
    col_u   = next((c for c in bb_cols if c.startswith('BBU')), None)
    col_m   = next((c for c in bb_cols if c.startswith('BBM')), None)
    col_l   = next((c for c in bb_cols if c.startswith('BBL')), None)
    bb_ok   = bool(col_u and col_m and col_l)
    if bb_ok:
        df['BB_u'] = bb[col_u]; df['BB_m'] = bb[col_m]; df['BB_l'] = bb[col_l]

    last = df.iloc[-1]
    atr  = last['ATR'] if pd.notna(last['ATR']) else 0.0

    ema_trend = "BULLISH" if (pd.notna(last['EMA9']) and pd.notna(last['EMA21']) and last['EMA9'] > last['EMA21']) else "BEARISH"
    e50_pos   = f"above EMA50 by {((last['close']-last['EMA50'])/last['EMA50']*100):.2f}%" if pd.notna(last['EMA50']) and last['EMA50']>0 else "EMA50 N/A"
    if pd.notna(last['EMA50']) and last['EMA50'] > 0:
        e50_pct = (last['close'] - last['EMA50']) / last['EMA50'] * 100
        e50_pos = f"{'above' if e50_pct>0 else 'below'} EMA50 by {abs(e50_pct):.2f}%"
    else:
        e50_pos = "EMA50 N/A (not enough candles)"
    e200_pos  = ("above EMA200" if last['close'] > last['EMA200'] else "below EMA200") if pd.notna(last['EMA200']) else "EMA200 N/A (not enough candles)"
    macd_dir  = "Bullish" if (pd.notna(last['MACD']) and pd.notna(last['MACD_Sig']) and last['MACD'] > last['MACD_Sig']) else "Bearish"
    stoch_st  = ("Overbought" if last['Stoch_K']>80 else ("Oversold" if last['Stoch_K']<20 else "Neutral")) if pd.notna(last['Stoch_K']) else "N/A"
    vol_r     = last['volume'] / last['VolumeMA'] if (pd.notna(last['VolumeMA']) and last['VolumeMA']>0) else 0
    vol_st    = f"High ({vol_r:.1f}x)" if vol_r>1.5 else (f"Low ({vol_r:.1f}x)" if vol_r<0.5 else f"Normal ({vol_r:.1f}x)")
    age_sec   = (datetime.now(timezone.utc) - last['timestamp'].to_pydatetime()).total_seconds()
    inc       = age_sec < TF_SECONDS.get(tf, 900)
    vol_note  = " ⚠️incomplete" if inc else ""
    roc_str   = f"{last['ROC5']:+.2f}%" if pd.notna(last['ROC5']) else "N/A"

    vwap_str = "N/A"
    if daily_vwap:
        vp = "above VWAP" if last['close'] > daily_vwap else "below VWAP"
        vwap_str = f"{daily_vwap} | Price {vp}"

    bb_line = "BB: N/A\n"
    if bb_ok and pd.notna(last.get("BB_u")) and pd.notna(last.get("BB_l")):
        bb_r   = last['BB_u'] - last['BB_l']
        bb_pct = ((last['close'] - last['BB_l']) / bb_r * 100) if bb_r > 0 else 50
        bb_pos = "Above upper" if last['close'] > last['BB_u'] else \
                 "Below lower" if last['close'] < last['BB_l'] else \
                 f"Inside ({bb_pct:.0f}% from bottom)"
        bb_line = f"BB U:{last['BB_u']:.4f} M:{last['BB_m']:.4f} L:{last['BB_l']:.4f} | {bb_pos}\n"

    sl_safe = round(atr * 1.5, 4)

    ms   = market_structure(df, tf)
    fvg  = calc_fvg(df, tf)
    bos  = calc_bos_choch(df, ms, tf)
    obs  = calc_order_blocks(df, ms, tf)
    bbk  = calc_breaker_blocks(df, obs)
    eql  = calc_equal_hl(df, tf)
    fta  = calc_fta(df, ms, fvg, obs)
    prem = calc_premium_discount(df, ms, tf)

    sh  = f"{ms['swing_high']:.4f}" if ms['swing_high'] else "---"
    sl_ = f"{ms['swing_low']:.4f}"  if ms['swing_low']  else "---"
    psh = f"{ms['prev_sh']:.4f}"    if ms['prev_sh']    else "---"
    psl = f"{ms['prev_sl']:.4f}"    if ms['prev_sl']    else "---"
    all_h = ", ".join([f"{v:.4f}" for v in ms['all_highs']]) if ms['all_highs'] else "---"
    all_l = ", ".join([f"{v:.4f}" for v in ms['all_lows']])  if ms['all_lows']  else "---"

    r5 = df.tail(5)
    kr = ", ".join([f"{v:.4f}" for v in sorted(r5['high'].tolist(), reverse=True)[:3]])
    ks = ", ".join([f"{v:.4f}" for v in sorted(r5['low'].tolist())[:3]])

    fta_r = f"{fta['resistance'][0]} @ {fta['resistance'][1]:.4f}" if fta['resistance'] else "---"
    fta_s = f"{fta['support'][0]} @ {fta['support'][1]:.4f}"      if fta['support']    else "---"

    candle_count = 30 if tf in ('1m','3m','5m') else 15
    candles = df[['time','open','high','low','close','volume']].tail(candle_count).to_string(index=False)

    tf_info  = f"[TF: {tf}]\n"
    tf_info += f"RSI:{last['RSI']:.2f} | ATR:{atr:.4f} | Safe SL dist:{sl_safe:.4f} | Momentum(ROC5):{roc_str}\n"
    tf_info += f"EMA9:{last['EMA9']:.4f} EMA21:{last['EMA21']:.4f} EMA50:{last['EMA50']:.4f} EMA200:{last['EMA200']:.4f}\n"
    tf_info += f"Trend: {ema_trend} | Price {e50_pos} | {e200_pos}\n"
    tf_info += f"VWAP(daily): {vwap_str}\n"
    tf_info += f"MACD:{last['MACD']:.4f} Sig:{last['MACD_Sig']:.4f} Hist:{last['MACD_Hist']:.4f} | {macd_dir}\n"
    tf_info += f"Stoch %K:{last['Stoch_K']:.2f} %D:{last['Stoch_D']:.2f} | {stoch_st}\n"
    tf_info += bb_line
    tf_info += f"Volume:{last['volume']:.2f}{vol_note} | VolMA20:{last['VolumeMA']:.2f} | {vol_st}\n"

    tf_info += f"\n--- MARKET STRUCTURE ---\n"
    tf_info += f"Structure: {ms['structure']} | Trend: {ms['trend']}\n"
    tf_info += f"Swing High (above price): {sh} | Prev: {psh}\n"
    tf_info += f"Swing Low  (below price): {sl_} | Prev: {psl}\n"
    tf_info += f"All Pivot Highs: {all_h}\n"
    tf_info += f"All Pivot Lows:  {all_l}\n"

    tf_info += f"\n--- SMC: BOS / CHoCH ---\n"
    tf_info += f"BOS:   {fmt_bos(bos['bos'])}\n"
    tf_info += f"CHoCH: {fmt_bos(bos['choch'])}\n"
    if bos['last_tag']:
        tf_info += f"Last BOS: {bos['last_tag']}\n"

    tf_info += f"\n--- SMC: FVG / IMBALANCE ---\n"
    tf_info += f"Bullish FVG (demand, closest first): {fmt_fvg(fvg['bullish'])}\n"
    tf_info += f"Bearish FVG (supply, closest first): {fmt_fvg(fvg['bearish'])}\n"

    tf_info += f"\n--- SMC: ORDER BLOCKS ---\n"
    tf_info += f"Bullish OB (demand): {fmt_ob(obs['bullish'])}\n"
    tf_info += f"Bearish OB (supply): {fmt_ob(obs['bearish'])}\n"

    tf_info += f"\n--- SMC: BREAKER BLOCKS ---\n"
    tf_info += f"Bull Breaker (broken bear OB -> support): {fmt_ob(bbk['bullish'])}\n"
    tf_info += f"Bear Breaker (broken bull OB -> resist):  {fmt_ob(bbk['bearish'])}\n"

    tf_info += f"\n--- SMC: EQUAL HIGHS/LOWS (liquidity pools) ---\n"
    tf_info += f"Equal Highs (above): {fmt_eq(eql['equal_highs'])}\n"
    tf_info += f"Equal Lows  (below): {fmt_eq(eql['equal_lows'])}\n"

    tf_info += f"\n--- SMC: FTA (First Trouble Area) ---\n"
    tf_info += f"FTA Resistance (for longs):  {fta_r}\n"
    tf_info += f"FTA Support    (for shorts): {fta_s}\n"

    tf_info += f"\n--- SMC: PREMIUM / DISCOUNT ---\n"
    tf_info += f"Zone: {prem['zone']}\n"
    if prem['eq']:
        tf_info += f"Range: {prem['sl']:.4f} — {prem['sh']:.4f} | EQ: {prem['eq']:.4f}\n"
        tf_info += f"Premium (>=75%): {prem['premium_min']:.4f} | Discount (<=25%): {prem['discount_max']:.4f}\n"

    tf_info += f"\n--- RECENT LEVELS ---\n"
    tf_info += f"Recent Resistance: {kr} | Recent Support: {ks}\n"
    tf_info += f"Last {candle_count} candles:\n{candles}\n\n"

    print(tf_info.strip())
    output_text += tf_info

# ──────────────────────────────────────────────────────────────────────────────
# CLIPBOARD
# ──────────────────────────────────────────────────────────────────────────────
try:
    proc = subprocess.Popen(['clip'], stdin=subprocess.PIPE, shell=True)
    proc.communicate(input=output_text.encode('utf-8'))
    print("\n" + "="*65)
    print("Analysis complete! Result copied to clipboard.")
    print("Paste into GemBot with Ctrl+V")
    print("="*65)
except Exception as e:
    print(f"Clipboard error: {e}")

input("\nPress Enter to exit...")