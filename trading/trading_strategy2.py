## 주식 매매법
import pandas as pd
import logging
from datetime import datetime
from typing import Optional
from ta.trend import MACD, EMAIndicator, ADXIndicator
from ta.momentum import RSIIndicator
from ta.volatility import BollingerBands, AverageTrueRange
from ta.momentum import StochasticOscillator
from ta.volume import OnBalanceVolumeIndicator

from trading.trade import calculate_stop_loss_take_profit

logger = logging.getLogger(__name__)

class TradingContext:
    def __init__(self):
        self.last_sell_time = {}  # ✅ 코인별 마지막 손절 시간 저장
        self.consecutive_losses = {}  # ✅ 코인별 손절 횟수 저장
        self.last_buy_time = {}  # ✅ 코인별 매수 시간 저장

    def update_loss(self, ticker: str):
        """ 특정 코인의 손절 횟수 증가 및 마지막 손절 시간 저장 """
        self.consecutive_losses[ticker] = self.consecutive_losses.get(ticker, 0) + 1
        self.last_sell_time[ticker] = datetime.now()

    def reset_loss(self, ticker: str):
        """ 특정 코인의 손절 횟수 초기화 """
        self.consecutive_losses[ticker] = 0
        self.last_sell_time.pop(ticker, None)  # 마지막 손절 시간 삭제

trading_context = TradingContext()  # 공유 인스턴스

