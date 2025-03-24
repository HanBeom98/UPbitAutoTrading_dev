import logging
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
from ta.momentum import RSIIndicator
from ta.momentum import StochasticOscillator
from ta.trend import MACD, EMAIndicator, ADXIndicator
from ta.volatility import BollingerBands, AverageTrueRange
from utils.balance_util import get_total_balance

from trading.trade import calculate_stop_loss_take_profit, \
    calculate_fixed_take_profit

logger = logging.getLogger(__name__)

class TradingContext:
    def __init__(self):
        self.last_sell_time = {}  # ✅ 코인별 마지막 손절 시간 저장
        self.consecutive_losses = {}  # ✅ 코인별 손절 횟수 저장
        self.last_buy_time = {}  # ✅ 코인별 매수 시간 저장
        self.peak_price_since_buy = {}  # ✅ 최고가 저장용 추가
        self.last_partial_sell_time = {}  # ✅ 부분 익절 시간 저장
        self.partial_sell_count = {}  # ✅ 부분 익절 횟수
        self.total_start_balance = get_total_balance()  # ✅ 자산 기준점 저장
        self.realized_profit = 0.0
        self.daily_profit = 0.0

    def update_loss(self, ticker: str):
        """ 특정 코인의 손절 횟수 증가 및 마지막 손절 시간 저장 """
        self.consecutive_losses[ticker] = self.consecutive_losses.get(ticker, 0) + 1
        self.last_sell_time[ticker] = datetime.now()
        logger.warning(f"❌ [손절] {ticker} 손절 횟수 증가 → 현재 손절 카운트: {self.consecutive_losses[ticker]}")


    def reset_loss(self, ticker: str):
        """ 특정 코인의 손절 횟수 초기화 """
        self.consecutive_losses[ticker] = 0
        self.last_sell_time.pop(ticker, None)  # 마지막 손절 시간 삭제

trading_context = TradingContext()  # 공유 인스턴스

