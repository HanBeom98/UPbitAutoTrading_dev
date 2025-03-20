import logging
import time

from apscheduler.schedulers.background import BackgroundScheduler

from account.my_account import get_my_exchange_account
from trading.trade import buy_limit, get_min_trade_volume, \
  get_tick_size, sell_market, get_orderbook_data
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

  if new_market_data:
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
    new_balance_data = get_my_exchange_account()
    if not new_balance_data or "assets" not in new_balance_data:
        logger.error(f"🚨 {ticker} API 재조회 실패 → 응답 없음 또는 assets 키 누락")
        return 0  # 🔥 API 문제가 있어도 매수를 건너뛰지 않고 0 반환

    # ✅ 최신 balance_data를 재할당하여 반영
    balance_data = new_balance_data

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

# ✅ 투자금 배분을 균등하게 설정
def get_investment_amount(available_krw, current_position, ticker):
    """균등한 투자 금액을 계산"""
    total_tickers = len(TRADE_TICKERS)
    holding_tickers = sum(1 for t in TRADE_TICKERS if current_position.get(t, {}).get("balance", 0) > 5000)

    if holding_tickers >= total_tickers:
        logger.info(f"✅ 모든 코인 보유 중 → 신규 매수 금지")
        return 0

    remaining_tickers = total_tickers - holding_tickers
    invest_amount = min(available_krw / remaining_tickers, MAX_INVEST_AMOUNT)

    logger.info(f"📊 {ticker} 투자 금액 계산 - 투자 가능 금액: {invest_amount}원")
    return invest_amount

