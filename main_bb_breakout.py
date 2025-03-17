import logging
import os
import time

import numpy as np
from apscheduler.schedulers.background import BackgroundScheduler
from utils.db import save_trade_record
from account.my_account import get_my_exchange_account
from trading.trade import buy_market, get_order_status, sell_market, cancel_old_orders, check_order_status
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
TRADE_TICKERS = ['ETH', 'SOL', 'TRUMP', 'XRP', 'ZRO', 'VIRTUAL', 'ADA']
INVEST_RATIO = 0.95 / len(TRADE_TICKERS)
MAX_INVEST_AMOUNT = 400000
MIN_ORDER_AMOUNT = 5000
COOLDOWN_TIME = 30  # 초 단위
MAX_WAIT_TIME = 20  # ✅ 미체결 주문 자동 취소 대기 시간 (초)

# 🔹 상태 저장 변수
position = {}  # ✅ 보유 코인 상태 저장
market_data_cache = {}  # ✅ 시세 캐시
last_trade_times = {}  # ✅ 최근 매매 시간 저장


def update_market_data():
    """🔄 각 코인의 최신 시세 데이터를 업데이트"""
    global market_data_cache
    logger.info("========== update_market_data() 실행 ==========")

    new_market_data = {}

    for ticker in TRADE_TICKERS:
        try:
            logger.info(f"📡 {ticker} 시세 데이터를 가져오는 중...")
            data = get_min_candle_data(f'KRW-{ticker}', 1)
            if data is None or data.empty or data.tail(1).isnull().values.any():
                logger.warning(f"⚠️ {ticker} 시세 데이터 없음, 업데이트 건너뜀")
                continue

            new_market_data[ticker] = data.copy()
            logger.info(f"✅ {ticker} 시세 업데이트 완료 | 현재가: {data['close'].iloc[-1]} | 거래량: {data['volume'].iloc[-1]}")

        except Exception as e:
            logger.error(f"🚨 {ticker} 시세 업데이트 오류 발생: {e}")

    if not new_market_data:
        logger.error("🚨 모든 시세 데이터가 없음. API 문제 가능성 있음!")

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
            logger.warning(f"⚠️ {ticker} 시세 데이터 없음. 건너뜀.")
            continue

        df = market_data_cache[ticker]
        if df is None or df.empty or df.isnull().values.any():
            logger.warning(f"⚠️ {ticker} 시세 데이터 없음. 거래 건너뜀.")
            continue

        try:
            # ✅ **보유 여부와 관계없이 매매 전략 실행**
            is_holding = 1 if position.get(ticker, {}).get("balance", 0) > 0 else 0

            # ✅ **보유하지 않은 코인도 매수할 수 있도록 평균 매수가 기본값을 0으로 설정**
            avg_buy_price = get_avg_buy_price(my_balance, ticker) or 0

            # ✅ **매매 전략 실행**
            strategy_result = trading_strategy(df, is_holding, ticker=ticker, buy_price=avg_buy_price) or {"signal": "", "message": ""}

            signal = strategy_result.get("signal", "None")
            message = strategy_result.get("message", f"매매 전략에서 message 키가 없음, strategy_result: {strategy_result}")

            # ✅ **디버깅 로그 추가**
            logger.debug(f"📊 {ticker} 매매 전략 결과: signal={signal}, message={message}")

            if signal not in ["buy", "sell"]:
                logger.info(f"⚠️ {ticker} 매매 전략 신호 없음. 거래 건너뜀. | message: {message}")
                continue

            logger.info(f"📌 {ticker} 매매 전략 실행 결과 - signal: {signal}, message: {message}")

        except Exception as e:
            logger.error(f"🚨 {ticker} 매매 전략 실행 중 오류 발생: {e}", exc_info=True)
            continue

        # ✅ **쿨다운 적용 (30초 내 재매매 금지)**
        last_trade_time = last_trade_times.get(ticker, 0)
        if time.time() - last_trade_time < COOLDOWN_TIME:
            logger.info(f"⚠️ {ticker} 최근 거래 이후 {COOLDOWN_TIME}초 내 재매매 금지.")
            continue

        # ✅ **매수 로직 (시장가 매수)**
        if signal == "buy":
            invest_amount = min(available_krw * INVEST_RATIO, MAX_INVEST_AMOUNT)
            if invest_amount >= MIN_ORDER_AMOUNT:
                trade_result = buy_market(f"KRW-{ticker}", invest_amount)
                if trade_result and "uuid" in trade_result:
                    order_uuid = trade_result["uuid"]
                    last_trade_times[ticker] = time.time()

                    # ✅ 주문 상태 확인 추가
                    order_status = check_order_status(order_uuid)
                    logger.info(f"📌 {ticker} 주문 상태 확인: {order_status.get('state', '확인 불가')}")

                    # ✅ 미체결 주문 확인 및 자동 취소
                    cancel_old_orders(f"KRW-{ticker}", MAX_WAIT_TIME)

        # ✅ **매도 로직 (시장가 매도)**
        if signal == "sell":
            trade_result = sell_market(f"KRW-{ticker}", position.get(ticker, {}).get("balance", 0)) ###
            if trade_result and "uuid" in trade_result:
                order_uuid = trade_result["uuid"]
                last_trade_times[ticker] = time.time()

                logger.info(f"✅ {ticker} 매도 주문 완료 - 주문 UUID: {order_uuid}")

                # ✅ 미체결 주문 확인 및 자동 취소
                cancel_old_orders(f"KRW-{ticker}", MAX_WAIT_TIME)

                # ✅ 주문 상태 확인 (옵션)
                order_status = check_order_status(order_uuid)
                logger.info(f"📌 {ticker} 매도 주문 상태: {order_status.get('state', '확인 불가')}")

                # ✅ **매도 후 최신 보유 자산 다시 조회**
                my_balance = get_my_exchange_account()  # 🔥 매도 후 최신 잔고 업데이트
                position = my_balance.get("assets", {})  # 최신 자산 반영

                # ✅ DEBUG 로그 추가
                logger.debug(f"🔄 최신 position 데이터: {position}")

            else:
                logger.warning(f"🚨 {ticker} 매도 주문 실패 - API 응답 오류: {trade_result}")



scheduler = BackgroundScheduler()
scheduler.add_job(execute_trade, 'interval', seconds=10, max_instances=4)
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
