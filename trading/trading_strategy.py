import logging
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
from ta.momentum import RSIIndicator, StochasticOscillator
from ta.trend import MACD, ADXIndicator
from ta.volatility import BollingerBands, AverageTrueRange

from account.my_account import get_my_exchange_account
from settings import MAX_TOTAL_INVEST, MAX_INVEST_PER_TICKER_RATIO
from utils.balance_util import get_total_balance
from db.trade_state import load_trade_status, save_trade_status
from trading.trade import calculate_stop_loss_take_profit, calculate_fixed_take_profit, get_current_volume_ratio, get_order_status

logger = logging.getLogger(__name__)

class TradingContext:
    def __init__(self):
        self.last_sell_time = {}
        self.consecutive_losses = {}
        self.last_buy_time = {}
        self.peak_price_since_buy = {}
        self.last_partial_sell_time = {}
        self.partial_sell_count = {}
        self.total_start_balance = get_total_balance()
        self.realized_profit = 0.0
        self.daily_profit = 0.0
        self.avg_buy_price = {}

    def update_loss(self, ticker: str):
        self.consecutive_losses[ticker] = self.consecutive_losses.get(ticker, 0) + 1
        self.last_sell_time[ticker] = datetime.now()
        logger.warning(f"❌ [손절] {ticker} 손절 회수 증가 → 현재 손절 카운트: {self.consecutive_losses[ticker]}")

    def reset_loss(self, ticker: str):
        self.consecutive_losses[ticker] = 0
        self.last_sell_time.pop(ticker, None)

trading_context = TradingContext()

def initialize_context_for_ticker(ticker):
    status = load_trade_status(ticker)
    if status:
        trading_context.consecutive_losses[ticker] = status.consecutive_losses or 0
        trading_context.last_sell_time[ticker] = status.last_sell_time
        trading_context.partial_sell_count[ticker] = status.partial_sell_count or 0
        trading_context.last_partial_sell_time[ticker] = status.last_partial_sell_time

        if status.buy_price:
            trading_context.last_buy_time[ticker] = datetime.now()
            trading_context.avg_buy_price[ticker] = status.buy_price
            logger.info(f"[INIT] {ticker} 매수가 로딩 완료: {status.buy_price:.2f}")
        else:
            logger.info(f"[INIT] {ticker} 매수가 없음 → 기본값 없음")

        if hasattr(status, "peak_price") and status.peak_price:
            trading_context.peak_price_since_buy[ticker] = status.peak_price
            logger.info(f"[INIT] {ticker} 최고가 로딩 완료: {status.peak_price:.2f}")
        elif status.buy_price:
            trading_context.peak_price_since_buy[ticker] = status.buy_price
            logger.info(f"[INIT] {ticker} 최고가 없음 → 매수가로 초기화: {status.buy_price:.2f}")
        else:
            logger.warning(f"[INIT] {ticker} peak_price와 buy_price 모두 없음 → 최고가 설정 안됨")

    else:
        account_data = get_my_exchange_account()
        asset = account_data.get("assets", {}).get(ticker, None)
        if asset:
            balance = float(asset.get("balance", 0))
            avg_price = float(asset.get("avg_buy_price", 0))
            if balance > 0 and avg_price > 0:
                trading_context.last_buy_time[ticker] = datetime.now()
                trading_context.peak_price_since_buy[ticker] = avg_price
                trading_context.avg_buy_price[ticker] = avg_price
                save_trade_status(ticker, buy_price=avg_price, partial_sell_count=0, peak_price=avg_price)
                logger.info(f"[INIT] {ticker} 계좌 기반 초기화 완료 - 잔고: {balance}, 평단가: {avg_price}")
            else:
                logger.info(f"[INIT] {ticker} 잔고 또는 평단가 0 → 초기화 생략")
        else:
            logger.info(f"[INIT] {ticker} 보유하지 않음 → 초기화 생략")

