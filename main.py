import logging
import os
import time

import numpy as np
from apscheduler.schedulers.background import BackgroundScheduler
from utils.db import save_trade_record
from account.my_account import get_my_exchange_account, get_balance
from trading.trade import get_order_status, cancel_old_orders, \
  check_order_status, buy_limit, sell_limit, get_min_trade_volume, \
  get_tick_size, sell_market, buy_market, get_current_price, get_open_orders, \
  get_orderbook_data
from trading.trading_strategy import trading_strategy
from upbit_data.candle import get_min_candle_data


# 🔹 로깅 설정
logger = logging.getLogger(__name__)
log_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)

logger.addHandler(console_handler)
logger.setLevel(logging.INFO)

# 🔹 매매 설정
TRADE_TICKERS = ['ETH', 'TRUMP', 'XRP', 'ADA','BTC', 'SUI']
INVEST_RATIO = 0.95 / len(TRADE_TICKERS)
MAX_INVEST_AMOUNT = 400000
MIN_ORDER_AMOUNT = 5000
COOLDOWN_TIME = 60  # 초 단위
MAX_WAIT_TIME = 20  # ✅ 미체결 주문 자동 취소 대기 시간 (초)

# 🔹 상태 저장 변수
position = {}  # ✅ 보유 코인 상태 저장
market_data_cache = {}  # ✅ 시세 캐시
last_trade_times = {}  # ✅ 최근 매매 시간 저장
tracked_orders = set()


def update_market_data():
  """🔄 각 코인의 최신 시세 데이터를 업데이트"""
  global market_data_cache
  logger.info("========== update_market_data() 실행 ==========")

  new_market_data = {}

  for ticker in TRADE_TICKERS:
    try:
      logger.info(f"📡 {ticker} 시세 데이터를 가져오는 중...")

      data_dict = get_min_candle_data(f'KRW-{ticker}', [5, 15])

      data_5min = data_dict.get(5)

      if data_5min is None or data_5min.empty:
        logger.warning(f"⚠️ {ticker} 5분봉 데이터 없음, 업데이트 건너뜀")
        continue

      data_15min = data_dict.get(15)
      if data_15min is None or data_15min.empty:
        logger.warning(f"⚠️ {ticker} 15분봉 데이터 없음, 업데이트 건너뜀")

      new_market_data[ticker] = {"5m": data_5min, "15m": data_15min}

      logger.info(f"✅ {ticker} 시세 업데이트 완료 | 5분봉 현재가: {data_5min['close'].iloc[-1]} | 거래량: {data_5min['volume'].iloc[-1]}")

    except Exception as e:
      logger.error(f"🚨 {ticker} 시세 업데이트 오류 발생: {e}")

  market_data_cache.update(new_market_data)


def get_avg_buy_price(balance_data, ticker):
  """업비트 API에서 평균 매수가(avg_buy_price)를 가져오되, 보유하지 않은 코인은 0으로 반환"""
  asset_info = balance_data.get("assets", {}).get(ticker, {})

  # ✅ **보유하지 않은 경우 평균 매수가 0으로 설정하여 매수 가능하도록 수정**
  if not asset_info:
    logger.info(f"⚠️ {ticker} 보유하지 않음. 평균 매수가 0으로 설정.")
    return 0  # 🔥 **보유하지 않은 경우 0을 반환**

  avg_price = asset_info.get("avg_buy_price")

  if avg_price is None or avg_price == 0:
    logger.warning(f"⚠️ {ticker} 평균 매수가 없음 → API 재조회 시도")

    # ✅ API 재조회
    updated_balance = get_my_exchange_account()

    if not updated_balance or "assets" not in updated_balance:
      logger.error(f"🚨 {ticker} API 재조회 실패 → 응답 없음 또는 assets 키 누락")
      return 0  # 🔥 **API 문제가 있어도 매수를 건너뛰지 않고 0 반환**

    # ✅ 최신 balance_data 반영
    balance_data.update(updated_balance)

    # ✅ 최신 balance_data에서 다시 평균 매수가 가져오기
    asset_info = balance_data["assets"].get(ticker, {})
    avg_price = asset_info.get("avg_buy_price")

    # ✅ DEBUG: API에서 가져온 데이터 로그 출력
    logger.debug(f"🔍 {ticker} 재조회된 avg_buy_price: {avg_price}")

    # ✅ **여전히 평균 매수가 없으면 0으로 반환 (매수 가능)**
    if avg_price is None or avg_price == 0:
      logger.warning(f"⚠️ {ticker} 평균 매수가 없으므로 0으로 설정.")
      return 0  # 🔥 **보유하지 않은 코인은 매수할 수 있도록 0 반환**

  return float(avg_price)

