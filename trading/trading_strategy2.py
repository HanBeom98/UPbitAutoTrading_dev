import pandas as pd
import numpy as np
import logging
import os
import json
import time
import requests
from typing import Optional
from ta.trend import MACD
from ta.momentum import RSIIndicator
from ta.volatility import BollingerBands
from datetime import datetime

logger = logging.getLogger(__name__)

# ✅ 매매 전략 함수
def trading_strategy(df: pd.DataFrame, position: int, ticker: str,
    buy_time: Optional[str] = None, buy_price: Optional[float] = None) -> dict:
    """ ✅ 트레이딩 전략 최적화 """

    required_columns = ['close', 'datetime', 'open', 'low', 'volume']
    if not all(col in df.columns for col in required_columns):
        logger.error(f"DataFrame에 {required_columns} 컬럼이 포함되어야 합니다.")
        return {"signal": "", "message": ""}

    if len(df) < 200:
        return {"signal": "", "message": "데이터 부족 (최소 200개 필요)"}

    # ✅ 지표 계산
    df['EMA20'] = df['close'].ewm(span=20, adjust=False).mean()
    df['EMA50'] = df['close'].ewm(span=50, adjust=False).mean()
    df['RSI'] = RSIIndicator(df['close'], window=14).rsi()
    macd_histogram = MACD(df['close']).macd_diff().iloc[-1]
    bb_lower = BollingerBands(df['close']).bollinger_lband().iloc[-1]
    recent_df = df.tail(20)

    # ✅ 30분 쿨다운 (매수 후 30분이 지나야 재매수 가능)
    cooldown_time = 1800  # 30분 (초 단위)
    if buy_time:
        last_buy_time = pd.to_datetime(buy_time)
        time_diff = (df['datetime'].iloc[-1] - last_buy_time).total_seconds()
        if time_diff < cooldown_time:
            return {"signal": "", "message": "최근 매수 이후 쿨다운 미경과"}

    # ✅ 하락장 체크 (EMA20 < EMA50 이지만 RSI 30 이하면 예외적으로 매수 허용)
    if df['EMA20'].iloc[-1] < df['EMA50'].iloc[-1] and df['RSI'].iloc[-1] > 30:
        return {"signal": "", "message": "하락장 - 매수 금지"}

    # ✅ 매수 조건 (RSI 40 이하, MACD 신호)
    rsi_under_40 = df['RSI'].iloc[-1] < 40
    macd_cross_up = macd_histogram > 0

    if position == 0 and rsi_under_40 and macd_cross_up:
        return {"signal": "buy", "message": "RSI 40 이하 & MACD 상승 신호"}

    # ✅ 매도 조건 (손절, 익절, 트레일링 스탑)
    if position == 1:
        if not buy_time or not buy_price:
            return {"signal": "", "message": ""}

        current_price = df['close'].iloc[-1]
        max_profit = df['close'].max()
        current_rsi = df['RSI'].iloc[-1]

        # ✅ 손절 (-3% 이상 하락)
        if current_price < buy_price * 0.97 and current_rsi < 50:
            return {"signal": "sell", "message": "손절 (-3% 이상 하락 & RSI 50 이하)"}

        # ✅ 트레일링 스탑 (최고가 대비 4% 하락 시 매도)
        if max_profit > buy_price * 1.1 and current_price < max_profit * 0.96 and current_rsi < 55:
            return {"signal": "sell", "message": "트레일링 스탑 (최고가 대비 4% 하락 & RSI 55 이하)"}

        # ✅ MACD 하락 & 거래량 증가 → 매도
        if macd_histogram < 0 and df['volume'].iloc[-1] > df['volume'].mean():
            return {"signal": "sell", "message": "MACD 하락 & 거래량 증가 → 매도 신호"}

        # ✅ 수익 실현
        profit_rate = ((current_price - buy_price) / buy_price) * 100
        if profit_rate > 5:
            return {"signal": "sell", "message": "5% 수익 실현"}
        elif profit_rate > 3:
            return {"signal": "sell_half", "message": "3% 수익 실현"}

    return {"signal": "", "message": ""}

