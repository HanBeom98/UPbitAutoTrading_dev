import pandas as pd
import logging
from datetime import datetime
from typing import Optional
from ta.trend import MACD, EMAIndicator
from ta.momentum import RSIIndicator
from ta.volatility import BollingerBands, AverageTrueRange
from ta.momentum import StochasticOscillator

from trading.trade import calculate_stop_loss_take_profit

logger = logging.getLogger(__name__)

class TradingContext:
    def __init__(self):
        self.last_sell_time = None  # 마지막 매도 시간
        self.consecutive_losses = 0  # 연속 손절 횟수
        self.last_buy_time = None  # 마지막 매수 시간

trading_context = TradingContext()  # 공유 인스턴스

def trading_strategy(df_5m: pd.DataFrame, df_15m: pd.DataFrame, position: int, ticker: str,
                    buy_price: Optional[float] = None, fee_rate: float = 0.0005,
                    ) -> dict:
    """📌 5분봉 + 15분봉을 활용한 단타 트레이딩 전략"""

    # 🔹 매매 전략 실행 로그
    logger.info(f"📊 {ticker} 매매 전략 시작 - 보유 여부: {position}, 현재가: {df_5m['close'].iloc[-1]}, 매수가: {buy_price}")

    # ✅ **데이터 정리 (결측치 처리)**
    df_5m = df_5m.copy().ffill().dropna()
    df_15m = df_15m.copy().ffill().dropna()

    # 🔥 데이터 유효성 검사 (5분봉 & 15분봉)
    if df_5m.empty or len(df_5m) < 200:
        logger.warning(f"⚠️ {ticker} 5분봉 데이터 부족")
        return {"signal": "", "message": "5분봉 데이터 부족"}

    if df_15m.empty or len(df_15m) < 100:
        logger.warning(f"⚠️ {ticker} 15분봉 데이터 부족")
        return {"signal": "", "message": "15분봉 데이터 부족"}

    # ✅ MACD 계산 (5분봉 + 15분봉)
    macd_5m = MACD(df_5m['close'], window_slow=12, window_fast=26, window_sign=9).macd_diff().fillna(0).iloc[-1]
    macd_15m = MACD(df_15m['close'], window_slow=12, window_fast=26, window_sign=9).macd_diff().fillna(0).iloc[-1]

    # ✅ 🔥 **장기 MACD 추가 (50, 200 기준)**
    macd_long = MACD(df_5m['close'], window_slow=200, window_fast=50, window_sign=9)
    macd_long_prev = macd_long.macd_diff().fillna(0).iloc[-2]  # 이전 MACD 값
    macd_long_histogram = macd_long.macd_diff().fillna(0).iloc[-1]

    # MACD가 음수에서 양수로 변하면 골든크로스 발생!
    if macd_long_prev < 0 < macd_long_histogram:
        logger.info(f"🔥 {ticker} 장기 MACD 골든크로스 발생!")

    # ✅ RSI 계산 (5분봉 + 15분봉)
    rsi_5m = RSIIndicator(df_5m['close'], window=14).rsi().fillna(50).iloc[-1]
    rsi_15m = RSIIndicator(df_15m['close'], window=14).rsi().fillna(50).iloc[-1]

    # ✅ 볼린저 밴드 (5분봉)
    bb_indicator = BollingerBands(df_5m['close'], window=20)
    bb_lower_5m = bb_indicator.bollinger_lband().fillna(df_5m['close'])

    latest_close = df_5m['close'].iloc[-1]

    # ✅ 볼린저 밴드 하단 터치 후, 3개 캔들 연속 상승 시 매수
    if (
        df_5m['close'].iloc[-3] < bb_lower_5m.iloc[-3] and
        df_5m['close'].iloc[-3] < df_5m['close'].iloc[-2] < latest_close
    ):
        return {"signal": "buy", "message": "볼린저 밴드 강한 반등 확인"}

    # ✅ ATR 계산 (5분봉)
    atr = AverageTrueRange(df_5m['high'], df_5m['low'], df_5m['close'], window=14).average_true_range().iloc[-1]

    # ✅ EMA 계산 (5분봉)
    df_5m['EMA5'] = EMAIndicator(df_5m['close'], window=5).ema_indicator().fillna(df_5m['close'])
    df_5m['EMA15'] = EMAIndicator(df_5m['close'], window=15).ema_indicator().fillna(df_5m['close'])

    latest_close = df_5m['close'].iloc[-1]
    recent_low = df_5m['close'].rolling(window=10).min().iloc[-1]
    volume_spike = df_5m['volume'].iloc[-1] > df_5m['volume'].rolling(5).mean().iloc[-1] * 1.3

    is_bullish = df_5m['EMA5'].iloc[-1] > df_5m['EMA15'].iloc[-1]
    is_bearish = df_5m['EMA5'].iloc[-1] < df_5m['EMA15'].iloc[-1]

    # 📌 **손절 3번 이상이면 30분 동안 매수 금지**
    if trading_context.last_sell_time:
        time_since_last_sell = (datetime.now() - trading_context.last_sell_time).total_seconds()
        atr_multiplier = max(1, min(2, atr / df_5m['close'].iloc[-1] * 100))  # 최소 1, 최대 2 배수로 제한
        base_limit = 1800 + (trading_context.consecutive_losses - 3) * 600
        limit_time = min(max(base_limit * atr_multiplier, 1800), 7200)  # 최소 30분, 최대 2시간으로 제한
        if time_since_last_sell < limit_time:
            logger.warning(f"⛔ {ticker} 최근 손절 {trading_context.consecutive_losses}번 → {limit_time // 60}분 동안 매수 금지")
            return {"signal": "", "message": f"손절 {trading_context.consecutive_losses}번 → {limit_time//60}분 동안 매수 금지"}

        # 🔥 30분 경과 후 손절 횟수 점진적 감소
        trading_context.consecutive_losses = max(1, trading_context.consecutive_losses - 1)
        logger.info(f"✅ {ticker} 손절 제한 시간 종료 → 손절 횟수 감소: {trading_context.consecutive_losses}")

    # ✅ Stochastic Oscillator 계산 (5분봉 기준)
    stoch = StochasticOscillator(df_5m['high'], df_5m['low'], df_5m['close'], window=14, smooth_window=3)
    stoch_k_series = stoch.stoch()  # 시리즈 형태 유지
    stoch_d_series = stoch.stoch_signal()  # 시리즈 형태 유지

    # ✅ 최근 값과 이전 값 가져오기
    if len(stoch_k_series) >= 2:  # 데이터 개수 확인
        stoch_k = stoch_k_series.iloc[-1]
        stoch_k_prev = stoch_k_series.iloc[-2]  # 🔥 이전 값을 가져오도록 수정
        stoch_d = stoch_d_series.iloc[-1]
    else:
        logger.warning("⚠️ Stochastic Oscillator 데이터 부족으로 계산 불가")
        return {"signal": "", "message": "스토캐스틱 데이터 부족"}

    if stoch_k > 20 and (stoch_k - stoch_d) > 10 and stoch_k > stoch_k_prev:
        return {"signal": "buy", "message": "스토캐스틱 과매도 반등 매수"}

    # 📌 **매수 조건**
    if position == 0:
        # ✅ 매수 후 최소 5분(300초) 대기
        if trading_context.last_buy_time and (datetime.now() - trading_context.last_buy_time).total_seconds() < 300:
            logger.warning(f"⛔ {ticker} 최근 매수 후 5분 미만 경과 → 매수 금지")
            return {"signal": "", "message": "최근 매수 후 5분 미만 경과 → 매수 금지"}

        # 🔥 동일 가격대에서 매수 반복 방지 (단, 3% 이상 조정되면 가능)
        if buy_price is not None and abs(latest_close - buy_price) < (buy_price * 0.03):
            logger.warning(f"⛔ {ticker} 동일 가격대에서 매수 반복 방지 → 매수 취소 (최근 매수가: {buy_price}, 현재가: {latest_close})")
            return {"signal": "", "message": "동일 가격대에서 매수 반복 방지"}



        # ✅ 손절 횟수에 따라 투자 비율 조정
        investment_ratio = max(0.1, 1.0 - (trading_context.consecutive_losses * 0.1))
        logger.info(f"📉 {ticker} 투자 비율 조정: {investment_ratio * 100:.1f}% (손절 횟수: {trading_context.consecutive_losses})")

        # ✅ 손절 5번 이상이면 RSI 25 이하 & MACD 골든크로스가 발생해야만 매수 가능
        if trading_context.consecutive_losses >= 5:
            if rsi_5m < 25 and macd_5m > 0.1 and macd_15m > 0 and volume_spike:
                logger.warning(f"⛔ {ticker} 연속 손절 {trading_context.consecutive_losses}번 → RSI 25 이하 & MACD 골든크로스 필요")
                return {"signal": "buy", "message": "RSI 과매도 + MACD 상승 + 거래량 급증 매수"}

        # ✅ 손절 7번 이상이면 거래량 급증도 필요
        if trading_context.consecutive_losses >= 7:
            if not volume_spike:
                logger.warning(f"⛔ {ticker} 연속 손절 {trading_context.consecutive_losses}번 → 추가적으로 거래량 급증 필요")
                return {"signal": "", "message": "연속 손절 7번 초과 → 거래량 급증 필요"}

        # ✅ 최종 매수 조건 (5분봉 + 15분봉)
        if (is_bullish and
            macd_5m > 0 and macd_15m > 0 and
            rsi_5m > 50 and rsi_15m > 50 and
            latest_close > bb_lower_5m.iloc[-1] and volume_spike and
            df_5m['EMA5'].iloc[-1] > df_5m['EMA15'].iloc[-1] and
            stoch_k > stoch_d and
            macd_long_histogram > 0):
            logger.info(f"✅ {ticker} 상승장 매수 조건 충족")
            trading_context.last_buy_time = datetime.now()
            return {"signal": "buy", "message": "5분봉 + 15분봉 상승 신호"}

        if is_bearish and rsi_5m < 30 and latest_close > recent_low and stoch_k < 20:
            logger.info(f"✅ {ticker} 하락장 반등 매수 신호 트리거 - RSI: {rsi_5m}, 최저가: {recent_low}, Stoch_K: {stoch_k}")
            trading_context.last_buy_time = datetime.now()
            return {"signal": "buy", "message": "하락장 반등 매수"}

        if latest_close <= bb_lower_5m.iloc[-1] and rsi_5m < 35:
            logger.info(f"✅ {ticker} 볼린저 밴드 하단 반등 매수 - 현재가: {latest_close}, 볼밴 하단: {bb_lower_5m}, RSI: {rsi_5m}")
            trading_context.last_buy_time = datetime.now()
            return {"signal": "buy", "message": "볼린저 밴드 하단 반등 매수"}

        # ✅ 연속 손절 후 RSI 25 이하 & MACD 상승 골든크로스 시 강제 매수
        if trading_context.consecutive_losses >= 3 and rsi_5m < 25 and macd_5m > 0:
            logger.info(f"🔥 {ticker} RSI 과매도 + MACD 골든크로스 → 강제 매수")
            trading_context.last_buy_time = datetime.now()
            return {"signal": "buy", "message": "RSI 과매도 + MACD 반등 강제 매수"}

        return {"signal": "", "message": "매수 조건 미충족"}

    # 📌 **매도 조건**
    if position == 1 and buy_price is not None:
        buy_price = buy_price or df_5m['close'].iloc[-1]  # 현재가를 대체값으로 설정

        # ✅ 손절 및 익절 가격 계산
        stop_loss, take_profit = calculate_stop_loss_take_profit(buy_price, atr, fee_rate)

        # ✅ 실질 손익 계산
        net_profit = (latest_close * (1 - fee_rate)) - (buy_price * (1 + fee_rate))

        logger.info(f"📊 {ticker} 매도 전략 - 손절가: {stop_loss:.2f}, 익절가: {take_profit:.2f}, 실질 손익: {net_profit:.2f}원")

        # ✅ 익절 실행
        if latest_close >= take_profit and net_profit > 0:
            trading_context.consecutive_losses = max(0, trading_context.consecutive_losses - 2)
            logger.info(f"✅ {ticker} 익절 발생 → 손절 횟수 2단계 감소 (현재 손절 횟수: {trading_context.consecutive_losses})")
            return {
                "signal": "sell",
                "message": f"익절 실행 (손절 횟수: {trading_context.consecutive_losses})",
                "stop_loss": stop_loss,
                "take_profit": take_profit,
            }

        # ✅ 손절 실행
        if latest_close < stop_loss:
            trading_context.consecutive_losses += 1
            trading_context.last_sell_time = datetime.now()
            logger.info(f"❌ {ticker} 손절 실행 (손절가: {stop_loss:.2f}원, 실제 손익: {net_profit:.2f}원)")
            return {
                "signal": "sell",
                "message": f"손절 실행 (손절가: {stop_loss:.2f}원, 실제 손익: {net_profit:.2f}원)",
                "stop_loss": stop_loss,
                "take_profit": take_profit
            }

        return {"signal": "", "message": "매매 조건 미충족"}
