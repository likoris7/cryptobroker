import pandas as pd
import numpy as np
import setup_memory as _sm

class StrategyEngine:
    def __init__(self):
        pass

    def calculate_rsi(self, series, periods=14):
        close_delta = series.diff()
        up   = close_delta.clip(lower=0)
        down = -1 * close_delta.clip(upper=0)
        ma_up   = up.ewm(com=periods - 1, adjust=True, min_periods=periods).mean()
        ma_down = down.ewm(com=periods - 1, adjust=True, min_periods=periods).mean()
        rsi = ma_up / ma_down
        return 100 - (100 / (1 + rsi))

    def calculate_macd(self, series, fast=12, slow=26, signal=9):
        exp1   = series.ewm(span=fast, adjust=False).mean()
        exp2   = series.ewm(span=slow, adjust=False).mean()
        macd   = exp1 - exp2
        signal_line = macd.ewm(span=signal, adjust=False).mean()
        hist   = macd - signal_line
        return macd, signal_line, hist

    def calculate_ema(self, series, span):
        return series.ewm(span=span, adjust=False).mean()

    def add_indicators(self, df):
        df['rsi']  = self.calculate_rsi(df['close'], periods=14)
        df['macd'], df['macd_signal'], df['macd_hist'] = self.calculate_macd(df['close'])
        
        df['ema_50'] = self.calculate_ema(df['close'], 50)
        df['ema_200'] = self.calculate_ema(df['close'], 200)
        
        df['vol_avg'] = df['volume'].rolling(20).mean()
        
        high_low   = df['high'] - df['low']
        high_close = (df['high'] - df['close'].shift()).abs()
        low_close  = (df['low']  - df['close'].shift()).abs()
        tr = high_low.combine(high_close, max).combine(low_close, max)
        df['atr']     = tr.ewm(span=14, adjust=False).mean()
        df['atr_avg'] = df['atr'].rolling(20).mean()
        
        # Anomaly Filter: Detect if a candle is > 4x the normal ATR (Insider pump/dump)
        df['anomaly'] = (df['high'] - df['low']) > (df['atr_avg'] * 4)

        # Divergence Swing Points (Lookback = 3)
        # Swing Low confirmed 1 candle later
        df['is_swing_low'] = (df['low'].shift(1) < df['low'].shift(2)) & \
                             (df['low'].shift(1) < df['low'].shift(3)) & \
                             (df['low'].shift(1) < df['low'])
        # Swing High confirmed 1 candle later
        df['is_swing_high'] = (df['high'].shift(1) > df['high'].shift(2)) & \
                              (df['high'].shift(1) > df['high'].shift(3)) & \
                              (df['high'].shift(1) > df['high'])

        return df

    def generate_signals(self, df, current_trend=0, mtf_context=None):
        """Combined Strategy: Wyckoff Liquidity Sweep & Institutional Displacement + SMC POI Confluence"""
        df['probability'] = 0
        df['signal']      = 0
        df['sl']          = np.nan
        df['tp1']         = np.nan
        df['tp2']         = np.nan
        df['setup_id']    = ''

        prev_swing_low_price = np.nan
        prev_swing_low_rsi = np.nan
        last_swing_low_price = np.nan
        last_swing_low_rsi = np.nan
        
        prev_swing_high_price = np.nan
        prev_swing_high_rsi = np.nan
        last_swing_high_price = np.nan
        last_swing_high_rsi = np.nan

        # Smart Money Concept lists
        active_bull_obs = []
        active_bear_obs = []
        active_bull_fvgs = []
        active_bear_fvgs = []

        for i in range(15, len(df)):
            close = df['close'].iloc[i]
            high = df['high'].iloc[i]
            low = df['low'].iloc[i]
            atr = df['atr'].iloc[i]
            vol = df['volume'].iloc[i]
            vol_avg = df['vol_avg'].iloc[i]
            macd_hist = df['macd_hist'].iloc[i]
            prev_macd_hist = df['macd_hist'].iloc[i-1]
            ema50 = df['ema_50'].iloc[i]
            ema200 = df['ema_200'].iloc[i]
            rsi = df['rsi'].iloc[i]

            # 1. Update/Mitigate existing OBs and FVGs (capped to prevent leaks and speed up)
            active_bull_obs = [ob for ob in active_bull_obs if close >= ob['bottom']][-30:]
            active_bear_obs = [ob for ob in active_bear_obs if close <= ob['top']][-30:]
            active_bull_fvgs = [fvg for fvg in active_bull_fvgs if low >= fvg['bottom']][-30:]
            active_bear_fvgs = [fvg for fvg in active_bear_fvgs if high <= fvg['top']][-30:]

            # 2. Detect New FVG formed at bar i-1 (relative to i-3)
            if df['low'].iloc[i-1] > df['high'].iloc[i-3]:
                active_bull_fvgs.append({
                    'bottom': df['high'].iloc[i-3],
                    'top': df['low'].iloc[i-1],
                    'id': i-1
                })
            if df['high'].iloc[i-1] < df['low'].iloc[i-3]:
                active_bear_fvgs.append({
                    'bottom': df['high'].iloc[i-1],
                    'top': df['low'].iloc[i-3],
                    'id': i-1
                })

            # 3. Detect New OB formed at bar i-1
            body_i1 = df['close'].iloc[i-1] - df['open'].iloc[i-1]
            atr_i1 = df['atr'].iloc[i-1]
            
            has_bull_fvg = df['low'].iloc[i-1] > df['high'].iloc[i-3]
            if (body_i1 > atr_i1 * 1.3) or has_bull_fvg:
                ob_idx = None
                for idx in [i-2, i-3, i-4]:
                    if idx >= 0 and df['close'].iloc[idx] < df['open'].iloc[idx]:
                        ob_idx = idx
                        break
                if ob_idx is not None:
                    active_bull_obs.append({
                        'bottom': df['low'].iloc[ob_idx],
                        'top': df['high'].iloc[ob_idx],
                        'id': ob_idx
                    })

            has_bear_fvg = df['high'].iloc[i-1] < df['low'].iloc[i-3]
            if (body_i1 < -atr_i1 * 1.3) or has_bear_fvg:
                ob_idx = None
                for idx in [i-2, i-3, i-4]:
                    if idx >= 0 and df['close'].iloc[idx] > df['open'].iloc[idx]:
                        ob_idx = idx
                        break
                if ob_idx is not None:
                    active_bear_obs.append({
                        'bottom': df['low'].iloc[ob_idx],
                        'top': df['high'].iloc[ob_idx],
                        'id': ob_idx
                    })

            # Macro trend definition
            macro_trend = 'FLAT'
            if close > ema50 and ema50 > ema200:
                macro_trend = 'BULL'
            elif close < ema50 and ema50 < ema200:
                macro_trend = 'BEAR'

            # 4. Swing point updates (using confirmed points)
            if df['is_swing_low'].iloc[i]:
                prev_swing_low_price = last_swing_low_price
                prev_swing_low_rsi = last_swing_low_rsi
                last_swing_low_price = df['low'].iloc[i-1]
                last_swing_low_rsi = df['rsi'].iloc[i-1]

            if df['is_swing_high'].iloc[i]:
                prev_swing_high_price = last_swing_high_price
                prev_swing_high_rsi = last_swing_high_rsi
                last_swing_high_price = df['high'].iloc[i-1]
                last_swing_high_rsi = df['rsi'].iloc[i-1]

            # Manipulation/Anomaly filter
            recent_anomalies = df['anomaly'].iloc[max(0, i-12):i+1].sum()
            if recent_anomalies > 0:
                continue

            # Volatility Filter: Skip if market is dead (ATR < 75% of average ATR)
            if atr < df['atr_avg'].iloc[i] * 0.75:
                continue

            signal = 0
            is_ldsc_signal = False
            sl_price = np.nan
            poi_bottom_val = np.nan
            poi_top_val = np.nan

            # --- A. Wyckoff LDSC Sweep Strategy (Spring / Upthrust) ---
            # Long Sweep: price sweeps below recent swing low, but close reclaims it!
            if not np.isnan(last_swing_low_price) and low < last_swing_low_price and close > last_swing_low_price:
                # Institutional Displacement check (softened to 0.65 * ATR and 0.65 * Vol Avg):
                has_displacement = False
                for idx in [i, i-1, i-2]:
                    if idx >= 0:
                        c_body = df['close'].iloc[idx] - df['open'].iloc[idx]
                        c_vol = df['volume'].iloc[idx]
                        if c_body > atr * 0.65 and c_vol > vol_avg * 0.65:
                            has_displacement = True
                            break
                if has_displacement and macd_hist > prev_macd_hist:
                    signal = 1
                    is_ldsc_signal = True
                    sl_price = min(low, last_swing_low_price) - (0.1 * atr)

            # Short Sweep: price sweeps above recent swing high, but close drops below it!
            elif not np.isnan(last_swing_high_price) and high > last_swing_high_price and close < last_swing_high_price:
                # Institutional Displacement check (softened to 0.65 * ATR and 0.65 * Vol Avg):
                has_displacement = False
                for idx in [i, i-1, i-2]:
                    if idx >= 0:
                        c_body = df['open'].iloc[idx] - df['close'].iloc[idx]
                        c_vol = df['volume'].iloc[idx]
                        if c_body > atr * 0.65 and c_vol > vol_avg * 0.65:
                            has_displacement = True
                            break
                if has_displacement and macd_hist < prev_macd_hist:
                    signal = -1
                    is_ldsc_signal = True
                    sl_price = max(high, last_swing_high_price) + (0.1 * atr)

            # --- B. Classic SMC POI Confluence Strategy ---
            # Run if not already triggered by Wyckoff LDSC
            if signal == 0:
                if df['is_swing_low'].iloc[i] and not np.isnan(prev_swing_low_price):
                    if last_swing_low_price < prev_swing_low_price * 1.012 and last_swing_low_rsi > prev_swing_low_rsi:
                        is_in_poi = False
                        for ob in active_bull_obs:
                            if (last_swing_low_price >= ob['bottom'] * 0.995 and last_swing_low_price <= ob['top'] * 1.005) or \
                               (low >= ob['bottom'] * 0.995 and low <= ob['top'] * 1.005):
                                is_in_poi = True
                                poi_bottom_val = ob['bottom']
                                break
                        if not is_in_poi:
                            for fvg in active_bull_fvgs:
                                if (last_swing_low_price >= fvg['bottom'] * 0.995 and last_swing_low_price <= fvg['top'] * 1.005) or \
                                   (low >= fvg['bottom'] * 0.995 and low <= fvg['top'] * 1.005):
                                    is_in_poi = True
                                    poi_bottom_val = fvg['bottom']
                                    break
                        if is_in_poi and macd_hist > prev_macd_hist and vol > vol_avg * 0.5:
                            signal = 1

                elif df['is_swing_high'].iloc[i] and not np.isnan(prev_swing_high_price):
                    if last_swing_high_price > prev_swing_high_price * 0.988 and last_swing_high_rsi < prev_swing_high_rsi:
                        is_in_poi = False
                        for ob in active_bear_obs:
                            if (last_swing_high_price >= ob['bottom'] * 0.995 and last_swing_high_price <= ob['top'] * 1.005) or \
                               (high >= ob['bottom'] * 0.995 and high <= ob['top'] * 1.005):
                                is_in_poi = True
                                poi_top_val = ob['top']
                                break
                        if not is_in_poi:
                            for fvg in active_bear_fvgs:
                                if (last_swing_high_price >= fvg['bottom'] * 0.995 and last_swing_high_price <= fvg['top'] * 1.005) or \
                                   (high >= fvg['bottom'] * 0.995 and high <= fvg['top'] * 1.005):
                                    is_in_poi = True
                                    poi_top_val = fvg['top']
                                    break
                        if is_in_poi and macd_hist < prev_macd_hist and vol > vol_avg * 0.5:
                            signal = -1

            # 5. Process execution and probabilities
            if signal == 1:
                prob = 75 if is_ldsc_signal else 70
                if last_swing_low_rsi <= 35: prob += 10
                if vol > vol_avg * 1.1: prob += 10
                
                div_strength = 'strong' if not is_ldsc_signal and (last_swing_low_rsi - prev_swing_low_rsi) > 5 else 'weak'
                sid_type = 'LDSC_LONG' if is_ldsc_signal else 'LONG_SMC'
                sid = _sm.build_setup_id(sid_type, rsi, macd_hist, div_strength, macro_trend)
                conf = _sm.get_confidence(sid)
                prob = min(prob + conf['prob_delta'], 99)

                if not conf['suppressed'] and prob >= 50:
                    if is_ldsc_signal:
                        sl = sl_price
                    else:
                        sl = poi_bottom_val - (0.1 * atr)
                    sl = min(sl, df['low'].iloc[i] - 0.25 * atr)
                    
                    df.at[df.index[i], 'signal'] = 1
                    df.at[df.index[i], 'probability'] = prob
                    df.at[df.index[i], 'sl'] = sl
                    df.at[df.index[i], 'setup_id'] = sid
                    print(f"[SIGNAL] LONG {sid_type} {sid} | Prob: {prob}% | SL: {sl:.5f}")

            elif signal == -1:
                prob = 75 if is_ldsc_signal else 70
                if last_swing_high_rsi >= 65: prob += 10
                if vol > vol_avg * 1.1: prob += 10
                
                div_strength = 'strong' if not is_ldsc_signal and (prev_swing_high_rsi - last_swing_high_rsi) > 5 else 'weak'
                sid_type = 'LDSC_SHORT' if is_ldsc_signal else 'SHORT_SMC'
                sid = _sm.build_setup_id(sid_type, rsi, macd_hist, div_strength, macro_trend)
                conf = _sm.get_confidence(sid)
                prob = min(prob + conf['prob_delta'], 99)

                if not conf['suppressed'] and prob >= 50:
                    if is_ldsc_signal:
                        sl = sl_price
                    else:
                        sl = poi_top_val + (0.1 * atr)
                    sl = max(sl, df['high'].iloc[i] + 0.25 * atr)
                    
                    df.at[df.index[i], 'signal'] = -1
                    df.at[df.index[i], 'probability'] = prob
                    df.at[df.index[i], 'sl'] = sl
                    df.at[df.index[i], 'setup_id'] = sid
                    print(f"[SIGNAL] SHORT {sid_type} {sid} | Prob: {prob}% | SL: {sl:.5f}")

        return df
