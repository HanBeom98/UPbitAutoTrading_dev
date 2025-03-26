# UPbitAutoTrading_dev/example.py

import logging
import time
from threading import Lock

from db.trade_state import save_trade_status
from trading.trade import buy_limit, sell_market, get_orderbook_data, \
  get_avg_buy_price_from_balance, cancel_order, \
  buy_market, get_tick_size, wait_for_limit_order, get_order_status, calculate_new_avg_buy_price, get_avg_buy_price
from trading.trading_strategy import trading_strategy, trading_context, \
  update_realized_profit
from account.my_account import get_my_exchange_account
from settings import TRADE_TICKERS, MAX_TOTAL_INVEST, MAX_INVEST_AMOUNT, MIN_ORDER_AMOUNT
from db.strategy_logger import log_trade_result
from utils.balance_util import get_total_balance, get_min_trade_volume

position = {}
market_data_cache = {}
last_trade_times = {}
highest_prices = {}

active_tickers = set()
ticker_lock = Lock()

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
console_handler = logging.StreamHandler()
formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# ✅ 수동 매수 자산 초기화
account_data = get_my_exchange_account()
if account_data and "assets" in account_data:
  for ticker, asset in account_data["assets"].items():
    if ticker in TRADE_TICKERS:
      balance = float(asset.get("balance", 0))
      avg_price = float(asset.get("avg_buy_price", 0))
      if balance > 0:
        position[ticker] = {"balance": balance, "avg_buy_price": avg_price}
        #last_trade_times[ticker] = time.time() - COOLDOWN_TIME
        highest_prices[ticker] = avg_price


def get_investment_amount(available_krw, current_position, ticker):
  holding_tickers = sum(
      1 for t in TRADE_TICKERS if current_position.get(t, {}).get("balance", 0) > 5000
  )
  if current_position.get(ticker, {}).get("balance", 0) > 5000:
    logger.info(f"✅ {ticker} 이미 보유 중 → 매수 스킵")
    return 0

  remaining_tickers = len(TRADE_TICKERS) - holding_tickers
  if remaining_tickers <= 0:
    logger.info(f"✅ 모든 코인 보유 중 → 매수 스킵")
    return 0

  total_invest_limit = min(available_krw, MAX_TOTAL_INVEST)
  invest_amount = total_invest_limit / len(TRADE_TICKERS)
  invest_amount = min(invest_amount, MAX_INVEST_AMOUNT)

  logger.info(f"📊 {ticker} 투자 금액: {invest_amount}원")
  return invest_amount