def update_realized_profit(order_uuid: str, avg_buy_price: float):
    try:
        status = get_order_status(order_uuid)
    except Exception as e:
        logger.error(f"❌ 주문 정보 조회 실패 - {order_uuid}: {e}")
        return

    trades = status.get("trades", [])
    if not trades:
        return

    total_price = sum(float(t["price"]) * float(t["volume"]) for t in trades)
    total_volume = sum(float(t["volume"]) for t in trades)
    avg_sell_price = total_price / total_volume if total_volume > 0 else 0

    profit = (avg_sell_price - avg_buy_price) * total_volume
    trading_context.realized_profit += profit
    trading_context.daily_profit += profit

    current_total_balance = get_total_balance()
    total_profit = current_total_balance - trading_context.total_start_balance
    profit_rate = (total_profit / trading_context.total_start_balance) * 100

    logger.info(f"💰 부분 익절 실현 수익: +{profit:,.2f}원 | 누적 수익: {trading_context.realized_profit:,.2f}원")
    print(f"📈 실현 수익: +{profit:,.0f}원 | 누적 수익률: {profit_rate:.2f}%")

def get_partial_sell_ratio(count: int) -> float:
    if count == 0:
        return 0.3
    elif count == 1:
        return 0.2
    elif count == 2:
        return 0.1
    else:
        return 0.1

