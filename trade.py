# -*- coding: utf-8 -*-
# crypto_scanner_v15_stable.py
# SMC Full Toolkit | Hybrid Engine | Sync Network | Vectorized Logic

import ccxt
import pandas as pd
import numpy as np
from datetime import datetime, timezone
import pandas_ta as ta
import subprocess
import sys
import os
import concurrent.futures
import time

try:
    import pyperclip
    HAS_PYPERCLIP = True
except ImportError:
    HAS_PYPERCLIP = False

class CryptoScanner:
    EXCHANGES = {
        '1': ('binance', ccxt.binance, {'options': {'defaultType': 'future'}}),
        '2': ('binanceusdm', ccxt.binanceusdm, {}),
        '3': ('okx', ccxt.okx, {'options': {'defaultType': 'swap'}}),
        '4': ('bybit', ccxt.bybit, {'options': {'defaultType': 'linear'}}),
        '5': ('bitget', ccxt.bitget, {'options': {'defaultType': 'swap'}}),
        '6': ('whitebit', ccxt.whitebit, {}),
    }

    AVAILABLE_TFS = ['1m', '3m', '5m', '15m', '30m', '1h', '4h', '1d', '3d', '1w', '1M']

    TF_CONFIG = {
        '1m': {'pivot_n': 30, 'confirm': 1, 'smc_lb': 50, 'eq_lb': 30, 'eq_thresh': 0.03, 'fvg_gap': 0.03, 'pivot_age': 25},
        '3m': {'pivot_n': 40, 'confirm': 1, 'smc_lb': 60, 'eq_lb': 40, 'eq_thresh': 0.03, 'fvg_gap': 0.04, 'pivot_age': 30},
        '5m': {'pivot_n': 50, 'confirm': 2, 'smc_lb': 80, 'eq_lb': 50, 'eq_thresh': 0.04, 'fvg_gap': 0.05, 'pivot_age': 40},
        '15m': {'pivot_n': 60, 'confirm': 2, 'smc_lb': 100, 'eq_lb': 60, 'eq_thresh': 0.05, 'fvg_gap': 0.08, 'pivot_age': None},
        '30m': {'pivot_n': 70, 'confirm': 2, 'smc_lb': 100, 'eq_lb': 60, 'eq_thresh': 0.05, 'fvg_gap': 0.10, 'pivot_age': None},
        '1h': {'pivot_n': 80, 'confirm': 2, 'smc_lb': 120, 'eq_lb': 80, 'eq_thresh': 0.07, 'fvg_gap': 0.15, 'pivot_age': None},
        '4h': {'pivot_n': 100, 'confirm': 1, 'smc_lb': 150, 'eq_lb': 100, 'eq_thresh': 0.10, 'fvg_gap': 0.20, 'pivot_age': None},
        '1d': {'pivot_n': 120, 'confirm': 1, 'smc_lb': 200, 'eq_lb': 120, 'eq_thresh': 0.20, 'fvg_gap': 0.30, 'pivot_age': None},
        '3d': {'pivot_n': 60, 'confirm': 1, 'smc_lb': 100, 'eq_lb': 60, 'eq_thresh': 0.40, 'fvg_gap': 0.50, 'pivot_age': None},
        '1w': {'pivot_n': 52, 'confirm': 1, 'smc_lb': 80, 'eq_lb': 52, 'eq_thresh': 0.60, 'fvg_gap': 1.00, 'pivot_age': None},
        '1M': {'pivot_n': 24, 'confirm': 1, 'smc_lb': 36, 'eq_lb': 24, 'eq_thresh': 1.00, 'fvg_gap': 2.00, 'pivot_age': None},
    }

    TF_SECONDS = {
        '1m': 60, '3m': 180, '5m': 300, '15m': 900, '30m': 1800,
        '1h': 3600, '4h': 14400, '1d': 86400, '3d': 259200, '1w': 604800, '1M': 2592000
    }

    def __init__(self, ex_input, symbol_input, tf_input):
        ex_input = ex_input if ex_input in self.EXCHANGES else '2'
        self.ex_name, self.ex_class, self.ex_opts = self.EXCHANGES[ex_input]
        
        # Check for Proxy in Environment (Critical for Render.com / Restricted Regions)
        raw_proxy = os.getenv('PROXY_URL') or os.getenv('HTTPS_PROXY') or os.getenv('HTTP_PROXY')
        
        def format_proxy(url):
            if not url: return None
            url = url.strip()
            if url.startswith('http'): return url
            # Common raw format host:port:user:pass
            parts = url.split(':')
            if len(parts) == 4:
                return f"http://{parts[2]}:{parts[3]}@{parts[0]}:{parts[1]}"
            return url # Fallback

        proxy_url = format_proxy(raw_proxy)
        self.proxies = {'http': proxy_url, 'https': proxy_url} if proxy_url else None
        
        self.exchange = self.ex_class({'enableRateLimit': True, 'proxies': self.proxies, **self.ex_opts})

        self.raw_symbol = symbol_input if symbol_input else 'BTC'
        self.symbol = None 
        
        timeframes = [tf.strip() for tf in tf_input.split(',')] if tf_input else ['15m', '1h', '4h']
        self.timeframes = [tf for tf in timeframes if tf in self.AVAILABLE_TFS] or ['15m', '1h', '4h']

        self.limit_candles = 400
        self.min_candles = 30
        self.ob_impulse_mult = 1.5

        self.results = {}
        self.gen_data = {}
        self.daily_vwap = None
        self.gen_err = None

    def _retry_fetch(self, func, *args, retries=3, delay=1.5, **kwargs):
        for i in range(retries):
            try:
                return func(*args, **kwargs)
            except (ccxt.NetworkError, ccxt.RequestTimeout) as e:
                # Network issues (timeout, DNS, connection) - worth retrying
                if i == retries - 1:
                    return e
                time.sleep(delay)
            except ccxt.BaseError as e:
                # Fatal API errors (BadSymbol, RateLimit, Authentication) - stop immediately
                return e
            except Exception as e:
                # Local runtime errors (Pandas operation issues, etc.) - stop immediately
                return e
        return None

    def cfg(self, tf):
        return self.TF_CONFIG.get(tf, self.TF_CONFIG['15m'])

    @staticmethod
    def _fmt_num(val, precision=4, default="N/A"):
        return f"{val:.{precision}f}" if pd.notna(val) and val is not None else default

    @staticmethod
    def get_session_context():
        now = datetime.now(timezone.utc)
        time_val = now.hour + now.minute / 60.0

        sessions = []
        if 0 <= time_val < 8: sessions.append("Asia")
        if 8 <= time_val < 16.5: sessions.append("London")
        if 13.5 <= time_val < 20: sessions.append("NY")
        
        return " + ".join(sessions) if sessions else "Transition/Low liquidity"

    @staticmethod
    def find_pivots_vectorized(series: pd.Series, confirm: int, mode: str) -> pd.Series:
        window = confirm * 2 + 1
        ref = series.rolling(window=window, center=True).max() if mode == 'high' else series.rolling(window=window, center=True).min()
        return series == ref

    def market_structure(self, df: pd.DataFrame, tf: str) -> dict:
        c = self.cfg(tf)
        n = min(c['pivot_n'], len(df))
        conf = c['confirm']
        max_age = c['pivot_age']
        close = df.iloc[-1]['close']

        recent = df.tail(n).copy().reset_index(drop=True)
        recent['is_ph'] = self.find_pivots_vectorized(recent['high'], conf, 'high')
        recent['is_pl'] = self.find_pivots_vectorized(recent['low'], conf, 'low')

        last_idx = len(recent) - 1
        ph_mask = recent['is_ph']
        pl_mask = recent['is_pl']
        
        if max_age is not None:
            ph_mask = ph_mask & ((last_idx - recent.index) <= max_age)
            pl_mask = pl_mask & ((last_idx - recent.index) <= max_age)

        ph_vals = recent.loc[ph_mask, 'high'].tolist()
        pl_vals = recent.loc[pl_mask, 'low'].tolist()

        hh = ("HH" if ph_vals[-1] > ph_vals[-2] else "LH") if len(ph_vals) >= 2 else "N/A"
        ll = ("HL" if pl_vals[-1] > pl_vals[-2] else "LL") if len(pl_vals) >= 2 else "LL"
        trend = "BULLISH" if hh == "HH" and ll == "HL" else "BEARISH" if hh == "LH" and ll == "LL" else "MIXED"

        all_h = sorted(set(ph_vals), reverse=True)
        all_l = sorted(set(pl_vals))

        valid_h = [v for v in all_h if v > close]
        valid_l = [v for v in all_l if v < close]

        sh = valid_h[0] if valid_h else (float(recent['high'].max()) if float(recent['high'].max()) > close else None)
        sl = valid_l[0] if valid_l else (float(recent['low'].min()) if float(recent['low'].min()) < close else None)
        
        psh = valid_h[1] if len(valid_h) >= 2 else (all_h[1] if len(all_h) >= 2 else None)
        psl = valid_l[1] if len(valid_l) >= 2 else (all_l[1] if len(all_l) >= 2 else None)

        return {
            "structure": f"{hh}/{ll}",
            "trend": trend,
            "swing_high": sh,
            "swing_low": sl,
            "prev_sh": psh,
            "prev_sl": psl,
            "all_highs": all_h[:5],
            "all_lows": all_l[:5],
            "ph_chron": ph_vals,
            "pl_chron": pl_vals,
        }

    def calc_fib_retest(self, df: pd.DataFrame, ms: dict) -> dict:
        close = df.iloc[-1]['close']
        trend = ms['trend']
        sh = ms['swing_high']
        sl = ms['swing_low']

        if not sh or not sl or sh <= sl:
            return {"state": "N/A", "retest_zone": "N/A", "fibs": {}}

        rng = sh - sl
        if trend == "BULLISH":
            f382, f500, f618 = sh - (rng * 0.382), sh - (rng * 0.5), sh - (rng * 0.618)
            state = "OTE Retest Zone" if f618 <= close <= f382 else ("In Extension" if close > sh else "Early Retest")
            zone = f"[{f618:.4f} - {f382:.4f}]"
        elif trend == "BEARISH":
            f382, f500, f618 = sl + (rng * 0.382), sl + (rng * 0.5), sl + (rng * 0.618)
            state = "OTE Retest Zone" if f382 <= close <= f618 else ("In Extension" if close < sl else "Early Retest")
            zone = f"[{f382:.4f} - {f618:.4f}]"
        else:
            return {"state": "MIXED", "retest_zone": "N/A", "fibs": {}}

        return {
            "state": state,
            "retest_zone": zone,
            "fibs": {"0.382": f382, "0.5": f500, "0.618": f618, "0.786": (sh - rng*0.786 if trend=="BULLISH" else sl + rng*0.786)}
        }

    def calc_fvg(self, df: pd.DataFrame, tf: str) -> dict:
        c = self.cfg(tf)
        lookback = min(c['smc_lb'], len(df))
        min_gap = c['fvg_gap'] / 100.0
        recent = df.tail(lookback).copy()
        close = df.iloc[-1]['close']
        
        recent['h_s2'] = recent['high'].shift(2)
        recent['l_s2'] = recent['low'].shift(2)
        
        gap_bull = (recent['low'] - recent['h_s2']) / recent['h_s2']
        gap_bear = (recent['l_s2'] - recent['high']) / recent['l_s2']
        
        bull_mask = (recent['low'] > recent['h_s2']) & (gap_bull >= min_gap) & (close > recent['h_s2']) & ~((recent['h_s2'] <= close) & (close <= recent['low']))
        bear_mask = (recent['high'] < recent['l_s2']) & (gap_bear >= min_gap) & (close < recent['l_s2']) & ~((recent['high'] <= close) & (close <= recent['l_s2']))
        
        bull_fvg = list(zip(recent.loc[bull_mask, 'h_s2'].round(4), recent.loc[bull_mask, 'low'].round(4)))
        bear_fvg = list(zip(recent.loc[bear_mask, 'high'].round(4), recent.loc[bear_mask, 'l_s2'].round(4)))

        return {
            'bullish': sorted(bull_fvg, key=lambda x: abs(close - x[0]))[:3],
            'bearish': sorted(bear_fvg, key=lambda x: abs(close - x[1]))[:3]
        }

    def calc_bos_choch(self, df: pd.DataFrame, ms: dict, tf: str) -> dict:
        ph, pl = ms['ph_chron'], ms['pl_chron']
        if len(ph) < 2 or len(pl) < 2:
            return {'bos': [], 'choch': [], 'last_tag': None}

        recent = df.tail(min(self.cfg(tf)['smc_lb'], len(df))).reset_index(drop=True)
        total = len(recent)
        trend, prev_sh, prev_sl = ms['trend'], ph[-2], pl[-2]
        
        mask_bull = (recent['close'].shift(1) <= prev_sh) & (recent['close'] > prev_sh)
        mask_bear = (recent['close'].shift(1) >= prev_sl) & (recent['close'] < prev_sl)

        events = []
        for idx in recent[mask_bull].index:
            events.append({'type': 'CHoCH' if trend == 'BEARISH' else 'BOS', 'dir': 'BULLISH', 'level': round(prev_sh, 4), 'age': total - idx})
        for idx in recent[mask_bear].index:
            events.append({'type': 'CHoCH' if trend == 'BULLISH' else 'BOS', 'dir': 'BEARISH', 'level': round(prev_sl, 4), 'age': total - idx})

        events.sort(key=lambda x: x['age'], reverse=True)

        def tag(e):
            t = "FRESH" if e['age'] <= 5 else ("RECENT" if e['age'] <= 20 else "STALE")
            return f"{e['dir']} {e['type']} @ {e['level']} ({e['age']} bars ago — {t})"

        bos_ev = [e for e in events if e['type'] == 'BOS']
        choch_ev = [e for e in events if e['type'] == 'CHoCH']
        
        return {
            'bos': [tag(e) for e in bos_ev[-2:]],
            'choch': [tag(e) for e in choch_ev[-2:]],
            'last_tag': tag(bos_ev[-1]) if bos_ev else None,
        }

    def calc_order_blocks(self, df: pd.DataFrame, ms: dict, tf: str) -> dict:
        recent = df.tail(min(self.cfg(tf)['smc_lb'], len(df))).copy()
        body = (recent['close'] - recent['open']).abs()
        avg_b = body.rolling(20).mean()
        prev_sh, prev_sl = ms['prev_sh'], ms['prev_sl']
        
        is_bear = recent['close'] < recent['open']
        is_bull = recent['close'] > recent['open']
        
        bear_top = recent[['open', 'close']].max(axis=1).where(is_bear).ffill().shift(1)
        bear_bot = recent[['open', 'close']].min(axis=1).where(is_bear).ffill().shift(1)
        
        bull_top = recent[['open', 'close']].max(axis=1).where(is_bull).ffill().shift(1)
        bull_bot = recent[['open', 'close']].min(axis=1).where(is_bull).ffill().shift(1)
        
        bull_cond = is_bull & (body > self.ob_impulse_mult * avg_b)
        if prev_sh is not None: bull_cond &= (recent['close'] > prev_sh)
        else: bull_cond &= False

        bear_cond = is_bear & (body > self.ob_impulse_mult * avg_b)
        if prev_sl is not None: bear_cond &= (recent['close'] < prev_sl)
        else: bear_cond &= False

        bull_obs_raw = zip(bear_bot[bull_cond], bear_top[bull_cond])
        bear_obs_raw = zip(bull_bot[bear_cond], bull_top[bear_cond])

        bull_obs = [{'bot': round(b, 4), 'top': round(t, 4)} for b, t in bull_obs_raw if pd.notna(b)]
        bear_obs = [{'bot': round(b, 4), 'top': round(t, 4)} for b, t in bear_obs_raw if pd.notna(b)]

        def dedup(lst):
            seen, out = set(), []
            for ob in lst:
                if (ob['bot'], ob['top']) not in seen:
                    seen.add((ob['bot'], ob['top']))
                    out.append(ob)
            return out

        return {'bullish': dedup(bull_obs)[-3:], 'bearish': dedup(bear_obs)[-3:]}

    def calc_breaker_blocks(self, df: pd.DataFrame, obs: dict) -> dict:
        close = df.iloc[-1]['close']
        return {
            'bullish': [ob for ob in obs.get('bearish', []) if close > ob['top']],
            'bearish': [ob for ob in obs.get('bullish', []) if close < ob['bot']],
        }

    def calc_equal_hl(self, df: pd.DataFrame, tf: str) -> dict:
        c = self.cfg(tf)
        recent = df.tail(min(c['eq_lb'], len(df)))
        close = df.iloc[-1]['close']
        highs, lows = recent['high'].values, recent['low'].values
        eq_highs, eq_lows = [], []

        if len(highs) > 1:
            diffs_h = np.abs(np.subtract.outer(highs, highs)) / highs[:, None] * 100
            np.fill_diagonal(diffs_h, np.inf)
            i_idx, j_idx = np.where((diffs_h <= c['eq_thresh']) & (np.tri(len(highs), k=-1) == 0))
            seen_h = set()
            for i, j in zip(i_idx, j_idx):
                level = round((highs[i] + highs[j]) / 2, 4)
                if level > close and level not in seen_h:
                    eq_highs.append({'level': level, 'diff': round(diffs_h[i, j], 3)})
                    seen_h.add(level)

        if len(lows) > 1:
            diffs_l = np.abs(np.subtract.outer(lows, lows)) / lows[:, None] * 100
            np.fill_diagonal(diffs_l, np.inf)
            i_idx_l, j_idx_l = np.where((diffs_l <= c['eq_thresh']) & (np.tri(len(lows), k=-1) == 0))
            seen_l = set()
            for i, j in zip(i_idx_l, j_idx_l):
                level = round((lows[i] + lows[j]) / 2, 4)
                if level < close and level not in seen_l:
                    eq_lows.append({'level': level, 'diff': round(diffs_l[i, j], 3)})
                    seen_l.add(level)

        return {
            'equal_highs': sorted(eq_highs, key=lambda x: x['level'])[:3],
            'equal_lows': sorted(eq_lows, key=lambda x: x['level'], reverse=True)[:3],
        }

    def calc_premium_discount(self, df: pd.DataFrame, ms: dict) -> dict:
        sh, sl = ms['swing_high'], ms['swing_low']
        if not sh or not sl or sh <= sl: 
            return {'zone': 'N/A', 'eq': None, 'premium_min': None, 'discount_max': None, 'sh': sh, 'sl': sl}
        
        close = df.iloc[-1]['close']
        rng = sh - sl
        eq, prem_min, disc_max = round(sl + rng * 0.5, 4), round(sl + rng * 0.75, 4), round(sl + rng * 0.25, 4)
        pct = round((close - sl) / rng * 100, 1)
        
        if pct > 100: zone = f"ABOVE RANGE ({pct}%)"
        elif pct < 0: zone = f"BELOW RANGE ({pct}%)"
        else: zone = f"PREMIUM ({pct}%)" if pct >= 75 else f"DISCOUNT ({pct}%)" if pct <= 25 else f"EQ {'UPPER' if pct>=50 else 'LOWER'} ({pct}%)"
        
        return {'zone': zone, 'eq': eq, 'premium_min': prem_min, 'discount_max': disc_max, 'sh': sh, 'sl': sl}

    def calc_fta(self, df: pd.DataFrame, ms: dict, fvg: dict, obs: dict) -> dict:
        close = df.iloc[-1]['close']
        res, sup = [], []
        if ms['swing_high'] and ms['swing_high'] > close: res.append(('Swing High', ms['swing_high']))
        for _, t in fvg.get('bearish', []):
            if t > close: res.append(('Bear FVG', t))
        for ob in obs.get('bearish', []):
            if ob['top'] > close: res.append(('Bear OB', ob['top']))
        if ms['swing_low'] and ms['swing_low'] < close: sup.append(('Swing Low', ms['swing_low']))
        for b, _ in fvg.get('bullish', []):
            if b < close: sup.append(('Bull FVG', b))
        for ob in obs.get('bullish', []):
            if ob['bot'] < close: sup.append(('Bull OB', ob['bot']))
            
        return {'resistance': min(res, key=lambda x: x[1]) if res else None, 'support': max(sup, key=lambda x: x[1]) if sup else None}

    def fetch_daily_vwap(self):
        try:
            start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            since, end = int(start.timestamp() * 1000), int(datetime.now(timezone.utc).timestamp() * 1000)
            rows, cur = [], since
            while cur < end:
                batch = self._retry_fetch(self.exchange.fetch_ohlcv, self.symbol, '1m', since=cur, limit=500)
                if isinstance(batch, Exception) or not batch: break
                rows.extend(batch)
                if batch[-1][0] >= end or len(batch) < 500: break
                cur = batch[-1][0] + 60_000
            if rows:
                d = pd.DataFrame(rows, columns=['ts', 'o', 'h', 'l', 'c', 'v'])
                return round(float(((d['h'] + d['l'] + d['c']) / 3 * d['v']).sum() / d['v'].sum()), 4)
        except Exception:
            return None
        return None

    def fetch_tf(self, tf):
        return tf, self._retry_fetch(self.exchange.fetch_ohlcv, self.symbol, tf, limit=self.limit_candles)

    def fetch_general(self):
        try:
            t = self._retry_fetch(self.exchange.fetch_ticker, self.symbol)
            if isinstance(t, Exception): return {'err': t}
            
            fr = self._retry_fetch(self.exchange.fetch_funding_rate, self.symbol, retries=2)
            oi = self._retry_fetch(self.exchange.fetch_open_interest, self.symbol, retries=2)
            ob = self._retry_fetch(self.exchange.fetch_order_book, self.symbol, limit=100, retries=2)
            
            return {
                'ticker': t, 
                'fr': fr if not isinstance(fr, Exception) else None, 
                'oi': oi if not isinstance(oi, Exception) else None, 
                'ob': ob if not isinstance(ob, Exception) else None, 
                'ob_err': str(ob) if isinstance(ob, Exception) else None
            }
        except Exception as e: 
            return {'err': e}

    def calc_orderbook_metrics(self, ob, price):
        if not ob or not ob.get('bids') or not ob.get('asks'): return None
        bids, asks = ob['bids'], ob['asks']
        best_bid, best_ask = bids[0][0], asks[0][0]
        bid_vol_2pct = sum(v for p, v in bids if p >= best_bid * 0.98)
        ask_vol_2pct = sum(v for p, v in asks if p <= best_ask * 1.02)
        total_vol = bid_vol_2pct + ask_vol_2pct
        
        bid_vols_5 = [x[1] for x in bids if x[0] >= best_bid * 0.95]
        ask_vols_5 = [x[1] for x in asks if x[0] <= best_ask * 1.05]
        med_b, med_a = np.median(bid_vols_5) if bid_vols_5 else 0, np.median(ask_vols_5) if ask_vols_5 else 0
        
        top_b_walls = sorted([x for x in bids if x[0] >= best_bid * 0.95 and x[1] > med_b * 3], key=lambda x: x[1], reverse=True)[:3]
        top_a_walls = sorted([x for x in asks if x[0] <= best_ask * 1.05 and x[1] > med_a * 3], key=lambda x: x[1], reverse=True)[:3]
        
        return {
            'spread_pct': (best_ask - best_bid) / best_ask * 100,
            'bid_pct': (bid_vol_2pct / total_vol * 100) if total_vol > 0 else 50,
            'ask_pct': (ask_vol_2pct / total_vol * 100) if total_vol > 0 else 50,
            'b_walls': top_b_walls, 'a_walls': top_a_walls
        }

    def calc_liquidity_sweeps(self, df: pd.DataFrame, ms: dict, eql: dict) -> list:
        sweeps = []
        key_highs = list(ms['all_highs'][:5]) + [eq['level'] for eq in eql.get('equal_highs', [])]
        key_lows = list(ms['all_lows'][:5]) + [eq['level'] for eq in eql.get('equal_lows', [])]
        recent_n = 15
        if len(df) < recent_n: return sweeps
        
        recent = df.tail(recent_n)
        for idx, candle in recent.iterrows():
            c_h, c_l, c_c, age = candle['high'], candle['low'], candle['close'], (len(df) - 1) - idx
            for kh in key_highs:
                if c_h > kh and c_c < kh and (c_h - kh) / kh > 0.0005: sweeps.append({'dir': 'Bearish', 'level': kh, 'age': age})
            for kl in key_lows:
                if c_l < kl and c_c > kl and (kl - c_l) / kl > 0.0005: sweeps.append({'dir': 'Bullish', 'level': kl, 'age': age})
                
        return sorted(list({f"{s['dir']}_{s['level']}": s for s in sweeps}.values()), key=lambda x: x['age'])[:5]

    def run(self):
        try:
            # Check Proxy Status first
            if self.proxies:
                p_url = next(iter(self.proxies.values()))
                print(f"[{datetime.now().strftime('%H:%M:%S')}] NETWORK: Using proxy {p_url.split('@')[-1]}")
                
                # HEARTBEAT TEST: Check if proxy works at all (using ccxt internal request)
                try:
                    import requests
                    # Try to get public IP through the proxy
                    resp = requests.get('https://api.ipify.org?format=json', proxies=self.proxies, timeout=10, verify=False)
                    ext_ip = resp.json().get('ip', 'Unknown')
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] NETWORK: Proxy Test SUCCESS. IP through proxy is {ext_ip}")
                except Exception as p_err:
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] NETWORK: Proxy Test FAILED. Error: {p_err}")
            else:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] NETWORK: No Proxy set. Direct connection (may fail on Render).")

            # Initialize exchange here for Proxy and RateLimit safety
            self.exchange = self.ex_class({
                'enableRateLimit': True, 
                'proxies': self.proxies,
                'verify': False, # Critical: Bypass SSL cert chain issues on cloud proxies
                **self.ex_opts
            })
            
            self.exchange.load_markets()
            
            possible_symbols = [
                self.raw_symbol,
                f"{self.raw_symbol}/USDT",
                f"{self.raw_symbol}/USDT:USDT",
                f"{self.raw_symbol}/USDC:USDC",
                f"{self.raw_symbol}USDT"
            ]
            
            for sym in possible_symbols:
                if sym in self.exchange.markets:
                    self.symbol = sym
                    break
                    
            if not self.symbol:
                return f"Error: Symbol {self.raw_symbol} not found on {self.ex_name}."

            print(f"\nFetching data concurrently for {self.symbol}...")
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=max(4, len(self.timeframes)+2)) as executor:
                futures = {
                    executor.submit(self.fetch_general): 'gen',
                    executor.submit(self.fetch_daily_vwap): 'vwap'
                }
                for tf in self.timeframes:
                    futures[executor.submit(self.fetch_tf, tf)] = tf
                
                for future in concurrent.futures.as_completed(futures):
                    key = futures[future]
                    res = future.result()
                    if key == 'gen':
                        if 'err' in res: self.gen_err = res['err']
                        else: self.gen_data = res
                    elif key == 'vwap':
                        self.daily_vwap = res
                    else:
                        tf_name, tf_data = res
                        self.results[tf_name] = tf_data
                
            return self.generate_report()
            
        except Exception as e:
            return f"Run Error: {e}"

    def generate_report(self):
        if not self.symbol: return "Init failed."
        ticker, fr_data, oi_data = self.gen_data.get('ticker', {}), self.gen_data.get('fr', {}), self.gen_data.get('oi', {})
        ob, ob_err = self.gen_data.get('ob'), self.gen_data.get('ob_err')
        price = ticker.get('last', 0.0) if ticker else 0.0
        session_ctx = self.get_session_context()

        ob_str = "--- ORDERBOOK & SCALPING INFO ---\n"
        if ob_meters := self.calc_orderbook_metrics(ob, price):
            ob_str += f"Spread: {ob_meters['spread_pct']:.4f}%\n"
            ob_str += f"Imbalance (2%): Bids {ob_meters['bid_pct']:.1f}% | Asks {ob_meters['ask_pct']:.1f}%\n"
            ob_str += f"Bid Walls (-5%): {' | '.join([f'{p:.4f} ({v:,.0f})' for p, v in ob_meters['b_walls']]) or 'None'}\n"
            ob_str += f"Ask Walls (+5%): {' | '.join([f'{p:.4f} ({v:,.0f})' for p, v in ob_meters['a_walls']]) or 'None'}\n\n"
        elif ob_err: ob_str += f"Orderbook Error: {ob_err}\n\n"
        else: ob_str += "Orderbook N/A\n\n"

        fr, fr_bias = (fr_data['fundingRate'] * 100, "BEARISH (longs->shorts)" if fr_data['fundingRate'] > 0.0005 else "BULLISH (shorts->longs)" if fr_data['fundingRate'] < -0.0005 else "NEUTRAL") if fr_data and 'fundingRate' in fr_data else (0.0, "N/A")
        oi_line = f"OI: {oi_data['openInterestAmount']:.2f} (${oi_data['openInterestAmount'] * price:,.0f})\n" if oi_data and 'openInterestAmount' in oi_data else ""

        hdr = f"EXCHANGE: {self.ex_name}\n" + (f"WARNING: General data fetch failed: {self.gen_err}\n" if self.gen_err else "")
        hdr += f"PAIR: {self.symbol} | PRICE: {price:.4f} | SESSION: {session_ctx}\n"
        hdr += f"24h Vol: {ticker.get('baseVolume',0):,.2f} (${ticker.get('quoteVolume',0):,.0f})\n" if ticker else ""
        hdr += f"Funding: {fr:.4f}% -> {fr_bias}\n{oi_line}"
        hdr += f"Daily VWAP: {self.daily_vwap if self.daily_vwap else 'N/A'}\n"
        hdr += f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC\n\n{ob_str}"
        return hdr + "".join([self._process_tf(tf) for tf in self.timeframes])

    def _process_tf(self, tf):
        tf_info = f"{'='*65}\nTF: {tf}\n{'='*65}\n"
        raw = self.results.get(tf)
        if isinstance(raw, Exception): return tf_info + f"Error {tf}: {raw}\n\n"
        if not raw or len(raw) < self.min_candles: return tf_info + f"[TF:{tf}] Not enough candles. Skipped.\n\n"
        
        df = pd.DataFrame(raw, columns=['timestamp','open','high','low','close','volume'])
        df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms', utc=True)
        
        df['RSI'] = ta.rsi(df['close'], length=14)
        df['ATR'] = ta.atr(df['high'], df['low'], df['close'], length=14)
        df['EMA9'], df['EMA21'] = ta.ema(df['close'], length=9), ta.ema(df['close'], length=21)
        df['EMA50'], df['EMA200'] = ta.ema(df['close'], length=50), ta.ema(df['close'], length=200)
        df['VolumeMA'] = ta.sma(df['volume'], length=20)
        df['ROC5'] = df['close'].pct_change(5) * 100
        
        macd = ta.macd(df['close'])
        if macd is not None: df['MACD'], df['MACD_S'], df['MACD_H'] = macd.iloc[:,0], macd.iloc[:,2], macd.iloc[:,1]
        stoch = ta.stoch(df['high'], df['low'], df['close'])
        if stoch is not None: df['ST_K'], df['ST_D'] = stoch.iloc[:,0], stoch.iloc[:,1]
        bb = ta.bbands(df['close'])
        if bb is not None: df['BB_l'], df['BB_m'], df['BB_u'] = bb.iloc[:,0], bb.iloc[:,1], bb.iloc[:,2]
        
        last = df.iloc[-1]
        ms = self.market_structure(df, tf)
        fibs = self.calc_fib_retest(df, ms)
        fvg, bos, obs = self.calc_fvg(df, tf), self.calc_bos_choch(df, ms, tf), self.calc_order_blocks(df, ms, tf)
        bbk, eql, fta = self.calc_breaker_blocks(df, obs), self.calc_equal_hl(df, tf), self.calc_fta(df, ms, fvg, obs)
        prem, sweeps = self.calc_premium_discount(df, ms), self.calc_liquidity_sweeps(df, ms, eql)
        
        vol_r = last['volume'] / last['VolumeMA'] if pd.notna(last.get('VolumeMA')) and last['VolumeMA']>0 else 0
        inc = (datetime.now(timezone.utc) - last['timestamp'].to_pydatetime()).total_seconds() < self.TF_SECONDS.get(tf, 900)
        
        info = f"[TF: {tf}]\n"
        info += f"RSI:{self._fmt_num(last.get('RSI'),2)} | ATR:{self._fmt_num(last.get('ATR'))} | Safe SL dist:{self._fmt_num(last.get('ATR',0)*1.5)} | ROC5:{self._fmt_num(last.get('ROC5'),2)}%\n"
        info += f"EMA9:{self._fmt_num(last.get('EMA9'))} EMA21:{self._fmt_num(last.get('EMA21'))} EMA50:{self._fmt_num(last.get('EMA50'))} EMA200:{self._fmt_num(last.get('EMA200'))}\n"
        info += f"Trend: {ms['trend']} | Price {'above' if last['close']>last.get('EMA50',0) else 'below'} EMA50 | {'above' if last['close']>last.get('EMA200',0) else 'below'} EMA200\n"
        info += f"VWAP(daily): {self.daily_vwap if self.daily_vwap else 'N/A'} | Price {'above' if self.daily_vwap and last['close']>self.daily_vwap else 'below'} VWAP\n"
        info += f"MACD:{self._fmt_num(last.get('MACD'))} Sig:{self._fmt_num(last.get('MACD_S'))} Hist:{self._fmt_num(last.get('MACD_H'))}\n"
        info += f"Stoch %K:{self._fmt_num(last.get('ST_K'),2)} %D:{self._fmt_num(last.get('ST_D'),2)} | BB: {'Inside' if pd.notna(last.get('BB_u')) and last.get('BB_l',0)<=last['close']<=last.get('BB_u',0) else 'Outside'}\n"
        info += f"Volume:{last.get('volume',0):.2f}{' ⚠️inc' if inc else ''} | VolMA20:{last.get('VolumeMA',0):.2f} | {'High' if vol_r>1.5 else 'Low' if vol_r<0.5 else 'Normal'} ({vol_r:.1f}x)\n"

        info += f"\n--- MARKET STRUCTURE & TRADE STATE ---\n"
        info += f"Structure: {ms['structure']} | Trend: {ms['trend']}\n"
        info += f"State: {fibs['state']} | Retest Zone: {fibs['retest_zone']}\n"
        if fibs['fibs']: info += f"OTE Levels: 0.382:{fibs['fibs']['0.382']:.4f} 0.5:{fibs['fibs']['0.5']:.4f} 0.618:{fibs['fibs']['0.618']:.4f}\n"

        info += f"\n--- SMC: BOS / CHoCH ---\n"
        info += f"BOS: {' | '.join(bos['bos']) or 'None'}\nCHoCH: {' | '.join(bos['choch']) or 'None'}\n"

        info += f"\n--- SMC: FVG & ORDER BLOCKS ---\n"
        info += f"Bull FVG: {' | '.join([f'[{b:.4f}-{t:.4f}]' for b,t in fvg['bullish']]) or 'None'}\n"
        info += f"Bear FVG: {' | '.join([f'[{b:.4f}-{t:.4f}]' for b,t in fvg['bearish']]) or 'None'}\n"
        info += f"Bull OB: {' | '.join([f'[{o['bot']:.4f}-{o['top']:.4f}]' for o in obs['bullish']]) or 'None'}\n"
        info += f"Bear OB: {' | '.join([f'[{o['bot']:.4f}-{o['top']:.4f}]' for o in obs['bearish']]) or 'None'}\n"

        info += f"\n--- SMC: LIQUIDITY (EQL / Sweeps) ---\n"
        info += f"EQ Highs: {' | '.join([f'{e['level']:.4f}' for e in eql['equal_highs']]) or 'None'}\n"
        info += f"EQ Lows:  {' | '.join([f'{e['level']:.4f}' for e in eql['equal_lows']]) or 'None'}\n"
        info += "Sweeps: " + (", ".join([f"{s['dir']} {s['level']:.4f}" for s in sweeps]) if sweeps else "None") + "\n"

        info += f"\n--- P/D ZONE & FTA ---\n"
        info += f"Zone: {prem['zone']} | EQ: {self._fmt_num(prem['eq'])}\n"
        info += f"FTA Resist: {fta['resistance'][0] if fta['resistance'] else 'None'} @ {self._fmt_num(fta['resistance'][1]) if fta['resistance'] else '---'}\n"
        info += f"FTA Support: {fta['support'][0] if fta['support'] else 'None'} @ {self._fmt_num(fta['support'][1]) if fta['support'] else '---'}\n\n"
        return info

def main():
    if sys.platform == 'win32':
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
        
    print("=" * 65 + "\n  Crypto Scanner v15 Stable Hybrid | Extreme Performance\n" + "=" * 65)
    for k, (name, _, _) in CryptoScanner.EXCHANGES.items(): print(f"  {k}. {name}")
    ex_in = input("\nExchange (default 2): ").strip()
    sym_in = input("Pair (e.g. BTC): ").strip().upper()
    tf_in = input("TFs (e.g. 15m,1h,4h): ").strip()

    scanner = CryptoScanner(ex_in, sym_in, tf_in)
    report = scanner.run()
    print(report)

    if HAS_PYPERCLIP:
        try: pyperclip.copy(report); print("=" * 65 + "\nCopied to clipboard!\n" + "=" * 65)
        except Exception: pass
    else: print("=" * 65 + "\nInstall pyperclip to copy automatically.\n" + "=" * 65)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nExiting...")