def process_ticker(ticker, current_balance, available_krw):
  global position

  with ticker_lock:
    if ticker in active_tickers:
      logger.info(f"⏸ {ticker} 이미 실행 중 → 중복 실행 방지")
      return
    active_tickers.add(ticker)

  try:
    if ticker not in market_data_cache:
      logger.warning(f"⚠️ {ticker} 시세 캐시 없음")
      return

    df_1m = market_data_cache.get(ticker, {}).get("1m")
    df_5m = market_data_cache.get(ticker, {}).get("5m")
    df_15m = market_data_cache.get(ticker, {}).get("15m")
    df_orderbook = get_orderbook_data(f"KRW-{ticker}")

    if df_1m is None or df_1m.empty:
      print(f"❌ {ticker} 1분봉 누락 → 매매 스킵")
      return
    if df_5m is None or df_5m.empty:
      print(f"❌ {ticker} 5분봉 누락 → 매매 스킵")
      return
    if df_15m is None or df_15m.empty:
      print(f"❌ {ticker} 15분봉 누락 → 매매 스킵")
      return
    if df_orderbook is None or df_orderbook.empty:
      print(f"❌ {ticker} 주문장 없음 → 매매 스킵")
      return

    balance = position.get(ticker, {}).get("balance", 0)
    is_holding = 1 if balance > 0 else 0
    avg_buy_price = get_avg_buy_price_from_balance(current_balance, ticker) if is_holding else None

    result = trading_strategy(
        df_1m, df_5m, df_15m, df_orderbook,
        is_holding, ticker=ticker, buy_price=avg_buy_price
    ) or {}

    signal = result.get("signal", "None")
    if signal not in ["buy", "sell", "sell_partial"]:
      logger.info(f"📭 {ticker} 매매 시그널 없음")
      return

    message = result.get("message", "")
    stop_loss = result.get("stop_loss", None)
    #last_trade_time = last_trade_times.get(ticker, 0)

    #if time.time() - last_trade_time < COOLDOWN_TIME:
      #logger.info(f"⏳ {ticker} 쿨다운 중")
      #return

    trade_result = None

    if signal == "buy":
      invest_amount = get_investment_amount(available_krw, position, ticker)
      if invest_amount < MIN_ORDER_AMOUNT:
        logger.warning(f"⚠️ {ticker} 투자금 부족")
        return

      buy_target_price = result.get("buy_target_price", df_5m['close'].iloc[-1])
      buy_price = get_tick_size(buy_target_price)
      volume = invest_amount / buy_price

      if volume >= get_min_trade_volume(f"KRW-{ticker}"):
        logger.info(f"🚀 {ticker} 매수 시도: {buy_price}원 × {volume}개")
        trade_result = buy_limit(f"KRW-{ticker}", buy_price, volume)

        if trade_result and "uuid" in trade_result:
          order_uuid = trade_result["uuid"]
          last_trade_times[ticker] = time.time()

          success, status = wait_for_limit_order(order_uuid, max_wait_time=10, interval=1)

          if success:
            new_avg_price = get_avg_buy_price(order_uuid)
            if new_avg_price is None:
              logger.warning(f"🚫 {ticker} 매수 체결 후 평균 매수가 확인 실패 → 현재가 사용")
              new_avg_price = df_5m['close'].iloc[-1]  # 또는 latest_close

            new_volume = float(trade_result.get("volume", 0)) if "volume" in trade_result else invest_amount / buy_price
            prev_qty = position.get(ticker, {}).get("balance", 0)
            prev_avg = position.get(ticker, {}).get("avg_buy_price", 0)

            updated_avg = calculate_new_avg_buy_price(prev_avg, prev_qty, new_avg_price, new_volume)

            position[ticker] = {
              "balance": prev_qty + new_volume,
              "avg_buy_price": updated_avg
            }

            save_trade_status(
                ticker,
                buy_price=updated_avg,
                partial_sell_count=0,
                peak_price=new_avg_price  # 또는 latest_close
            )

            logger.info(f"📌 {ticker} 평단가 갱신: {updated_avg:.2f}원, 총 보유 수량: {prev_qty + new_volume:.6f}")

          if not success:
            logger.warning(f"⚠️ {ticker} 지정가 미체결 → 시장가 매수")
            cancel_order(order_uuid)
            time.sleep(1)
            trade_result = buy_market(f"KRW-{ticker}", invest_amount)

            if trade_result and "uuid" in trade_result:
              order_uuid = trade_result["uuid"]

              # ❗ get_avg_buy_price 실패 대비
              new_avg_price = get_avg_buy_price(order_uuid)
              if new_avg_price is None:
                logger.warning(f"🚫 {ticker} 시장가 매수 체결 후 평균 매수가 확인 실패 → 현재가 사용")
                new_avg_price = df_5m['close'].iloc[-1]

              # ✅ 잔고 기준 보정 적용
              account_data = get_my_exchange_account()
              asset_data = account_data["assets"].get(ticker)
              if asset_data:
                final_avg_price = float(asset_data["avg_buy_price"])
                final_volume = float(asset_data["balance"])

                position[ticker] = {
                  "balance": final_volume,
                  "avg_buy_price": final_avg_price
                }

                save_trade_status(
                    ticker,
                    buy_price=final_avg_price,
                    partial_sell_count=0,
                    peak_price=final_avg_price
                )
                logger.info(f"📌 [잔고 기준] {ticker} 평단가: {final_avg_price:.2f}원, 수량: {final_volume:.6f}")

            else:
              new_volume = float(trade_result.get("volume", 0)) if "volume" in trade_result else invest_amount / buy_price
              prev_qty = position.get(ticker, {}).get("balance", 0)
              prev_avg = position.get(ticker, {}).get("avg_buy_price", 0)

              updated_avg = calculate_new_avg_buy_price(prev_avg, prev_qty, new_avg_price, new_volume)

              position[ticker] = {
                "balance": prev_qty + new_volume,
                "avg_buy_price": updated_avg
              }

              save_trade_status(
                  ticker,
                  buy_price=updated_avg,
                  partial_sell_count=0,
                  peak_price=new_avg_price
              )
              logger.info(f"📌 {ticker} 평단가 갱신: {updated_avg:.2f}원, 총 보유 수량: {prev_qty + new_volume:.6f}")

          log_trade_result(ticker, "buy", buy_price=buy_price, message=message)

    elif signal in ["sell_partial", "sell"]:
      volume = position.get(ticker, {}).get("balance", 0) * (0.5 if signal == "sell_partial" else 1.0)
      trade_result = sell_market(f"KRW-{ticker}", volume)
      if trade_result and "uuid" in trade_result:
        order_uuid = trade_result["uuid"]
        update_realized_profit(order_uuid, avg_buy_price)
        original_qty = position.get(ticker, {}).get("balance", 0)
        sell_ratio = result.get("sell_ratio", 0.5)
        remaining_qty = original_qty * (1 - sell_ratio)
        avg_price = position[ticker].get("avg_buy_price", avg_buy_price)

        position[ticker] = {
          "balance": remaining_qty,
          "avg_buy_price": avg_price
        }

        save_trade_status(
            ticker,
            buy_price=avg_price,
            partial_sell_count=trading_context.partial_sell_count.get(ticker, 0),
            last_partial_sell_time=trading_context.last_partial_sell_time.get(ticker),
            peak_price=trading_context.peak_price_since_buy.get(ticker, 0)
        )

        time.sleep(0.5)
        status = get_order_status(order_uuid)
        trades = status.get("trades", [])
        if trades:
          total_price = sum(float(t["price"]) * float(t["volume"]) for t in trades)
          total_volume = sum(float(t["volume"]) for t in trades)
          avg_sell_price = total_price / total_volume if total_volume > 0 else None
          if avg_buy_price and avg_buy_price > 0:
            profit = (avg_sell_price - avg_buy_price) / avg_buy_price * 100
          else:
            profit = None
          log_trade_result(ticker, signal, sell_price=avg_sell_price, profit_rate=profit, message=message)

          current_total = get_total_balance()
          if trading_context.total_start_balance:
            daily_profit = current_total - trading_context.total_start_balance
            profit_rate = (daily_profit / trading_context.total_start_balance) * 100
            trading_context.daily_profit = profit_rate
            logger.info(f"📊 평가 수익: {daily_profit:,.0f}원 / 수익률: {profit_rate:.2f}%")
            logger.info(f"💼 현재 총 평가 자산: {current_total:,.0f}원 / 기준 자산: {trading_context.total_start_balance:,.0f}원")
            logger.info(f"📈 누적 실현 수익: {trading_context.realized_profit:,.0f}원")

    if not trade_result or "uuid" not in trade_result:
      logger.warning(f"🚨 {ticker} 매매 실패")
      if stop_loss:
        volume = position.get(ticker, {}).get("balance", 0)
        if volume > 0:
          logger.warning(f"🛑 {ticker} 손절 실행")
          result = sell_market(f"KRW-{ticker}", volume)
          if result and "uuid" in result:
            order_uuid = result["uuid"]
            update_realized_profit(order_uuid, avg_buy_price)
            time.sleep(0.5)
            status = get_order_status(order_uuid)
            trades = status.get("trades", [])
            if trades:
              total_price = sum(float(t["price"]) * float(t["volume"]) for t in trades)
              total_volume = sum(float(t["volume"]) for t in trades)
              avg_sell_price = total_price / total_volume if total_volume > 0 else None
              if avg_buy_price and avg_buy_price > 0:
                profit = (avg_sell_price - avg_buy_price) / avg_buy_price * 100
              else:
                profit = None
              log_trade_result(ticker, "stop_loss", sell_price=avg_sell_price, profit_rate=profit, message="손절 실행")

              current_total = get_total_balance()
              if trading_context.total_start_balance:
                daily_profit = current_total - trading_context.total_start_balance
                profit_rate = (daily_profit / trading_context.total_start_balance) * 100
                trading_context.daily_profit = profit_rate
                logger.info(f"📊 평가 수익: {daily_profit:,.0f}원 / 수익률: {profit_rate:.2f}%")
                logger.info(f"💼 현재 총 평가 자산: {current_total:,.0f}원 / 기준 자산: {trading_context.total_start_balance:,.0f}원")
                logger.info(f"📈 누적 실현 수익: {trading_context.realized_profit:,.0f}원")

    time.sleep(1)

  except Exception as e:
    logger.error(f"🚨 {ticker} 전략 실행 오류: {e}", exc_info=True)

  finally:
    with ticker_lock:
      active_tickers.remove(ticker)