def execute_trade():
    """📌 매매 전략 실행 및 주문 처리"""
    global position

    # ✅ 최신 시세 데이터 업데이트
    update_market_data()

    # ✅ 업비트 API에서 보유 자산 정보 조회
    current_balance = get_my_exchange_account()
    if not current_balance:
        logger.error("🚨 업비트 API에서 보유 코인 데이터를 가져오지 못함. 거래 불가.")
        return

    available_krw = current_balance.get("KRW", 0)
    position = current_balance.get("assets", {})

    if available_krw < MIN_ORDER_AMOUNT:
        logger.warning(f"⚠️ 사용 가능한 원화 부족! 현재 잔고: {available_krw}원")
        return

    for ticker in TRADE_TICKERS:
        if ticker not in market_data_cache:
          continue

        # ✅ 5분봉과 15분봉 데이터를 개별적으로 가져옴
        df_5m = market_data_cache[ticker].get("5m")
        df_15m = market_data_cache[ticker].get("15m")
        df_orderbook = get_orderbook_data(f"KRW-{ticker}")

        # ✅ 데이터 검증
        if df_5m is None or df_5m.empty or df_15m is None or df_15m.empty:
            logger.warning(f"⚠️ {ticker} 차트 데이터가 없음")
            continue

        if df_orderbook is None or df_orderbook.empty:
            logger.warning(f"⚠️ {ticker} 주문장 데이터 없음, 매매 전략 실행 건너뜀")
            continue

        try:
            # ✅ 보유 여부 확인
            is_holding = 1 if position.get(ticker, {}).get("balance", 0) > 0 else 0
            avg_buy_price = get_avg_buy_price(current_balance, ticker) or 0

            # ✅ 매매 전략 실행
            strategy_result = trading_strategy(df_5m, df_15m, df_orderbook, is_holding, ticker=ticker, buy_price=avg_buy_price) or {}

            logger.debug(f"🔍 {ticker} 전략 반환값: {strategy_result}")

            signal = strategy_result.get("signal", "None")

            if signal not in ["buy", "sell"]:
                logger.info(f"⚠️ {ticker} 매매 시그널 없음. 전략 결과: {strategy_result}")
                continue

            message = strategy_result.get("message", "")
            stop_loss = strategy_result.get("stop_loss", None)
            take_profit = float(strategy_result.get("take_profit", 0) or 0)

            # ✅ **COOLDOWN 적용을 먼저 체크하여 불필요한 연산 방지**
            last_trade_time = last_trade_times.get(ticker, 0) or 0
            if time.time() - last_trade_time < COOLDOWN_TIME:
                logger.info(f"⏳ {ticker} 쿨다운 적용 중. 남은 시간: {COOLDOWN_TIME - (time.time() - last_trade_time)}초")
                continue

            trade_result = None

            # ✅ 매수 로직
            if signal == "buy":
                last_trade_time = last_trade_times.get(ticker, 0)

                if time.time() - last_trade_time < COOLDOWN_TIME:
                    logger.info(f"⏳ {ticker} 매수 쿨다운 적용 중. 남은 시간: {COOLDOWN_TIME - (time.time() - last_trade_time)}초")
                    continue  # ✅ 매수에만 쿨다운 적용, 매도는 실행 가능

                buy_target_price = strategy_result.get("buy_target_price", df_5m['close'].iloc[-1] * 0.999)

                # ✅ 기존 방식 (고정 비율) 대신 균등 투자 배분 적용
                invest_amount = get_investment_amount(available_krw, position, ticker)

                if invest_amount < MIN_ORDER_AMOUNT:
                    logger.warning(f"⚠️ {ticker} 투자 금액이 최소 주문 금액보다 적음 → 매수 스킵")
                    continue

                buy_price = get_tick_size(buy_target_price)
                volume = invest_amount / buy_price

                if volume >= get_min_trade_volume(f"KRW-{ticker}"):
                    logger.info(f"🚀 {ticker} 지정가 매수 주문 시도 - 목표가: {buy_price}, 수량: {volume}")
                    trade_result = buy_limit(f"KRW-{ticker}", buy_price, volume)

                    if trade_result and "uuid" in trade_result:
                        logger.info(f"✅ {ticker} 지정가 매수 주문 완료 - 주문 UUID: {trade_result['uuid']}")
                        last_trade_times[ticker] = time.time()

                        # ✅ 매수 후 즉시 잔고 업데이트 (투자금 반영)
                        time.sleep(1)  # 1초 대기 후 API 조회
                        my_balance = get_my_exchange_account()
                        available_krw = my_balance.get("KRW", 0)
                        position = my_balance.get("assets", {})
                    else:
                        logger.error(f"🚨 {ticker} 지정가 매수 주문 실패 - 응답 오류: {trade_result}")

            # ✅ 매도 로직
            elif signal == "sell":
                sell_volume = position.get(ticker, {}).get("balance", 0)
                if sell_volume <= 0:
                    logger.warning(f"⚠️ {ticker} 매도 실패! 보유량이 없음.")
                    continue

                # ✅ trading_strategy에서 익절/손절 여부 확인 후 실행
                if message.startswith("급락 가능성 감지 → 즉시 익절") or message.startswith("+1% 익절"):
                    logger.info(f"🚀 {ticker} {message} → 시장가 매도 실행")
                    trade_result = sell_market(f"KRW-{ticker}", sell_volume)

                elif message.startswith("손절 실행"):
                    logger.info(f"❌ {ticker} {message} → 시장가 매도")
                    trade_result = sell_market(f"KRW-{ticker}", sell_volume)

                else:
                    # ✅ `trading_strategy.py`에서 판단을 내리지 않았다면 보조 체크
                    current_price = df_5m['close'].iloc[-1]
                    if stop_loss is not None and current_price < stop_loss:
                        logger.info(f"🚨 {ticker} 손절 실행! 현재가({current_price}) < 손절가({stop_loss}) → 시장가 매도")
                        trade_result = sell_market(f"KRW-{ticker}", sell_volume)

                    elif take_profit is not None and current_price >= take_profit * 0.998:
                        logger.info(f"🚀 {ticker} 익절 목표가 근접 → 시장가 매도 (현재가: {current_price}, 목표가: {take_profit})")
                        trade_result = sell_market(f"KRW-{ticker}", sell_volume)

            # ✅ 매도 후 응답 확인
            if not trade_result:
                logger.warning(f"🚨 {ticker} 시장가 매도 실패 - API 응답 없음")
            elif "uuid" not in trade_result:
                logger.warning(f"🚨 {ticker} 시장가 매도 실패 - 응답에 UUID 없음: {trade_result}")

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