def trading_strategy(df_5m: pd.DataFrame, df_15m: pd.DataFrame, df_orderbook: pd.DataFrame,
    position: int, ticker: str,
    buy_price: Optional[float] = None, fee_rate: float = 0.0005,
) -> dict:
    """📌 5분봉 + 15분봉을 활용한 단타 트레이딩 전략"""

    # 🔹 매매 전략 실행 로그
    logger.info(f"📊 {ticker} 매매 전략 시작 - 보유 여부: {position}, 현재가: {df_5m['close'].iloc[-1]}, 매수가: {buy_price}")

    # ✅ **데이터 정리 (결측치 처리)**
    df_5m = df_5m.copy().ffill().dropna()
    df_15m = df_15m.copy().ffill().dropna()
    df_orderbook = df_orderbook.copy().ffill().dropna()

    # 🔥 데이터 유효성 검사 (5분봉 & 15분봉)
    if df_5m.empty or len(df_5m) < 200 or df_15m.empty or len(df_15m) < 100:
        return {"signal": "", "message": "데이터 부족"}

    # ✅ MACD 계산 (5분봉)
    macd_5m = MACD(df_5m['close'], window_slow=12, window_fast=26, window_sign=9)
    macd_series = macd_5m.macd()  # MACD 시리즈 캐싱
    macd_signal_series = macd_5m.macd_signal()  # MACD 신호선 시리즈 캐싱
    macd_diff_series = macd_5m.macd_diff()  # MACD 히스토그램 시리즈 캐싱

    macd_5m_value = macd_series.iloc[-1]  # 최신 MACD 값
    macd_slope = macd_5m_value - macd_series.iloc[-2]  # MACD 기울기
    macd_5m_diff = macd_diff_series.iloc[-1]  # MACD 오실레이터
    macd_histogram = macd_5m_diff - macd_signal_series.iloc[-1]  # MACD 히스토그램

    # ✅ ADX(추세 강도) 추가
    adx_5m = ADXIndicator(df_5m['high'], df_5m['low'], df_5m['close'], window=14).adx().iloc[-1]

    if macd_5m_diff < 0 or macd_slope < 0 or adx_5m < 20 or macd_histogram < 0:
        return {"signal": "", "message": "추세 미약, 매매 보류"}

    # 🔥 MACD 히스토그램 증가 여부 체크 추가
    macd_histogram_prev = macd_diff_series.iloc[-2] - macd_signal_series.iloc[-2]
    if macd_histogram > macd_histogram_prev:
        logger.info(f"📈 {ticker} MACD 히스토그램 증가 확인 → 매수 신호 가능성 상승")


    # ✅ 🔥 **장기 MACD 추가 (50, 200 기준)**
    macd_long = MACD(df_5m['close'], window_slow=200, window_fast=50, window_sign=9)
    macd_long_diff_series = macd_long.macd_diff().fillna(0)  # ✅ 한 번만 호출

    macd_long_histogram = macd_long_diff_series.iloc[-1]  # 최신 MACD 히스토그램 값
    macd_long_histogram_prev = macd_long_diff_series.iloc[-2]  # 이전 MACD 히스토그램 값
    macd_long_slope = macd_long_diff_series.diff().fillna(0).iloc[-1]  # MACD 히스토그램 기울기

    # MACD가 음수에서 양수로 변하면 골든크로스 발생!
    if macd_long_histogram_prev < 0 < macd_long_histogram and macd_long_slope > 0:
        logger.info(f"🔥 {ticker} 장기 MACD 골든크로스 발생! (기울기: {macd_long_slope:.4f})")

    # ✅ RSI 계산 (5분봉 + 15분봉)
    rsi_5m = RSIIndicator(df_5m['close'], window=14).rsi().fillna(50).iloc[-1]

    # ✅ 거래량 분석 (OBV 추가)
    obv_series = OnBalanceVolumeIndicator(df_5m['close'], df_5m['volume']).on_balance_volume()
    obv_5m = obv_series.iloc[-1]

    # ✅ 거래량 급증 여부 확인
    avg_volume_5m = df_5m['volume'].rolling(5, min_periods=1).mean().iloc[-1]
    volume_spike = (df_5m['volume'].iloc[-1] > avg_volume_5m * 1.3) and (obv_5m > obv_series.iloc[-2])

    # ✅ 볼린저 밴드 (5분봉)
    bb_indicator = BollingerBands(df_5m['close'], window=20)
    bb_lower_5m = bb_indicator.bollinger_lband().fillna(df_5m['close'].iloc[-1])  # NaN 방지

    latest_close = df_5m['close'].iloc[-1]

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
        atr_multiplier = max(1, min(2, atr / df_5m['close'].iloc[-1] * 100))  # 최소 1, 최대 2 배수로 제한
        base_limit = 1800 + (trading_context.consecutive_losses.get(ticker, 0) - 3) * 600
        limit_time = min(max(base_limit * atr_multiplier, 1800), 7200)  # 최소 30분, 최대 2시간 제한

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

    if stoch_k > 20 and (stoch_k - stoch_d) > 10 and stoch_k > stoch_k_prev:
        return {"signal": "buy", "message": "스토캐스틱 과매도 반등 매수"}

    # 📌 **매수 조건**
    if position == 0:
        # ✅ 매수 후 최소 5분(300초) 대기
        if (last_buy_time := trading_context.last_buy_time.get(ticker)) and (datetime.now() - last_buy_time).total_seconds() < 300:
            logger.warning(f"⛔ {ticker} 최근 매수 후 5분 미만 경과 → 매수 금지")
            return {"signal": "", "message": "최근 매수 후 5분 미만 경과 → 매수 금지"}

        # 🔥 동일 가격대에서 매수 반복 방지 (단, 3% 이상 조정되면 가능)
        if buy_price is None:
            buy_price = latest_close  # 기본값으로 현재가를 사용

        if abs(latest_close - buy_price) < (buy_price * 0.03):
            logger.warning(f"⛔ {ticker} 동일 가격대에서 매수 반복 방지 → 매수 취소 (최근 매수가: {buy_price}, 현재가: {latest_close})")
            return {"signal": "", "message": "동일 가격대에서 매수 반복 방지"}



        # ✅ 손절 횟수에 따라 투자 비율 조정
        investment_ratio = max(0.1, 1.0 - (trading_context.consecutive_losses.get(ticker, 0) * 0.1))
        logger.info(f"📉 {ticker} 투자 비율 조정: {investment_ratio * 100:.1f}% (손절 횟수: {trading_context.consecutive_losses})")

        # ✅ 손절 5번 이상이면 RSI 25 이하 & MACD 골든크로스가 발생해야만 매수 가능
        if rsi_5m < 25 and macd_5m_value > 0.1 and volume_spike:
            logger.warning(f"⛔ {ticker} 연속 손절 {trading_context.consecutive_losses.get(ticker, 0)}번 → RSI 25 이하 & MACD 골든크로스 필요")
            return {"signal": "buy", "message": "RSI 과매도 + MACD 상승 + 거래량 급증 매수"}

        # ✅ 손절 7번 이상이면 거래량 급증도 필요
        if trading_context.consecutive_losses.get(ticker, 0) >= 7:
            if not volume_spike:  # ✅ 거래량 급증이 없으면 매수 금지
                logger.warning(f"⛔ {ticker} 연속 손절 {trading_context.consecutive_losses.get(ticker, 0)}번 → 추가적으로 거래량 급증 필요")
                return {"signal": "", "message": "연속 손절 7번 초과 → 거래량 급증 필요"}

        # ✅ 최종 매수 조건 (5분봉 + 15분봉)
        if (is_bullish and
            macd_5m.macd().iloc[-1] > 0 and
            rsi_5m > 50 and
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

        bb_lower_5m_value = bb_lower_5m.iloc[-1] if not pd.isna(bb_lower_5m.iloc[-1]) else latest_close
        if latest_close <= bb_lower_5m_value and rsi_5m < 35:
            logger.info(f"✅ {ticker} 볼린저 밴드 하단 반등 매수 - 현재가: {latest_close}, 볼밴 하단: {bb_lower_5m}, RSI: {rsi_5m}")
            trading_context.last_buy_time = datetime.now()
            return {"signal": "buy", "message": "볼린저 밴드 하단 반등 매수"}

        # ✅ 연속 손절 후 RSI 25 이하 & MACD 상승 골든크로스 시 강제 매수
        if trading_context.consecutive_losses.get(ticker, 0) > 3 and rsi_5m < 25 and macd_5m_value > 0:
            logger.info(f"🔥 {ticker} RSI 과매도 + MACD 골든크로스 → 강제 매수")
            trading_context.last_buy_time = datetime.now()
            return {"signal": "buy", "message": "RSI 과매도 + MACD 반등 강제 매수"}

        return {"signal": "", "message": "매수 조건 미충족"}

    # 📌 **매도 조건**
    if position == 1 and buy_price is not None:
        latest_close = df_5m['close'].iloc[-1] if not df_5m.empty else 0
        buy_price = buy_price if buy_price is not None else latest_close

        # ✅ 손절 및 익절 가격 계산
        stop_loss, take_profit = calculate_stop_loss_take_profit(buy_price, atr, fee_rate)

        # ✅ 실질 손익 계산
        net_profit = (latest_close * (1 - fee_rate)) - (buy_price * (1 + fee_rate))

        # ✅ 체결강도 확인 (급등 가능성 판단)
        sell_volume_sum = df_orderbook['sell_volume'].sum()
        buy_volume_sum = df_orderbook['buy_volume'].sum()
        orderbook_strength = buy_volume_sum / (sell_volume_sum + 1e-9)  # ✅ 0 나누기 방지
        sell_wall_now, sell_wall_prev = df_orderbook['sell_wall'].iloc[-1], df_orderbook['sell_wall'].iloc[-2]
        sell_wall_reduction = sell_wall_now < sell_wall_prev * 0.9  # 10% 이상 감소해야 인정

        # ✅ 볼린저 밴드 상단 돌파 확인
        bb_indicator = BollingerBands(df_5m['close'], window=20)
        bb_upper_5m = bb_indicator.bollinger_hband().iloc[-1]

        logger.info(f"📊 {ticker} 매도 전략 - 손절가: {stop_loss:.2f}, 익절가: {take_profit:.2f}, 실질 손익: {net_profit:.2f}원")

        # ✅ +1% 도달 시 매도 **(단, 체결강도가 높다면 보류)**
        if latest_close >= take_profit:
            # 📌 체결강도가 높고 매도벽이 줄어들며 캔들 몸통이 연속 상승하는 경우 → 익절 보류
            if (orderbook_strength > 1.5  # 체결강도 상승
                and sell_wall_reduction  # 매도벽 감소
                and df_5m['close'].iloc[-1] > df_5m['open'].iloc[-1]  # 현재 캔들 상승
                and df_5m['close'].iloc[-2] > df_5m['open'].iloc[-2]  # 이전 캔들 상승
                and latest_close > bb_upper_5m  # 볼린저 밴드 상단 돌파
            ):
                logger.info(f"🚀 {ticker} 강한 상승세 감지 → 익절 보류 (체결강도: {orderbook_strength:.2f})")
                return {"signal": "", "message": "급등 가능성 높음 → 익절 보류"}

            logger.info(f"✅ {ticker} +1% 수익 도달! 익절 실행")
            trading_context.consecutive_losses[ticker] = max(0, trading_context.consecutive_losses.get(ticker, 0) - 2)
            return {
                "signal": "sell",
                "message": f"+1% 익절 (현재가: {latest_close:.2f})",
                "stop_loss": stop_loss,
                "take_profit": take_profit,
            }

        # ✅ 급락 가능성 감지 후 즉시 익절 (단, 손실일 때는 적용 안 함)
        sell_spike = df_orderbook['sell_volume'].iloc[-5:].mean() > df_orderbook['sell_volume'].mean() * 3 if df_orderbook['sell_volume'].mean() > 0 else False
        sudden_drop = orderbook_strength.fillna(1) < 0.7  # NaN이면 1로 처리하여 sudden_drop = False

        rsi_series_5m = RSIIndicator(df_5m['close'], window=14).rsi()
        rsi_5m_sudden_drop = (
            len(rsi_series_5m) >= 2
            and rsi_series_5m.iloc[-1] < 40
            and rsi_series_5m.iloc[-1] < rsi_series_5m.iloc[-2] - 5
        )

        if (sell_spike or sudden_drop or rsi_5m_sudden_drop) and net_profit > buy_price * 0.001:  # 최소 0.1% 이상 수익 유지
            logger.warning(f"🚨 {ticker} 급락 가능성 감지 → 즉시 익절")
            return {
                "signal": "sell",
                "message": "급락 가능성 감지 → 즉시 익절",
                "stop_loss": stop_loss,
                "take_profit": take_profit
            }

        # ✅ **손절 시점 최적화**
        atr = atr or (df_5m['close'].diff().abs().rolling(10).mean().iloc[-1] if len(df_5m) >= 10 else 10)
        atr_threshold, max_loss_allowed = atr * 1.5, max(buy_price * 0.01, atr * 2)

        # ✅ 손절 체크 로그 추가 (디버깅용)
        logger.debug(f"📌 {ticker} 손절 체크 - 현재가: {latest_close}, 손절가: {stop_loss}, 손실 횟수: {trading_context.consecutive_losses}")

        if latest_close < stop_loss and (abs(latest_close - buy_price) > max_loss_allowed or abs(latest_close - buy_price) > atr_threshold):
            trading_context.update_loss(ticker)
            losses = trading_context.consecutive_losses.get(ticker, 0)
            logger.warning(f"🚨 {ticker} 손절 발생! (손절가: {stop_loss:.2f}원, 손실횟수: {losses})")

            return {
                "signal": "sell",
                "message": f"손절 실행 (손절가: {stop_loss:.2f}원, 실제 손익: {net_profit:.2f}원)",
                "stop_loss": stop_loss,
                "take_profit": take_profit
            }

        return {"signal": "", "message": "매매 조건 미충족"}