def trading_strategy(df_1m: pd.DataFrame, df_5m: pd.DataFrame, df_15m: pd.DataFrame, df_orderbook: pd.DataFrame,
    position: int, ticker: str, buy_price: Optional[float] = None, fee_rate: float = 0.0005) -> dict:
    logger.info(f"📊 {ticker} 매매 전략 시작 - 보유 여부: {position}, 현재가: {df_5m['close'].iloc[-1]}, 매수가: {buy_price}")

    df_1m = df_1m.copy().ffill().dropna()
    df_5m = df_5m.copy().ffill().dropna()
    df_15m = df_15m.copy().ffill().dropna()
    df_orderbook = df_orderbook.copy().ffill().dropna()

    if df_1m.empty or len(df_1m) < 14 or len(df_5m) < 200 or len(df_15m) < 100:
        return {"signal": "", "message": "데이터 부족"}

    latest_close = df_5m['close'].iloc[-1]
    orderbook_strength = df_orderbook['buy_volume'].sum() / (df_orderbook['sell_volume'].sum() + 1e-9)
    orderbook_strength = 1 if np.isnan(orderbook_strength) else orderbook_strength

    # ===== 기술 지표 계산 =====
    rsi_5m = RSIIndicator(df_5m['close'], window=14).rsi().fillna(50).iloc[-1]
    rsi_1m = RSIIndicator(df_1m['close'], window=14).rsi().fillna(50)
    bb = BollingerBands(df_5m['close'], window=20)
    bb_lower_5m = bb.bollinger_lband().fillna(latest_close)
    stoch = StochasticOscillator(df_5m['high'], df_5m['low'], df_5m['close'], window=14, smooth_window=3)
    stoch_k, stoch_d = stoch.stoch().iloc[-1], stoch.stoch_signal().iloc[-1]
    stoch_k_prev = stoch.stoch().iloc[-2]
    macd = MACD(df_5m['close'], window_slow=12, window_fast=26, window_sign=9)
    macd_val, macd_diff = macd.macd().iloc[-1], macd.macd_diff().iloc[-1]
    adx_val = ADXIndicator(df_5m['high'], df_5m['low'], df_5m['close'], window=14).adx().iloc[-1]
    atr = AverageTrueRange(df_5m['high'], df_5m['low'], df_5m['close'], window=14).average_true_range().iloc[-1]
    bullish_candles = (df_5m['close'].iloc[-3:] > df_5m['open'].iloc[-3:]).sum()
    volume_spike = df_orderbook['buy_volume'].iloc[-1] > df_orderbook['buy_volume'].mean() * 2
    recent_low = df_5m['low'].rolling(window=20).min().iloc[-1]
    is_bullish = df_5m['close'].iloc[-1] > df_5m['open'].iloc[-1]
    is_bearish = not is_bullish
    rsi_1m_drop = rsi_1m.iloc[-1] < 30 and rsi_1m.iloc[-1] < rsi_1m.iloc[-2]
    is_breaking_1m_support = df_1m['close'].iloc[-1] < df_1m['low'].rolling(5).min().iloc[-2]

    macd_long = MACD(df_5m['close'], window_slow=200, window_fast=50, window_sign=9)
    macd_long_diff_series = macd_long.macd_diff().fillna(0)

    macd_long_histogram = macd_long_diff_series.iloc[-1]
    macd_long_histogram_prev = macd_long_diff_series.iloc[-2]
    macd_long_slope = macd_long_diff_series.diff().fillna(0).iloc[-1]

    if macd_long_histogram_prev < 0 < macd_long_histogram and macd_long_slope > 0:
        logger.info(f"🔥 {ticker} 장기 MACD 골든크로스 발생! (기울기: {macd_long_slope:.4f})")

    current_volume_ratio = get_current_volume_ratio(ticker)
    my_asset = get_my_exchange_account()
    asset_data = my_asset.get("assets", {}).get(ticker)
    balance = float(asset_data.get("balance", 0)) if asset_data else 0
    current_investment = balance * latest_close
    max_per_ticker = MAX_TOTAL_INVEST * MAX_INVEST_PER_TICKER_RATIO
    cooldown_time = min(max(120, atr * 25), 600)
    last_buy_time = trading_context.last_buy_time.get(ticker)
    is_partial_reentry = trading_context.partial_sell_count.get(ticker, 0) > 0 and current_volume_ratio < 1.0
    is_low_volume_entry = current_volume_ratio < 0.5

    # === 매수 조건 ===
    if position == 0:

        last_sell_time = trading_context.last_sell_time.get(ticker)
        if last_sell_time:
            time_since_last_sell = (datetime.now() - last_sell_time).total_seconds()
            limit_time = min(max(180, atr * 30), 600)  # 최소 3분, 최대 10분
            if time_since_last_sell < limit_time:
                logger.warning(f"⛔ {ticker} 최근 손절 {trading_context.consecutive_losses.get(ticker, 0)}번 → {limit_time // 60}분 대기 중 → 매수 금지")
                return {"signal": "", "message": f"최근 손절 후 {limit_time // 60}분 대기 중 → 매수 금지"}

            if time_since_last_sell >= limit_time:
                trading_context.reset_loss(ticker)
                logger.info(f"✅ {ticker} 손절 제한 시간 종료 → 손절 횟수 초기화됨")

        if current_investment >= max_per_ticker:
            return {"signal": "", "message": "투자 비중 초과 → 매수 금지"}

        price_change_5m = df_5m['close'].iloc[-1] / df_5m['close'].iloc[-6] - 1
        if price_change_5m > 0.05:
            logger.warning(f"🚫 {ticker} 최근 5분간 5% 이상 급등 → 매수 보류")
            return {"signal": "", "message": "급등 이후 진입 제한"}

        if isinstance(last_buy_time, datetime) and (datetime.now() - last_buy_time).total_seconds() < cooldown_time:
            return {"signal": "", "message": "쿨다운 중 → 매수 금지"}

        if buy_price is None:
            buy_price = latest_close
            logger.info(f"ℹ️ {ticker} buy_price가 None → 현재가로 대체: {latest_close}")

        expected_profit_margin = (latest_close * 1.005) - (buy_price * 1.001)
        if expected_profit_margin < atr * 0.5:
            return {"signal": "", "message": "수익 기대폭 부족 → 진입 보류"}


        ignore_price_limit = (
            (
                rsi_5m < 35 and
                macd_val > 0 and
                volume_spike and
                orderbook_strength > 1.1
            ) or
            (
                stoch_k > stoch_d and
                stoch_k > 20 and
                orderbook_strength > 1.2 and
                macd_val > -0.05
            ) or
            (
                adx_val > 25 and
                macd_val > 0 and
                rsi_5m > 50 and
                orderbook_strength > 1.1  # 우상향일 때도 체결 강도 체크
            )
        )

        if adx_val > 25 and macd_val > 0 and rsi_5m > 50 and orderbook_strength > 1.1:
            logger.info(f"📈 {ticker} 우상향 추세 강함 → 평단 조건 무시하고 매수 허용")

        buy_conditions = [
            ((not is_partial_reentry and (latest_close - buy_price) / buy_price < -0.015) or ignore_price_limit, "평단 하락 또는 강한 시그널 진입 허용"),
            ((is_partial_reentry and latest_close <= buy_price * 1.01) or ignore_price_limit, "부분 익절 후 1% 이내 또는 강한 시그널 재진입"),

            (is_partial_reentry, "부분 익절 후 비중 회복 매수"),
            (is_low_volume_entry, "비중 50% 미만 → 추가 매수 허용"),
            ((not is_partial_reentry and (latest_close - buy_price) / buy_price < -0.015 and rsi_5m < 30 and macd_val > 0 and volume_spike), "재매수 허용 조건"),
            ((adx_val > 25 and macd_val > 0), "ADX 25 이상 + MACD 상승"),
            ((trading_context.consecutive_losses.get(ticker, 0) >= 5 and rsi_5m < 25 and macd_val > 0.1 and volume_spike), "연속 손절 후 재매수 허용"),
            ((trading_context.consecutive_losses.get(ticker, 0) > 3 and rsi_5m < 25 and macd_val > 0), "강제 매수 조건"),
            ((rsi_5m < 35 and latest_close <= bb_lower_5m.iloc[-1] and bullish_candles >= 2 and orderbook_strength < 1.2), "천천히 반등 매수"),
            ((latest_close <= bb_lower_5m.iloc[-1] and rsi_5m < 35 and volume_spike), "볼린저 하단 반등"),
            (((rsi_5m < 35 and latest_close <= bb_lower_5m.iloc[-1]) or (orderbook_strength > 1.3 and stoch_k > stoch_d) or (is_bullish and macd_val > -0.05)), "복합 조건 매수"),
            ((stoch_k > 20 and (stoch_k - stoch_d) > 10 and stoch_k > stoch_k_prev and volume_spike), "스토캐스틱 반등 매수"),
            ((is_bearish and rsi_5m < 30 and latest_close > recent_low and stoch_k < 20), "하락장 반등 매수")
        ]

        #if not is_partial_reentry and (latest_close - buy_price) / buy_price >= -0.015 and not ignore_price_limit:
            #logger.warning(
                #f"⛔ {ticker} 평단가 대비 충분히 하락하지 않음 → 매수 보류 "
                #f"(현재가: {latest_close:.2f}, 평단가: {buy_price:.2f})"
            #)
            #return {"signal": "", "message": "평단가 대비 1.5% 미만 하락 → 매수 보류"}

        for condition, message in buy_conditions:
            if condition:
                if "RSI" in message or "과매도" in message:
                    logger.info(f"✅ {ticker} 매수 조건 충족 → {message} (RSI: {rsi_5m:.2f}, MACD: {macd_val:.4f}, 체결강도: {orderbook_strength:.2f})")
                elif "스토캐스틱" in message:
                    logger.info(f"✅ {ticker} 매수 조건 충족 → {message} (Stoch_K: {stoch_k:.2f}, Stoch_D: {stoch_d:.2f})")
                elif "볼린저" in message:
                    logger.info(f"✅ {ticker} 매수 조건 충족 → {message} (현재가: {latest_close:.2f}, BB 하단: {bb_lower_5m.iloc[-1]:.2f})")
                else:
                    logger.info(f"✅ {ticker} 매수 조건 충족 → {message}")

                trading_context.last_buy_time[ticker] = datetime.now()
                trading_context.peak_price_since_buy[ticker] = latest_close
                trading_context.partial_sell_count[ticker] = 0

                prev_loss = trading_context.consecutive_losses.get(ticker, 0)
                trading_context.consecutive_losses[ticker] = max(0, prev_loss - 2)
                logger.info(f"📈 {ticker} 매수 성공 → 손절 횟수 {prev_loss} → {trading_context.consecutive_losses[ticker]} 감소")

                losses = trading_context.consecutive_losses.get(ticker, 0)

                if is_partial_reentry:
                    investment_ratio = 0.1
                    logger.info(f"📌 {ticker} 부분 익절 후 재매수 → 고정 투자 비율: 10%")
                else:
                    investment_ratio = max(0.1, 1.0 - (losses * 0.1))
                    logger.info(f"📉 {ticker} 손절 횟수 {losses} → 투자 비율: {investment_ratio * 100:.1f}%")

                save_trade_status(ticker, buy_price=latest_close, partial_sell_count=0, peak_price=latest_close)

                return {"signal": "buy", "message": message, "investment_ratio": investment_ratio}

    # === 매도 조건 ===
    if position == 1:
        if buy_price is None:
            buy_price = trading_context.avg_buy_price.get(ticker)
            if buy_price is None:
                logger.warning(f"⚠️ {ticker} 매도 포지션인데 매수가 없음 → 매도 전략 보류")
                return {"signal": "", "message": "매수가 정보 없음 → 매도 보류"}
            else:
                logger.info(f"ℹ️ {ticker} 매수가 상태에서 복원됨: {buy_price}")

        stop_loss, take_profit = calculate_stop_loss_take_profit(buy_price, atr, fee_rate)
        fixed_take_profit = calculate_fixed_take_profit(buy_price, fee_rate)
        net_profit = (latest_close * (1 - fee_rate)) - (buy_price * (1 + fee_rate))
        expected_profit = net_profit
        peak_price = trading_context.peak_price_since_buy.get(ticker, latest_close)
        partial_sell_time = trading_context.last_partial_sell_time.get(ticker)
        prev_peak = trading_context.peak_price_since_buy.get(ticker, latest_close)
        new_peak = max(prev_peak, latest_close)

        if new_peak > prev_peak:
            trading_context.peak_price_since_buy[ticker] = new_peak
            save_trade_status(ticker, peak_price=new_peak)
            logger.info(f"📈 {ticker} 최고가 갱신 → {new_peak:.2f}")

        # === 예외 처리: 급락 or 트레일링 스탑 발생 시 쿨다운 무시 ===
        is_critical_drop_price = latest_close < df_5m['low'].rolling(window=15).min().iloc[-1] * 0.99
        is_critical_drop_orderbook = (
        df_orderbook['sell_volume'].iloc[-5:].mean() > df_orderbook['sell_volume'].mean() * 3
        or orderbook_strength < 0.6
        )
        is_1m_crash = (rsi_1m_drop or is_breaking_1m_support) and net_profit > buy_price * 0.001


        if partial_sell_time and (datetime.now() - partial_sell_time).total_seconds() < 180 and not (
            is_critical_drop_price or is_critical_drop_orderbook or is_1m_crash
        ):
            logger.info(f"⏸️ {ticker} 부분 익절 후 3분 쿨다운 중 → 전체 매도 보류")
            return {"signal": "", "message": "부분 익절 후 쿨다운 중 → 매도 보류"}

        sell_conditions = [
            (latest_close < df_5m['low'].rolling(window=15).min().iloc[-1] * 0.99, "sell", "최근 15개 캔들 최저가 갱신 손절"),
            (latest_close >= fixed_take_profit and (not partial_sell_time or (datetime.now() - partial_sell_time).total_seconds() > 180), "sell_partial", "+1% 부분 익절"),
            (peak_price > buy_price * 1.015 and latest_close < peak_price * 0.988 and expected_profit > 0, "sell", "트레일링 스탑 익절 (최고가 대비 하락)"),
            ((rsi_1m_drop or is_breaking_1m_support) and net_profit > buy_price * 0.001, "sell_partial", "1분봉 급락 감지 → 선제 부분 익절"),
            ((df_orderbook['sell_volume'].iloc[-5:].mean() > df_orderbook['sell_volume'].mean() * 3 or orderbook_strength < 0.6) and net_profit > buy_price * 0.002, "sell", "5분봉 급락 감지 → 전체 매도"),
            (latest_close < stop_loss and (abs(latest_close - buy_price) > max(buy_price * 0.01, atr * 2) or abs(latest_close - buy_price) > atr * 1.5), "sell", f"손절 실행 (손절가: {stop_loss:.2f})")
        ]

        if peak_price > buy_price * 1.015 and latest_close < peak_price * 0.988 and expected_profit <= 0:
            logger.warning(f"⚠️ {ticker} 트레일링 스탑 조건 충족 BUT 손실 발생 가능 → 매도 보류")
            return {"signal": "", "message": "트레일링 스탑 조건이지만 손실 상태 → 매도 보류"}

        result = None

        for check, signal, message in sell_conditions:
            if check:
                logger.warning(f"📉 {ticker} 매도 조건 충족 → {message}")
                trading_context.peak_price_since_buy.pop(ticker, None)
                trading_context.last_partial_sell_time.pop(ticker, None)
                trading_context.partial_sell_count.pop(ticker, None)

                if signal == "sell":
                    if "트레일링 스탑" in message:
                        profit = (latest_close - buy_price) * (1 - fee_rate)
                        trading_context.realized_profit += profit
                        trading_context.daily_profit += profit
                        logger.info(f"💰 트레일링 스탑 실현 수익: +{profit:,.2f}원 | 누적 수익: {trading_context.realized_profit:,.2f}원")

                    trading_context.update_loss(ticker)
                    save_trade_status(ticker, consecutive_losses=trading_context.consecutive_losses.get(ticker, 0), last_sell_time=trading_context.last_sell_time.get(ticker))
                    result = {
                        "signal": "sell",
                        "message": message,
                        "stop_loss": stop_loss,
                        "take_profit": take_profit
                    }

                elif signal == "sell_partial":
                    partial_count = trading_context.partial_sell_count.get(ticker, 0)
                    sell_ratio = get_partial_sell_ratio(partial_count)

                    profit = (latest_close - buy_price) * sell_ratio * (1 - fee_rate)
                    trading_context.realized_profit += profit
                    trading_context.daily_profit += profit

                    logger.info(f"💰 부분 익절 실현 수익: +{profit:,.2f}원 | 누적 수익: {trading_context.realized_profit:,.2f}원")

                    trading_context.partial_sell_count[ticker] = partial_count + 1
                    trading_context.last_partial_sell_time[ticker] = datetime.now()
                    trading_context.consecutive_losses[ticker] = max(0, trading_context.consecutive_losses.get(ticker, 0) - 2)
                    trading_context.peak_price_since_buy.pop(ticker, None)

                    save_trade_status(
                        ticker,
                        partial_sell_count=trading_context.partial_sell_count[ticker],
                        last_partial_sell_time=trading_context.last_partial_sell_time[ticker]
                    )
                    result = {
                        "signal": "sell_partial",
                        "message": message,
                        "stop_loss": stop_loss,
                        "take_profit": take_profit,
                        "sell_ratio": sell_ratio
                    }

                return result

    logger.info(f"⛔ {ticker} 매매 전략 종료 → 신호 없음 (보유 상태: {position})")
    return {"signal": "", "message": "모든 매수 조건 미충족"}