def execute_trade():
  """📌 매매 전략 실행 및 주문 처리"""
  global position

  # ✅ 최신 시세 데이터 업데이트
  update_market_data()

  # ✅ 업비트 API에서 보유 자산 정보 조회
  my_balance = get_my_exchange_account()
  if not my_balance:
    logger.error("🚨 업비트 API에서 보유 코인 데이터를 가져오지 못함. 거래 불가.")
    return

  available_krw = my_balance.get("KRW", 0)
  position = my_balance.get("assets", {})

  if available_krw < MIN_ORDER_AMOUNT:
    logger.warning(f"⚠️ 사용 가능한 원화 부족! 현재 잔고: {available_krw}원")
    return

  for ticker in TRADE_TICKERS:
    if ticker not in market_data_cache:
        continue

    # ✅ 5분봉과 15분봉 데이터를 개별적으로 가져옴
    df_5m = market_data_cache[ticker].get("5m")
    df_15m = market_data_cache[ticker].get("15m")

    required_columns = {'close', 'high', 'low', 'open', 'volume'}

    if df_5m is None or df_5m.empty:
      logger.warning(f"⚠️ {ticker} 5분봉 데이터가 None이거나 비어 있음.")
      continue

    if df_5m.isnull().values.any() or not required_columns.issubset(df_5m.columns):
      logger.warning(f"⚠️ {ticker} 5분봉 데이터 오류 (컬럼 문제 가능): {df_5m.columns}")
      continue

    if df_15m is None or df_15m.empty:
      logger.warning(f"⚠️ {ticker} 15분봉 데이터가 None이거나 비어 있음.")
      continue

    if df_15m.isnull().values.any() or not required_columns.issubset(df_15m.columns):
      logger.warning(f"⚠️ {ticker} 15분봉 데이터 오류 (컬럼 문제 가능): {df_15m.columns}")
      continue

    # ✅ 주문장 데이터 가져오기 (df_orderbook 추가)
    df_orderbook = get_orderbook_data(f"KRW-{ticker}")

    # ✅ df_orderbook이 None이거나 비어 있으면 건너뜀
    if df_orderbook is None or df_orderbook.empty:
        logger.warning(f"⚠️ {ticker} 주문장 데이터 없음, 매매 전략 실행 건너뜀")
        continue

    try:
      # ✅ **보유 여부와 관계없이 매매 전략 실행**
      is_holding = 1 if position.get(ticker, {}).get("balance", 0) > 0 else 0

      # ✅ **보유하지 않은 코인도 매수할 수 있도록 평균 매수가 기본값을 0으로 설정**
      avg_buy_price = get_avg_buy_price(my_balance, ticker) or 0

      # ✅ **매매 전략 실행**
      strategy_result = trading_strategy(df_5m, df_15m, df_orderbook, is_holding, ticker=ticker, buy_price=avg_buy_price) or {}

      logger.debug(f"🔍 {ticker} 전략 반환값: {strategy_result}")

      signal = strategy_result.get("signal", "None")

      # ✅ 매매 시그널이 없는 경우 로그 추가
      if signal not in ["buy", "sell"]:
        logger.info(f"⚠️ {ticker} 매매 시그널 없음. 전략 결과: {strategy_result}")
        continue

      message = strategy_result.get("message", "")
      stop_loss = strategy_result.get("stop_loss", None)  # 손절가
      take_profit = strategy_result.get("take_profit", None)  # 익절가
      buy_target_price = strategy_result.get("buy_target_price") if strategy_result else None
      if buy_target_price is None:
        buy_target_price = df_5m['close'].iloc[-1] * 0.999  # 기본값 설정

      # ✅ 매수 및 매도 시도 로그 추가
      if signal == "buy":
        logger.info(f"📌 {ticker} 매수 시도 중... 목표가: {buy_target_price}")
      elif signal == "sell":
        logger.info(f"📌 {ticker} 매도 시도 중... 손절가: {stop_loss}, 익절가: {take_profit}")

      logger.info(f"📌 {ticker} 매매 전략 실행 결과 - signal: {signal}, message: {message}, stop_loss: {stop_loss}, take_profit: {take_profit}")

      # ✅ **미체결 주문 확인 및 자동 취소 (매매 전에 먼저 실행)**
      cancel_old_orders(f"KRW-{ticker}", MAX_WAIT_TIME)
      open_orders = get_open_orders(f"KRW-{ticker}")
      if open_orders:
        logger.info(f"⏳ {ticker} 미체결 주문이 있음 → 체결될 시간을 더 기다림")
        continue  # **취소하지 않고 루프를 넘어감**

      # ✅ COOLDOWN 적용
      last_trade_time = last_trade_times.get(ticker, 0)
      if time.time() - last_trade_time < COOLDOWN_TIME:
        logger.info(f"⏳ {ticker} 쿨다운 적용 중. 남은 시간: {COOLDOWN_TIME - (time.time() - last_trade_time)}초")
        continue

      # ✅ 최소 거래 금액 고려 (업비트 최소 주문 단위 적용)
      min_trade_volume = get_min_trade_volume(f"KRW-{ticker}")

      # ✅ 매수 로직 수정 (buy_target_price 적용)
      if signal == "buy":
        last_trade_time = last_trade_times.get(ticker, 0)

        if time.time() - last_trade_time < COOLDOWN_TIME:
          logger.info(f"⏳ {ticker} 쿨다운 적용 중. 남은 시간: {COOLDOWN_TIME - (time.time() - last_trade_time)}초")
          continue  # ✅ 쿨다운 중이면 매매 안 함

        invest_amount = min(available_krw * INVEST_RATIO, MAX_INVEST_AMOUNT)
        buy_price = get_tick_size(buy_target_price)
        volume = invest_amount / buy_price

        if volume >= min_trade_volume:
          logger.info(f"🚀 {ticker} 지정가 매수 주문 시도 - 목표가: {buy_price}, 수량: {volume}")

          last_trade_times[ticker] = time.time()

          trade_result = buy_limit(f"KRW-{ticker}", buy_price, volume)

          if not trade_result or "uuid" not in trade_result:
            logger.error(f"🚨 {ticker} 지정가 매수 주문 실패 - 응답 오류: {trade_result}")
            continue

          order_uuid = trade_result["uuid"]
          logger.info(f"📌 {ticker} 지정가 매수 주문 완료 - 주문 UUID: {order_uuid}")

          order_state = "확인 불가"  # 기본값 설정


          # ✅ 주문 후 일정 시간(예: 10초) 동안 체결 여부 확인
          wait_time = 30  # 주문 유지 시간 (초)
          start_time = time.time()

          while time.time() - start_time < wait_time:
            # ✅ 주문 상태 확인 추가
              time.sleep(1)  # 🔥 API 업데이트 시간이 필요할 수 있음
              order_status = check_order_status(order_uuid)
              order_state = order_status.get("state", "확인 불가")

              if order_state == "done" and order_uuid in tracked_orders:
                  tracked_orders.remove(order_uuid)
                  logger.info(f"✅ {ticker} 매수 체결 완료 - 주문 UUID: {order_uuid}, 체결 가격: {order_status.get('price', '미확인')}")
                  break  # 🔥 체결되면 즉시 루프 탈출

              logger.info(f"⏳ {ticker} 매수 주문 대기 중... 현재 상태: {order_state}")

          # ✅ 주문이 체결되지 않았으면 한 번만 취소
          if order_state != "done":
              logger.warning(f"⚠️ {ticker} 매수 주문이 10초 동안 체결되지 않음 → 주문 취소 진행")
              cancel_old_orders(f"KRW-{ticker}", MAX_WAIT_TIME)

              for retry in range(3):
                  try:
                      cancel_old_orders(f"KRW-{ticker}", MAX_WAIT_TIME)
                      logger.info(f"✅ {ticker} 주문 취소 성공")
                      break  # 성공하면 루프 탈출
                  except Exception as e:
                      logger.warning(f"⚠️ {ticker} 주문 취소 실패! {retry+1}/3 재시도 중... UUID: {order_uuid}")
                      time.sleep(1)  # 🔥 API 요청 속도 제한 추가
              else:
                  logger.error(f"🚨 {ticker} 주문 취소 최종 실패! UUID: {order_uuid}")

          # ✅ 🔥 시장가 매수 시도 (단, 현재 가격이 너무 높으면 취소)
          current_price = get_current_price(f"KRW-{ticker}")
          max_acceptable_price = buy_target_price * 1.001  # 🔥 0.20% 이상 차이나면 취소

          if current_price <= max_acceptable_price:
              time.sleep(1)  # API 요청 딜레이 고려
              available_krw = get_my_exchange_account().get("KRW", 0)
              invest_amount = min(available_krw, MAX_INVEST_AMOUNT)

              # ✅ 예상 구매 수량 & 금액 계산
              expected_volume = invest_amount / current_price
              expected_cost = expected_volume * current_price  # 실제 예상 매수 금액

              # ✅ 예상 금액이 초과되면 매수 취소
              if expected_cost > MAX_INVEST_AMOUNT:
                  logger.warning(f"⚠️ {ticker} 시장가 매수 취소 - 예상 금액 초과 (최대 {MAX_INVEST_AMOUNT}원, 예상 {expected_cost:.2f}원)")
                  return

              logger.info(f"🚀 {ticker} 시장가 매수 시도 - 현재가: {current_price}")

              trade_result = buy_market(f"KRW-{ticker}", invest_amount)

              if trade_result and "uuid" in trade_result:
                  logger.info(f"✅ {ticker} 시장가 매수 완료 - 주문 UUID: {trade_result['uuid']}")
                  last_trade_times[ticker] = time.time()
              else:
                  logger.warning(f"🚨 {ticker} 시장가 매수 실패")
          else:
              logger.warning(f"⚠️ {ticker} 시장가 매수 취소 - 현재가 {current_price} (허용 범위 초과)")

      # ✅ 매도 로직 수정 (trading_strategy() 반영)
      if signal == "sell":
          sell_volume = position.get(ticker, {}).get("balance", 0)

          if sell_volume <= 0:
            logger.warning(f"⚠️ {ticker} 매도 실패! 보유량이 없음.")
            continue

          # ✅ 1. 익절 주문이 있는지 확인
          if take_profit is not None:
              sell_price = get_tick_size(take_profit)
              trade_result = sell_limit(f"KRW-{ticker}", sell_price, sell_volume)

              if not trade_result or "uuid" not in trade_result:
                  logger.warning(f"🚨 {ticker} 지정가 매도 주문 실패 - API 응답 오류: {trade_result}")
                  continue

              order_uuid = trade_result["uuid"]
              logger.info(f"✅ {ticker} 지정가 매도 주문 완료 - 주문 UUID: {order_uuid}")

              # ✅ 2. 주문 체결 여부 확인 (최대 10초)
              wait_time = 10  # 주문 유지 시간 (초)
              start_time = time.time()

              while time.time() - start_time < wait_time:
                  time.sleep(1)  # 🔥 API 업데이트 시간이 필요할 수 있음
                  order_status = check_order_status(order_uuid)
                  order_state = order_status.get("state", "확인 불가")

                  if order_state == "done":
                      logger.info(f"✅ {ticker} 매도 체결 완료 - 주문 UUID: {order_uuid}, 체결 가격: {order_status.get('price', '미확인')}")
                      break

                  logger.info(f"⏳ {ticker} 매도 주문 대기 중... 현재 상태: {order_state}")

              else:
                  logger.warning(f"⚠️ {ticker} 매도 주문이 10초 동안 체결되지 않음 → 주문 취소 진행")
                  cancel_old_orders(f"KRW-{ticker}", MAX_WAIT_TIME)

          # ✅ 3. 손절 처리 (익절 주문이 없거나 체결되지 않았을 경우)
          current_price = df_5m['close'].iloc[-1]
          take_profit = float(strategy_result.get("take_profit", 0.0))

          if stop_loss is not None and current_price < stop_loss:
              # ✅ 익절 주문이 체결되지 않았고, 손절 조건 충족 시 시장가 매도
              logger.info(f"🚨 {ticker} 손절 실행! 현재가({current_price}) < 손절가({stop_loss}) → 시장가 매도")
              trade_result = sell_market(f"KRW-{ticker}", sell_volume)

              if not trade_result or "uuid" not in trade_result:
                  logger.warning(f"🚨 {ticker} 시장가 매도 실패 - API 응답 오류: {trade_result}")
                  continue

          # ✅ 4. 익절 주문이 체결되지 않았지만, 현재가가 익절가의 99.8% 이상이면 시장가 매도
          elif take_profit > 0 and current_price >= take_profit * 0.998:
              logger.info(f"🚀 {ticker} 익절 목표가 근접 → 시장가 매도 (현재가: {current_price}, 목표가: {take_profit})")
              trade_result = sell_market(f"KRW-{ticker}", sell_volume)

              if not trade_result or "uuid" not in trade_result:
                  logger.warning(f"🚨 {ticker} 시장가 매도 실패 - API 응답 오류: {trade_result}")
                  continue

          # ✅ 최신 잔고 업데이트
          time.sleep(1)  # API 호출 부담을 줄이기 위해 1초 대기
          my_balance = get_my_exchange_account()
          available_krw = my_balance.get("KRW", 0)
          position = my_balance.get("assets", {})

          logger.debug(f"🔄 최신 position 데이터: {position}")

    except Exception as e:
      logger.error(f"🚨 {ticker} 매매 전략 실행 중 오류 발생: {e}", exc_info=True)

scheduler = BackgroundScheduler()
scheduler.add_job(execute_trade, 'interval', seconds=10, max_instances=6)
scheduler.start()

if __name__ == '__main__':
  logger.info('++++++++++ 자동매매 시작 ++++++++++')

  # ✅ 최신 보유 코인 정보 동기화
  my_balance = get_my_exchange_account()
  if my_balance:
    position = my_balance["assets"]

    # ✅ DEBUG: API 응답 확인
    logger.info(f"🔍 초기 my_balance 데이터: {my_balance}")
    logger.info("✅ 초기 보유 코인 정보 동기화 완료")
  else:
    logger.error("🚨 초기 보유 코인 정보를 가져오지 못했습니다. 자동매매를 시작할 수 없습니다.")
    exit(1)  # 강제 종료

  try:
    while True:
      time.sleep(10)
  except (KeyboardInterrupt, SystemExit):
    logger.warning("⛔ 자동매매 종료 요청 감지. 시스템 종료 중...")
    scheduler.shutdown()