def trading_strategy(df_1m: pd.DataFrame,df_5m: pd.DataFrame, df_15m: pd.DataFrame, df_orderbook: pd.DataFrame,
                    position: int, ticker: str,
                    buy_price: Optional[float] = None, fee_rate: float = 0.0005,
                    ) -> dict:
    """📌 5분봉 + 15분봉을 활용한 단타 트레이딩 전략"""

    # 🔹 매매 전략 실행 로그
    logger.info(f"📊 {ticker} 매매 전략 시작 - 보유 여부: {position}, 현재가: {df_5m['close'].iloc[-1]}, 매수가: {buy_price}")

    # ✅ **데이터 정리 (결측치 처리)**
    df_1m = df_1m.copy().ffill().dropna()
    df_5m = df_5m.copy().ffill().dropna()
    df_15m = df_15m.copy().ffill().dropna()
    df_orderbook = df_orderbook.copy().ffill().dropna()

    #  🔥 데이터 유효성 검사 (1분봉)
    if df_1m.empty or len(df_1m) < max(14, 5):
        return {"signal": "", "message": "1분봉 데이터 부족"}

    # 🔥 데이터 유효성 검사 (5분봉 & 15분봉)
    if df_5m.empty or len(df_5m) < 200 or df_15m.empty or len(df_15m) < 100:
        return {"signal": "", "message": "데이터 부족"}

    # ✅ **체결 강도 계산
    sell_volume_sum = df_orderbook['sell_volume'].sum()
    buy_volume_sum = df_orderbook['buy_volume'].sum()
    orderbook_strength = buy_volume_sum / (sell_volume_sum + 1e-9)  # 🔥 체결강도 활용
    orderbook_strength = orderbook_strength if not np.isnan(orderbook_strength) else 1

    # ✅ 1분봉 RSI 급락 감지
    rsi_1m_series = RSIIndicator(df_1m['close'], window=14).rsi().fillna(50)
    rsi_1m_drop = (
        len(rsi_1m_series) >= 2
        and rsi_1m_series.iloc[-1] < 35
        and rsi_1m_series.iloc[-1] < rsi_1m_series.iloc[-2] - 5
    )

    # ✅ 1분봉 기준 최근 5개 캔들 중 최저가 갱신
    lowest_1m = df_1m['low'].rolling(window=5).min().iloc[-1]
    current_price = df_1m['close'].iloc[-1]
    is_breaking_1m_support = current_price < lowest_1m

    # ✅ 거래량 급증 여부 (체결 강도 기반으로 통합)
    volume_spike = orderbook_strength > 1.5  # 🔥 체결 강도가 급등하면 매수 신호 강화

    # ✅ MACD 계산 (5분봉)
    macd_5m = MACD(df_5m['close'], window_slow=12, window_fast=26, window_sign=9)
    macd_series = macd_5m.macd()  # MACD 시리즈 캐싱
    macd_signal_series = macd_5m.macd_signal()  # MACD 신호선 시리즈 캐싱
    macd_diff_series = macd_5m.macd_diff()  # MACD 히스토그램 시리즈 캐싱

    macd_5m_value = macd_series.iloc[-1]  # 최신 MACD 값
    macd_slope = macd_5m_value - macd_series.iloc[-2]  # MACD 기울기
    macd_5m_diff = macd_diff_series.iloc[-1]  # MACD 오실레이터
    macd_histogram = macd_5m_diff - macd_signal_series.iloc[-1]  # MACD 히스토그램

    # 🔥 MACD 히스토그램 증가 여부 체크 추가
    if macd_5m_value > 0 and macd_5m_diff > 0:
        logger.info(f"📈 {ticker} MACD 상승 확인 → 매수 신호 가능성 상승")

    # ✅ ADX(추세 강도) 계산
    adx_5m = ADXIndicator(df_5m['high'], df_5m['low'], df_5m['close'], window=14).adx().iloc[-1]

    # ✅ RSI 계산 (5분봉 + 15분봉)
    rsi_5m = RSIIndicator(df_5m['close'], window=14).rsi().fillna(50).iloc[-1]

    # ✅ 볼린저 밴드 (5분봉)
    bb_indicator = BollingerBands(df_5m['close'], window=20)
    bb_lower_5m = bb_indicator.bollinger_lband().fillna(df_5m['close'].iloc[-1])  # NaN 방지

    latest_close = df_5m['close'].iloc[-1] if not df_5m.empty else 0

    # ✅ Stochastic Oscillator 계산 (5분봉 기준)
    stoch = StochasticOscillator(df_5m['high'], df_5m['low'], df_5m['close'], window=14, smooth_window=3)
    stoch_k_series = stoch.stoch()  # 시리즈 형태 유지
    stoch_d_series = stoch.stoch_signal()  # 시리즈 형태 유지

    # ✅ 최근 값과 이전 값 가져오기
    if len(stoch_k_series) < 2 or len(stoch_d_series) < 1:
        logger.warning("⚠️ Stochastic Oscillator 데이터 부족으로 계산 불가")
        return {"signal": "", "message": "스토캐스틱 데이터 부족"}

    stoch_k = stoch_k_series.iloc[-1]
    stoch_k_prev = stoch_k_series.iloc[-2]
    stoch_d = stoch_d_series.iloc[-1]

    # ✅ ADX < 20일 때 예외적으로 매수할 수 있는 조건 추가
    allow_trade = (
        (latest_close <= bb_lower_5m.iloc[-1] and volume_spike)  # 🔥 볼린저 밴드 반등 + 체결강도 급등
        or (stoch_k > 20 and (stoch_k - stoch_d) > 10 and stoch_k > stoch_k_prev and volume_spike)  # 🔥 스토캐스틱 반등 + 체결강도 급등
        or (rsi_5m < 25 and macd_5m_value > 0 and trading_context.consecutive_losses.get(ticker, 0) > 3)  # 🔥 연속 손절 후 RSI 25 이하 & MACD 상승
        or (adx_5m > 25 and macd_5m_value > 0)  # ✅ ADX 25 이상 & MACD 상승 → 추가 매수 조건
    )

    # ✅ 기존의 "추세 미약" 조건에 예외 처리 추가
    if (macd_5m_diff < 0 or macd_slope < 0 or macd_histogram < 0) and not allow_trade:
        return {"signal": "", "message": "추세 미약, 매매 보류"}

    # ✅ 하락장에서 반등할 가능성 체크 (히스토그램이 증가하는 경우)
    macd_histogram_prev = macd_diff_series.iloc[-2] - macd_signal_series.iloc[-2]
    if macd_histogram > macd_histogram_prev:
        logger.info(f"📈 {ticker} MACD 히스토그램 증가 확인 → 반등 가능성 상승")

    # ✅ 🔥 **장기 MACD 추가 (50, 200 기준)**
    macd_long = MACD(df_5m['close'], window_slow=200, window_fast=50, window_sign=9)
    macd_long_diff_series = macd_long.macd_diff().fillna(0)  # ✅ 한 번만 호출

    macd_long_histogram = macd_long_diff_series.iloc[-1]  # 최신 MACD 히스토그램 값
    macd_long_histogram_prev = macd_long_diff_series.iloc[-2]  # 이전 MACD 히스토그램 값
    macd_long_slope = macd_long_diff_series.diff().fillna(0).iloc[-1]  # MACD 히스토그램 기울기

    # MACD가 음수에서 양수로 변하면 골든크로스 발생!
    if macd_long_histogram_prev < 0 < macd_long_histogram and macd_long_slope > 0:
        logger.info(f"🔥 {ticker} 장기 MACD 골든크로스 발생! (기울기: {macd_long_slope:.4f})")

    # 🔥 캔들 강도 추가 (양봉 개수 체크)
    bullish_candles = (df_5m['close'].iloc[-3:] > df_5m['open'].iloc[-3:]).sum()

    # ✅ 볼린저 밴드 하단 터치 후, 3개 캔들 연속 상승 시 매수
    if (
        df_5m['close'].iloc[-3] < bb_lower_5m.iloc[-3] and
        df_5m['close'].iloc[-3] < df_5m['close'].iloc[-2] < latest_close and
        bullish_candles >= 2 and  # 🔥 캔들 강도 조건 추가
        volume_spike  # 🔥 거래량 급증 확인 추가
    ):
        return {"signal": "buy", "message": "볼린저 밴드 강한 반등 확인"}

    # ✅ ATR 계산 (5분봉)
    atr = AverageTrueRange(df_5m['high'], df_5m['low'], df_5m['close'], window=14).average_true_range().iloc[-1]

    # ✅ EMA 계산 (5분봉)
    df_5m['EMA5'], df_5m['EMA15'] = (
        EMAIndicator(df_5m['close'], window=5).ema_indicator().fillna(df_5m['close']),
        EMAIndicator(df_5m['close'], window=15).ema_indicator().fillna(df_5m['close'])
    )

    recent_low = df_5m['close'].rolling(window=10).min().iloc[-1]

    is_bullish = df_5m['EMA5'].iloc[-1] > df_5m['EMA15'].iloc[-1]
    is_bearish = df_5m['EMA5'].iloc[-1] < df_5m['EMA15'].iloc[-1]

    last_sell_time = trading_context.last_sell_time.get(ticker)  # None일 경우 대비
    if last_sell_time:
        time_since_last_sell = (datetime.now() - trading_context.last_sell_time[ticker]).total_seconds()
        limit_time = min(max(180, atr * 30), 600)  # 수정: 3분 ~ 10분

        if time_since_last_sell < limit_time:
            logger.warning(f"⛔ {ticker} 최근 손절 {trading_context.consecutive_losses.get(ticker, 0)}번 → {limit_time // 60}분 동안 매수 금지")
            return {"signal": "", "message": f"손절 {trading_context.consecutive_losses.get(ticker, 0)}번 → {limit_time // 60}분 동안 매수 금지"}

        # 🔥 30분 경과 후 손절 횟수 점진적 감소
        if time_since_last_sell >= limit_time:
            trading_context.reset_loss(ticker)  # ✅ 완전히 초기화하도록 변경
            logger.info(f"✅ {ticker} 손절 제한 시간 종료 → 손절 횟수 초기화됨")

    # ✅ 손절 기록이 없는데 손절 횟수가 증가한 경우 초기화
    elif trading_context.consecutive_losses.get(ticker, 0) > 0 and ticker not in trading_context.last_sell_time:
        logger.warning(f"⚠️ {ticker} 손절 기록 없음 → 손절 횟수 초기화")
        trading_context.consecutive_losses[ticker] = 0  # ✅ 특정 코인만 초기화


    if stoch_k > 20 and (stoch_k - stoch_d) > 10 and stoch_k > stoch_k_prev:
        return {"signal": "buy", "message": "스토캐스틱 과매도 반등 매수"}

    # 📌 **매수 조건**
    if position == 0:
        # ✅ 매수 후 최소 5분(300초) 대기
        last_buy_time = trading_context.last_buy_time.get(ticker, None)
        if isinstance(last_buy_time, datetime) and (datetime.now() - last_buy_time).total_seconds() < 300:
            logger.warning(f"⛔ {ticker} 최근 매수 후 5분 미만 경과 → 매수 금지")
            return {"signal": "", "message": "최근 매수 후 5분 미만 경과 → 매수 금지"}

        # 🔥 동일 가격대에서 매수 반복 방지 (단, 1.5% 이상 조정되면 가능)
        if buy_price is None:
            buy_price = latest_close  # 기본값으로 현재가를 사용

        # ✅ 재매수 제한 조건: 손절 기록이 없고 이전 매수가격(buy_price)이 존재할 경우에만 체크
        if ticker not in trading_context.last_sell_time and buy_price and latest_close > buy_price * 0.985:
            # ✅ 기본적으로 1.5% 이상 하락해야 매수 가능
            if (latest_close - buy_price) / buy_price < -0.015:
                # 🔥 추가적인 강한 매수 신호가 있으면 매수 허용
                if rsi_5m < 30 and macd_5m_value > 0 and volume_spike:
                    return {"signal": "buy", "message": "RSI 과매도 + 거래량 급증 → 예외적 재매수 허용"}
            logger.warning(
                f"⛔ {ticker} 평단가 대비 충분히 하락하지 않음 → 매수 보류 "
                f"(현재가: {latest_close:.2f}, 평단가: {buy_price:.2f})"
            )
            return {"signal": "", "message": "평단가 대비 1.5% 미만 하락 → 매수 보류"}

        trading_context.partial_sell_count[ticker] = 0

        # ✅ 손절 횟수에 따라 투자 비율 조정
        investment_ratio = max(0.1, 1.0 - (trading_context.consecutive_losses.get(ticker, 0) * 0.1))
        logger.info(f"📉 {ticker} 투자 비율 조정: {investment_ratio * 100:.1f}% (손절 횟수: {trading_context.consecutive_losses})")

        # ✅ 손절 5번 이상이면 RSI 25 이하 & MACD 골든크로스가 발생해야만 매수 가능
        if rsi_5m < 25 and macd_5m_value > 0.1 and volume_spike:
            logger.warning(f"⛔ {ticker} 연속 손절 {trading_context.consecutive_losses.get(ticker, 0)}번 → RSI 25 이하 & MACD 골든크로스 필요")
            trading_context.last_buy_time[ticker] = datetime.now()
            trading_context.peak_price_since_buy[ticker] = latest_close
            return {"signal": "buy", "message": "RSI 과매도 + MACD 상승 + 거래량 급증 매수"}

        # ✅ 손절 7번 이상이면 거래량 급증도 필요
        if trading_context.consecutive_losses.get(ticker, 0) >= 7:
            if not volume_spike:  # ✅ 거래량 급증이 없으면 매수 금지
                logger.warning(f"⛔ {ticker} 연속 손절 {trading_context.consecutive_losses.get(ticker, 0)}번 → 추가적으로 거래량 급증 필요")
                trading_context.last_buy_time[ticker] = datetime.now()
                trading_context.peak_price_since_buy[ticker] = latest_close
                return {"signal": "", "message": "연속 손절 7번 초과 → 거래량 급증 필요"}

        # ✅ 천천히 반등하는 저점 매수 전략 (볼밴 하단 + 약한 체결강도)
        if (
            rsi_5m < 35 and
            latest_close <= bb_lower_5m.iloc[-1] and
            bullish_candles >= 2 and
            orderbook_strength < 1.2  # 체결강도 낮음 → 천천히 반등 중
        ):
            logger.info(f"✅ {ticker} 천천히 반등하는 저점 매수 조건 충족")
            trading_context.last_buy_time[ticker] = datetime.now()
            trading_context.peak_price_since_buy[ticker] = latest_close
            return {"signal": "buy", "message": "📉 천천히 반등하는 저점 매수 조건 충족"}

        # ✅ 최종 매수 조건 (5분봉 + 15분봉)
        if (
            (rsi_5m < 35 and latest_close <= bb_lower_5m.iloc[-1])  # 🔥 RSI 과매도 + 볼밴 하단 반등
            or (orderbook_strength > 1.3 and stoch_k > stoch_d)  # 🔥 체결강도 급등 & 스토캐스틱 반등
            or (is_bullish and df_5m['EMA5'].iloc[-1] > df_5m['EMA15'].iloc[-1] and macd_5m_value > -0.05)  # 🔥 EMA 강세 + MACD 하락 제한
        ):
            logger.info(f"✅ {ticker} 수정된 매수 조건 충족")
            trading_context.last_buy_time[ticker] = datetime.now()
            trading_context.peak_price_since_buy[ticker] = latest_close  # ✅ 매수 직후 최고가 초기화
            return {"signal": "buy", "message": "코인 시장 최적화 매수 신호"}

        if is_bearish and rsi_5m < 30 and latest_close > recent_low and stoch_k < 20:
            logger.info(f"✅ {ticker} 하락장 반등 매수 신호 트리거 - RSI: {rsi_5m}, 최저가: {recent_low}, Stoch_K: {stoch_k}")
            trading_context.last_buy_time[ticker] = datetime.now()
            trading_context.peak_price_since_buy[ticker] = latest_close
            return {"signal": "buy", "message": "하락장 반등 매수"}

        if latest_close <= bb_lower_5m.iloc[-1] and rsi_5m < 35 and volume_spike:
            logger.info(f"✅ {ticker} 볼린저 밴드 하단 반등 매수 - 현재가: {latest_close}, 볼밴 하단: {bb_lower_5m}, RSI: {rsi_5m}")
            trading_context.last_buy_time[ticker] = datetime.now()
            trading_context.peak_price_since_buy[ticker] = latest_close
            return {"signal": "buy", "message": "볼린저 밴드 하단 반등 매수"}

        # ✅ 연속 손절 후 RSI 25 이하 & MACD 상승 골든크로스 시 강제 매수
        if trading_context.consecutive_losses.get(ticker, 0) > 3 and rsi_5m < 25 and macd_5m_value > 0:
            logger.info(f"🔥 {ticker} RSI 과매도 + MACD 골든크로스 → 강제 매수")
            trading_context.last_buy_time[ticker] = datetime.now()
            trading_context.peak_price_since_buy[ticker] = latest_close
            return {"signal": "buy", "message": "RSI 과매도 + MACD 반등 강제 매수"}

        return {"signal": "", "message": "매수 조건 미충족"}

    # 📌 **매도 조건**
    if position == 1 and buy_price is not None:
        latest_close = df_5m['close'].iloc[-1] if not df_5m.empty else 0
        buy_price = buy_price if buy_price is not None else latest_close

        # ✅ 손절 및 익절 가격 계산
        stop_loss, take_profit = calculate_stop_loss_take_profit(buy_price, atr, fee_rate)

        # 고정 1% 익절가 계산 (부분 익절 전용)
        fixed_take_profit = calculate_fixed_take_profit(buy_price, fee_rate)

        # ✅ 실질 손익 계산
        net_profit = (latest_close * (1 - fee_rate)) - (buy_price * (1 + fee_rate))

        # ✅ 체결강도 확인 (급등 가능성 판단)
        sell_wall_now, sell_wall_prev = df_orderbook['sell_wall'].iloc[-1], df_orderbook['sell_wall'].iloc[-2]
        sell_wall_reduction = sell_wall_now < sell_wall_prev * 0.9  # 10% 이상 감소해야 인정

        # ✅ 볼린저 밴드 상단 돌파 확인
        bb_indicator = BollingerBands(df_5m['close'], window=20)
        bb_upper_5m = bb_indicator.bollinger_hband().iloc[-1]

        logger.info(f"📊 {ticker} 매도 전략 - 손절가: {stop_loss:.2f}, 익절가: {take_profit:.2f}, 실질 손익: {net_profit:.2f}원")

        # 🔼 보유 중이라면 최고가 업데이트
        if trading_context.peak_price_since_buy.get(ticker) is not None:
            trading_context.peak_price_since_buy[ticker] = max(trading_context.peak_price_since_buy[ticker], latest_close)
        else:
            trading_context.peak_price_since_buy[ticker] = latest_close  # ✅ 최초 할당

        # ✅ +1% 도달 시 부분 익절
        if latest_close >= fixed_take_profit:
            logger.info(f"📊 {ticker} 현재가: {latest_close}, 평단가: {buy_price}, 익절가: {fixed_take_profit}")

            partial_sell_time = trading_context.last_partial_sell_time.get(ticker)
            if partial_sell_time and (datetime.now() - partial_sell_time).total_seconds() < 180:
                logger.info(f"⏸️ {ticker} 부분 익절 쿨다운 중 → 중복 부분 익절 보류")
                return {"signal": "", "message": "부분 익절 쿨다운 중 → 중복 익절 보류"}

            # ✅ 부분 익절 2회 이상이면 추가 익절 보류
            if trading_context.partial_sell_count.get(ticker, 0) >= 2:
                logger.info(f"⏸️ {ticker} 이미 2회 부분 익절 → 추가 익절 보류")
                return {"signal": "", "message": "부분 익절 2회 초과 → 기다림"}

            trading_context.partial_sell_count[ticker] = trading_context.partial_sell_count.get(ticker, 0) + 1

            # ✅ 손절 횟수 감소
            trading_context.consecutive_losses[ticker] = max(0, trading_context.consecutive_losses.get(ticker, 0) - 2)

            trading_context.last_partial_sell_time[ticker] = datetime.now()

            # ✅ 일부 익절 (50% 매도)
            return {
                "signal": "sell_partial",  # 🔥 일부 익절
                "message": f"+1% 부분 익절 (현재가: {latest_close:.2f})",
                "stop_loss": stop_loss,
                "take_profit": fixed_take_profit,
            }

        # ✅ 3분 쿨다운: 부분 익절 후 180초 동안 전체 매도 방지
        partial_sell_time = trading_context.last_partial_sell_time.get(ticker)
        if partial_sell_time and (datetime.now() - partial_sell_time).total_seconds() < 180:
            logger.info(f"⏸️ {ticker} 부분 익절 후 3분 쿨다운 중 → 트레일링 스탑 매도 보류")
            return {"signal": "", "message": "부분 익절 후 쿨다운 → 트레일링 스탑 보류"}

        # ✅ 트레일링 스탑 로직: 최고가 대비 1.2% 이상 하락하면 익절
        peak_price = trading_context.peak_price_since_buy.get(ticker, latest_close)

        # 🔥 [추가] 트레일링 스탑 적용 전에 손익 계산 (익절인지 확인)
        expected_profit = (latest_close * (1 - fee_rate)) - (buy_price * (1 + fee_rate))

        # 🔥 [수정] 손실이 발생할 경우 트레일링 스탑 실행 안 함
        if peak_price > buy_price * 1.015 and latest_close < peak_price * 0.988:
            if expected_profit > 0:  # ✅ 트레일링 스탑 시 수익이 날 경우에만 실행
                trading_context.last_partial_sell_time.pop(ticker, None)
                trading_context.consecutive_losses[ticker] = max(0, trading_context.consecutive_losses.get(ticker, 0) - 2)  # ✅ 손절 횟수 감소
                trading_context.peak_price_since_buy.pop(ticker, None)  # ✅ 트레일링 스탑 후 최고가 제거
                trading_context.partial_sell_count.pop(ticker, None)
                logger.warning(f"📉 {ticker} 최고가 대비 하락폭 증가 → 트레일링 스탑 익절 (최고가: {peak_price:.2f}, 현재가: {latest_close:.2f})")

                return {
                    "signal": "sell",
                    "message": "트레일링 스탑 익절 (최고가 대비 하락)",
                    "stop_loss": stop_loss,
                    "take_profit": take_profit
                }
            else:
                logger.warning(f"⚠️ {ticker} 트레일링 스탑 조건 충족 BUT 손실 발생 가능 → 매도 보류")

        # ✅ 5분봉 급락 감지 (누락된 sell_spike & sudden_drop 추가)
        sell_spike = df_orderbook['sell_volume'].iloc[-5:].mean() > df_orderbook['sell_volume'].mean() * 3 if df_orderbook['sell_volume'].mean() > 0 else False
        sudden_drop = orderbook_strength < 0.7  # ✅ NaN이면 이미 1로 처리했으므로 fillna() 불필요

        # ✅ 1분봉 급락 감지 (단기 변동 감지)
        if (rsi_1m_drop or is_breaking_1m_support) and net_profit > buy_price * 0.001:
            logger.warning(f"🚨 {ticker} 1분봉 급락 신호 → 부분 익절 (50%)")

            trading_context.peak_price_since_buy.pop(ticker, None)  # ✅ 최고가 제거
            return {
                "signal": "sell_partial",  # 🔥 50% 부분 익절
                "message": "1분봉 급락 감지 → 선제 부분 익절",
                "stop_loss": stop_loss,
                "take_profit": take_profit,
            }

        # ✅ 5분봉 급락 감지 (지속적인 하락 감지)
        if (sell_spike or sudden_drop) and net_profit > buy_price * 0.002:
            logger.warning(f"🚨 {ticker} 5분봉 급락 신호 → 전체 포지션 청산")
            trading_context.partial_sell_count.pop(ticker, None)
            trading_context.peak_price_since_buy.pop(ticker, None)  # ✅ 최고가 제거
            return {
                "signal": "sell",  # 🔥 전량 매도
                "message": "5분봉 급락 감지 → 전체 매도",
                "stop_loss": stop_loss,
                "take_profit": take_profit
            }

        # ✅ 최근 15개 캔들 중 최저가 계산 후 손절
        recent_low_15 = df_5m['low'].rolling(window=15).min().iloc[-1]

        # ✅ 손절 트리거 추가 (최근 15개 캔들 중 최저가 갱신 시 즉시 손절)
        if latest_close < recent_low_15 * 0.99:
            logger.warning(f"🚨 {ticker} 최근 15개 캔들 최저가 {recent_low_15:.4f} 대비 1% 추가 하락 → 현재가: {latest_close:.4f} → 손절 실행")
            trading_context.update_loss(ticker)
            trading_context.last_partial_sell_time.pop(ticker, None)
            trading_context.peak_price_since_buy.pop(ticker, None)
            return {
                "signal": "sell",
                "message": f"최근 15개 캔들 최저가 갱신 손절 (최저가: {recent_low_15:.2f})",
                "stop_loss": stop_loss,
                "take_profit": take_profit
            }

        trading_context.last_partial_sell_time.pop(ticker, None)

        # ✅ **손절 시점 최적화 (ATR 기반 손절)**
        atr = atr or (df_5m['close'].diff().abs().rolling(10).mean().iloc[-1] if len(df_5m) >= 10 else 10)
        atr_threshold, max_loss_allowed = atr * 1.5, max(buy_price * 0.01, atr * 2)

        # ✅ 손절 체크 로그 추가 (디버깅용)
        logger.debug(f"📌 {ticker} 손절 체크 - 현재가: {latest_close}, 손절가: {stop_loss}, 손실 횟수: {trading_context.consecutive_losses}")

        if latest_close < stop_loss and (abs(latest_close - buy_price) > max_loss_allowed or abs(latest_close - buy_price) > atr_threshold):
            trading_context.update_loss(ticker)
            trading_context.peak_price_since_buy.pop(ticker, None)  # ✅ 손절 발생 시 최고가 제거
            trading_context.last_partial_sell_time.pop(ticker, None)
            losses = trading_context.consecutive_losses.get(ticker, 0)
            logger.warning(f"🚨 {ticker} 손절 발생! (손절가: {stop_loss:.2f}원, 손실횟수: {losses})")

            return {
                "signal": "sell",
                "message": f"손절 실행 (손절가: {stop_loss:.2f}원, 실제 손익: {net_profit:.2f}원)",
                "stop_loss": stop_loss,
                "take_profit": take_profit
            }

    return {"signal": "", "message": "매매 조건 미충족"}

def update_realized_profit(order_uuid: str, avg_buy_price: float):
    from trading.trade import get_order_status

    status = get_order_status(order_uuid)
    trades = status.get("trades", [])

    if not trades:
        return

    total_price = sum(float(t["price"]) * float(t["volume"]) for t in trades)
    total_volume = sum(float(t["volume"]) for t in trades)
    avg_sell_price = total_price / total_volume if total_volume > 0 else 0

    profit = (avg_sell_price - avg_buy_price) * total_volume
    trading_context.realized_profit += profit

    print(f"📈 실현 수익 업데이트: +{profit:,.0f}원 | 누적 수익: {trading_context.realized_profit:,.0f}원")
    print(f"📊 기준 자산 대비 수익률: {trading_context.realized_profit / trading_context.total_start_balance * 100:.2f}%")
