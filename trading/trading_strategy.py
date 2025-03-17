import pandas as pd
import numpy as np
import logging
from datetime import datetime, timedelta
from typing import Optional
from ta.trend import MACD, EMAIndicator
from ta.momentum import RSIIndicator
from ta.volatility import BollingerBands, AverageTrueRange
from ta.momentum import StochasticOscillator

logger = logging.getLogger(__name__)

class TradingContext:
    def __init__(self):
        self.last_sell_time = None  # 마지막 매도 시간
        self.consecutive_losses = 0  # 연속 손절 횟수
        self.last_buy_time = None  # 마지막 매수 시간

trading_context = TradingContext()  # 공유 인스턴스

def trading_strategy(df: pd.DataFrame, position: int, ticker: str, buy_price: Optional[float] = None, fee_rate: float = 0.0005, trailing_stop_pct: float = 0.02) -> dict:
    """📌 코인 시장 맞춤 단타 트레이딩 전략"""

    # 🔹 매매 전략 실행 로그
    logger.info(f"📊 {ticker} 매매 전략 시작 - 보유 여부: {position}, 현재가: {df['close'].iloc[-1]}, 매수가: {buy_price}")

    if df is None or df.empty or len(df) < 200 or df.isnull().sum().sum() > 0:
        logger.warning(f"⚠️ {ticker} 데이터 부족 또는 NaN 포함 (최소 200개 필요)")
        return {"signal": "", "message": "데이터 부족 또는 NaN 포함"}

    df = df.copy().ffill().dropna()

    # MACD 계산
    macd = MACD(df['close'], window_slow=12, window_fast=26, window_sign=9)
    macd_histogram = macd.macd_diff().fillna(0).iloc[-1]

    macd_long = MACD(df['close'], window_slow=50, window_fast=200, window_sign=9)
    macd_long_histogram = macd_long.macd_diff().fillna(0).iloc[-1]

    rsi = RSIIndicator(df['close'], window=14).rsi()
    rsi_value = rsi.fillna(50).iloc[-1]

    bb_indicator = BollingerBands(df['close'], window=20)
    bb_lower = bb_indicator.bollinger_lband().fillna(df['close']).iloc[-1]

    atr = AverageTrueRange(df['high'], df['low'], df['close'], window=14).average_true_range().iloc[-1]

    df['EMA5'] = EMAIndicator(df['close'], window=5).ema_indicator().fillna(df['close'])
    df['EMA15'] = EMAIndicator(df['close'], window=15).ema_indicator().fillna(df['close'])

    latest_close = df['close'].iloc[-1]
    recent_low = df['close'].rolling(window=10).min().iloc[-1]

    volume_spike = df['volume'].iloc[-1] > df['volume'].rolling(5).mean().iloc[-1] * 1.3  # 최근 5일 평균보다 30% 이상 증가

    is_bullish = df['EMA5'].iloc[-1] > df['EMA15'].iloc[-1]
    is_bearish = df['EMA5'].iloc[-1] < df['EMA15'].iloc[-1]

    stoch = StochasticOscillator(df['high'], df['low'], df['close'], window=14, smooth_window=3)
    stoch_k = stoch.stoch().iloc[-1]
    stoch_d = stoch.stoch_signal().iloc[-1]

    # 📌 **손절 3번 이상이면 30분 동안 매수 금지**
    if trading_context.consecutive_losses >= 3 and trading_context.last_sell_time:
        time_since_last_sell = datetime.now() - trading_context.last_sell_time
        logger.warning(f"⛔ {ticker} 손절 {trading_context.consecutive_losses}번 → 매수 제한 (남은 시간: {30 - time_since_last_sell.seconds // 60}분)")
        if time_since_last_sell < timedelta(minutes=30):  # 🔥 **손절 후 30분 제한**
            logger.warning(f"⛔ {ticker} 최근 손절 {trading_context.consecutive_losses}번 → 30분 동안 매수 금지 (남은 시간: {30 - time_since_last_sell.seconds // 60}분)")
            return {"signal": "", "message": "손절 3번 초과 → 30분 동안 매수 금지"}
    else:
        trading_context.consecutive_losses = 0  # 🔥 30분이 지나면 손절 횟수 초기화

    # 📌 매수 조건
    if position == 0:
        logger.info(f"📊 {ticker} 매수 조건 평가 - EMA5: {df['EMA5'].iloc[-1]}, EMA15: {df['EMA15'].iloc[-1]}, MACD: {macd_histogram}, MACD_LONG: {macd_long_histogram}, RSI: {rsi_value}, Stoch_K: {stoch_k}, Stoch_D: {stoch_d}, 볼밴 하단: {bb_lower}, 거래량 급증 여부: {volume_spike}")

        # ✅ 손절 횟수에 따라 투자 비율을 점진적으로 줄이기
        investment_ratio = max(0.1, 1.0 - (trading_context.consecutive_losses * 0.1))
        logger.info(f"📉 {ticker} 투자 비율 조정: {investment_ratio * 100:.1f}% (손절 횟수: {trading_context.consecutive_losses})")

        # ✅ 손절 5번 이상이면 RSI 30 이하 & MACD 골든크로스가 발생해야만 매수 가능
        if trading_context.consecutive_losses >= 5:
            if rsi_value >= 30 or macd_histogram <= 0:
                logger.warning(f"⛔ {ticker} 연속 손절 {trading_context.consecutive_losses}번 → RSI 30 이하 & MACD 골든크로스 필요 (현재 RSI: {rsi_value:.2f}, MACD: {macd_histogram:.2f})")
                return {"signal": "", "message": "연속 손절 5번 초과 → RSI 30 이하 & MACD 골든크로스 필요"}

        # ✅ 손절 7번 이상이면 거래량 급증도 필요
        if trading_context.consecutive_losses >= 7:
            if not volume_spike:
                logger.warning(f"⛔ {ticker} 연속 손절 {trading_context.consecutive_losses}번 → 추가적으로 거래량 급증 필요")
                return {"signal": "", "message": "연속 손절 7번 초과 → 거래량 급증 필요"}

        if is_bullish and latest_close > df['EMA5'].iloc[-1] and macd_histogram > 0 and macd_long_histogram > 0 and volume_spike and stoch_k > stoch_d and rsi_value > 50:
            logger.info(f"✅ {ticker} 상승장 매수 조건 충족: {is_bullish}, {latest_close}, {df['EMA5'].iloc[-1]}, {macd_histogram}, {macd_long_histogram}, {volume_spike}, {stoch_k}, {stoch_d}, {rsi_value}")
            trading_context.last_buy_time = datetime.now()
            return {"signal": "buy", "message": "상승장 매수"}

        if is_bearish and rsi_value < 30 and latest_close > recent_low and stoch_k < 20:
            logger.info(f"✅ {ticker} 하락장 반등 매수 신호 트리거 - RSI: {rsi_value}, 최저가: {recent_low}, Stoch_K: {stoch_k}")
            trading_context.last_buy_time = datetime.now()
            return {"signal": "buy", "message": "하락장 반등 매수"}

        if latest_close <= bb_lower and rsi_value < 35:
            logger.info(f"✅ {ticker} 볼린저 밴드 하단 반등 매수 - 현재가: {latest_close}, 볼밴 하단: {bb_lower}, RSI: {rsi_value}")
            trading_context.last_buy_time = datetime.now()
            return {"signal": "buy", "message": "볼린저 밴드 하단 반등 매수"}

        # 연속 손절 후 RSI 25 이하 & MACD 상승 골든크로스 시 강제 매수
        if trading_context.consecutive_losses >= 3 and rsi_value < 25 and macd_histogram > 0:
            logger.info(f"🔥 {ticker} RSI 과매도 + MACD 골든크로스 → 강제 매수")
            trading_context.last_buy_time = datetime.now()
            return {"signal": "buy", "message": "RSI 과매도 + MACD 반등 강제 매수"}

    # 📌 매도 조건
    if position == 1 and buy_price is not None:
        buy_price = float(buy_price)

        # ✅ 트레일링 스탑 (내 매수가 기준)
        trailing_stop = buy_price * (1 - trailing_stop_pct)
        stop_loss = max(trailing_stop, buy_price * 0.98) * (1 - fee_rate)
        take_profit = min(buy_price * 1.03, buy_price + (atr * 2))

        # ✅ 실질 손익 계산
        net_profit = (latest_close * (1 - fee_rate)) - (buy_price * (1 + fee_rate))

        # ✅ 손절 실행
        if latest_close < stop_loss:
            trading_context.consecutive_losses += 1
            trading_context.last_sell_time = datetime.now()
            logger.info(f"❌ {ticker} 손절 실행 (손절가: {stop_loss:.2f}원, 실제 손익: {net_profit:.2f}원)")
            return {"signal": "sell", "message": f"손절 실행 (손절가: {stop_loss:.2f}원, 실제 손익: {net_profit:.2f}원)"}

        # ✅ 익절 실행
        if latest_close >= take_profit and net_profit > 0:
            trading_context.consecutive_losses = max(0, trading_context.consecutive_losses - 2)
            logger.info(f"✅ {ticker} 익절 발생 → 손절 횟수 2단계 감소 (현재 손절 횟수: {trading_context.consecutive_losses})")
            return {"signal": "sell", "message": f"익절 실행 (손절 횟수: {trading_context.consecutive_losses})"}

        return {"signal": "", "message": "매매 조건 미충족"}
