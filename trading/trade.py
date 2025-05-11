import hashlib
import os
import time
import uuid
from datetime import datetime
from typing import Optional
from urllib.parse import urlencode, unquote

import jwt
import numpy as np
import pandas as pd
import requests
from dotenv import load_dotenv

from account.my_account import get_my_exchange_account, get_balance

# ✅ 환경 변수 로드
load_dotenv()

# ✅ API 키 설정
ACCESS_KEY = os.getenv('ACCESS_KEY', '')
SECRET_KEY = os.getenv('SECRET_KEY', '')

if not ACCESS_KEY or not SECRET_KEY:
    raise ValueError("🚨 API 키가 설정되지 않았습니다. .env 파일을 확인하세요.")

BASE_URL = "https://api.upbit.com/v1/orders"
ORDER_STATUS_URL = "https://api.upbit.com/v1/order"  # ✅ 주문 상태 조회 전용 URL
ORDERS_CHANCE_URL = "https://api.upbit.com/v1/orders/chance"  # ✅ 최소 거래 단위 가져오기
TICKER_URL = "https://api.upbit.com/v1/ticker"  # ✅ 현재가 조회용 URL


def generate_auth_headers(query_params=None):
    """📌 Upbit API 호출을 위한 JWT 인증 헤더 생성"""
    if query_params is None:
        query_params = {}

    query_string = unquote(urlencode(query_params, doseq=True)).encode("utf-8") if query_params else b""
    query_hash = hashlib.sha512(query_string).hexdigest()

    payload = {
        "access_key": ACCESS_KEY,
        "nonce": str(uuid.uuid4()),
        "query_hash": query_hash,
        "query_hash_alg": "SHA512"
    }

    jwt_token = jwt.encode(payload, SECRET_KEY)
    return {"Authorization": f"Bearer {jwt_token}"}

def validate_response(response):
    """📌 API 응답 검증 함수: 정상적인 JSON 데이터인지 확인"""
    try:
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, (dict, list)) else {}
    except requests.exceptions.HTTPError as e:
        print(f"🚨 HTTP 오류: {e} | 응답: {response.text}")
    except requests.exceptions.RequestException as e:
        print(f"🚨 API 요청 오류: {e}")
    except Exception as e:
        print(f"🚨 JSON 파싱 오류: {e} | 원본 응답: {response.text}")
        return {}

### 📌 **시장가 주문 함수**
def buy_market(market: str, price: float) -> dict:
    """📌 시장가 매수 주문 + 체결 가격 확인"""
    if not market or price is None or np.isnan(price) or np.isinf(price):
        print(f"🚨 {market} 시장가 매수 주문 실패: 가격({price})이 유효하지 않습니다.")
        return {}

    params = {
        "market": market,
        "side": "bid",
        "ord_type": "price",
        "price": str(price),
    }

    headers = generate_auth_headers(params)
    response = requests.post(BASE_URL, json=params, headers=headers)
    result = validate_response(response)  # ✅ 먼저 응답을 받아 변수에 저장

    # ✅ 평단가 계산 추가
    if result and "uuid" in result:
        uuid = result["uuid"]
        check_order_status(uuid)  # 체결 대기 처리
        avg_price = get_avg_buy_price(uuid)
        if avg_price:
            result["avg_buy_price"] = avg_price

    return result


def sell_market(market: str, volume: float) -> dict:
    """📌 시장가 매도 주문"""
    if not market or volume is None or np.isnan(volume) or np.isinf(volume) or volume <= 0:
        print(f"🚨 {market} 시장가 매도 주문 실패: volume({volume})이 유효하지 않습니다.")
        return {}

    # ✅ 현재 잔고 조회 (잔고 부족 오류 방지)
    my_balance = get_my_exchange_account()
    available_volume = my_balance.get("assets", {}).get(market.replace("KRW-", ""), {}).get("balance", 0)

    if available_volume < volume:
        print(f"🚨 {market} 시장가 매도 주문 실패: 보유량 부족 (보유량: {available_volume}, 주문량: {volume})")
        return {}

    params = {
        "market": market,
        "side": "ask",
        "ord_type": "market",
        "volume": str(volume),
    }

    headers = generate_auth_headers(params)
    response = requests.post(BASE_URL, json=params, headers=headers)
    return validate_response(response)

### 📌 **주문 상태 조회 (최대 3회 재시도)**
def get_order_status(uuid: str) -> dict:
    """📌 주문 상태 확인 후 체결 여부 포함하여 반환"""
    if not uuid:
        print("🚨 주문 UUID가 제공되지 않았습니다.")
        return {}

    params = {"uuid": uuid}
    headers = generate_auth_headers(params)

    for attempt in range(3):  # ✅ 최대 3회 재시도
        try:
            response = requests.get("https://api.upbit.com/v1/order", params=params, headers=headers)

            if response.status_code == 429:
                print(f"⚠️ 요청이 너무 많음! 1초 후 재시도 ({attempt + 1}/3)")
                time.sleep(1)
                continue  # 재시도

            if response.status_code == 200:
                data = response.json()
                executed_volume = float(data.get("executed_volume", 0))
                trades = data.get("trades", [])

                executed_price = None
                if trades and executed_volume > 0:
                    executed_price = float(trades[0].get("price", 0))

                return {
                    "uuid": uuid,
                    "state": data.get("state"),
                    "executed_volume": executed_volume,
                    "price": executed_price,
                    "trades": trades  # ✅ 추가하면 get_avg_buy_price()에서도 사용 가능
                }

            print(f"⚠️ {uuid} 주문은 체결되지 않았습니다.")
            return {
                "uuid": uuid,
                "state": "wait",
                "executed_volume": 0,
                "price": None,
                "trades": []
            }

        except requests.exceptions.RequestException as e:
            print(f"🚨 {uuid} 주문 상태 조회 요청 실패: {e}")

    print(f"🚨 {uuid} 주문 상태 조회 3회 실패!")
    return {}

def check_order_status(order_uuid, max_retries=5, wait_time: float = 1.0):
    """
    ✅ 주문 상태를 최대 `max_retries`번까지 반복해서 확인하는 함수
    - max_retries: 최대 확인 횟수 (기본 5회)
    - wait_time: 각 확인 사이의 대기 시간 (기본 1초)
    """
    for attempt in range(max_retries):
        order_status = get_order_status(order_uuid)  # 기존 함수 사용

        # ✅ 주문이 체결된 경우 바로 반환
        if order_status and "price" in order_status:
            print(f"✅ 주문 체결 완료 - UUID: {order_uuid}, 체결 가격: {order_status['price']}")
            return order_status

        # ✅ 주문이 아직 체결되지 않은 경우 재시도
        print(f"🔄 주문 상태 확인 재시도 {attempt + 1}/{max_retries} - UUID: {order_uuid}")
        time.sleep(wait_time)

    print(f"⚠️ 주문 상태 확인 실패 - UUID: {order_uuid}, 상태 확인 불가")
    return {"state": "확인 불가"}


### 📌 **미체결 주문 조회 및 취소**
def get_open_orders(market: str) -> list:
    """📌 특정 마켓의 미체결 주문 조회"""
    if not market:
        print("🚨 마켓 정보가 제공되지 않았습니다.")
        return []

    params = {"market": market, "state": "wait"}
    headers = generate_auth_headers(params)

    max_retries = 3  # 최대 3회 재시도
    for attempt in range(max_retries):
        try:
            response = requests.get("https://api.upbit.com/v1/orders", params=params, headers=headers)  # ✅ 정확한 URL 적용

            if response.status_code == 429:  # 요청이 너무 많을 경우
                print(f"⚠️ 요청이 너무 많음! {attempt + 1}/{max_retries}회 재시도 중...")
                time.sleep(1)  # 1초 대기 후 재시도
                continue  # 다음 루프 실행

            return validate_response(response)

        except requests.exceptions.RequestException as e:
            print(f"🚨 미체결 주문 조회 실패: {e}")

    return []  # ✅ 최종적으로 3회 실패 시 빈 리스트 반환


def cancel_old_orders(market: str, max_wait_time=30):
    """📌 미체결 주문이 일정 시간 이상 유지되면 자동 취소"""
    open_orders = get_open_orders(market)

    if not open_orders:  # ✅ 주문 조회 실패 또는 없는 경우 로그 출력
        print(f"⚠️ {market} 미체결 주문이 없거나 조회 실패함. 자동 취소 작업 없음.")
        return  # 더 이상 진행할 필요 없음

    current_time = time.time()

    for order in open_orders:
        order_uuid = order["uuid"]
        created_at = order["created_at"]

        try:
            # datetime 객체로 파싱 후, 시간대 정보 제거
            order_timestamp = datetime.fromisoformat(created_at.replace("+09:00", "")).timestamp()
        except ValueError:
            print(f"🚨 {market} 주문 생성 시간 형식 오류: {created_at}")
            continue

        # ✅ 특정 시간 이상 경과한 주문 취소
        if current_time - order_timestamp > max_wait_time:
            cancel_result = cancel_order(order_uuid)

            # ✅ cancel_result가 None이 아닌지 확인하고 처리
            if not cancel_result or cancel_result.get("state") != "cancel":
                print(f"✅ {market} 미체결 주문 취소 완료 - 주문 UUID: {order_uuid}")
                continue  # 취소 실패한 경우 계속 진행하지 않음

            print(f"✅ {market} 미체결 주문 취소 완료 - 주문 UUID: {order_uuid}")

        time.sleep(2)

        for i in range(5):
            open_orders = get_open_orders(market)

            if not open_orders:
                print(f"✅ {market} 모든 미체결 주문이 취소됨.")
                return

            print(f"⚠️ {market} 미체결 주문이 아직 존재! ({i+1}/5) → 추가 확인 진행")
            time.sleep(2)

        # ✅ 마지막까지 취소되지 않은 주문이 있다면 로그 출력
        print(f"🚨 {market} 미체결 주문이 여전히 존재! → 취소 실패 가능성 있음")


def cancel_order(order_uuid, max_retries=3):
    """📌 미체결 주문 취소"""
    if not order_uuid:
        print("🚨 주문 UUID가 제공되지 않았습니다.")
        return {}

    params = {"uuid": order_uuid}
    headers = generate_auth_headers(params)

    for attempt in range(max_retries):
        response = requests.delete(ORDER_STATUS_URL, params=params, headers=headers)
        result = validate_response(response)

        if result and result.get("state") == "cancel":
            print(f"✅ 주문 취소 완료 - UUID: {order_uuid}")
            return result

        print(f"⚠️ 주문 취소 실패! {attempt + 1}/{max_retries} 재시도 중... UUID: {order_uuid}")
        time.sleep(1)

    print(f"🚨 주문 취소 최종 실패! UUID: {order_uuid}")
    return {}

### 📌 **지정가 매수**
def buy_limit(market: str, price: float, volume: float) -> dict:
    """📌 지정가 매수 주문 (코인 개수 기준)"""
    if not market or price <= 0 or volume <= 0:
        print(f"🚨 {market} 지정가 매수 주문 실패: price({price}), volume({volume})가 유효하지 않습니다.")
        return {}

    # ✅ 업비트 호가 단위에 맞춰 가격 조정
    adjusted_price = max(get_tick_size(price), 1)

    params = {
        "market": market,
        "side": "bid",
        "ord_type": "limit",  # ✅ 지정가 주문
        "price": str(adjusted_price),  # ✅ 호가 단위 적용된 가격
        "volume": str(volume),  # 매수할 코인 개수
    }

    headers = generate_auth_headers(params)
    max_retries = 3  # 최대 3회 재시도
    for attempt in range(max_retries):
        try:
            print(f"🛠 {market} 지정가 매수 요청 {attempt + 1}/{max_retries}회 시도 중...")
            print(f"🔹 요청 파라미터: {params}")

            response = requests.post(BASE_URL, json=params, headers=headers)

            print(f"✅ API 응답 코드: {response.status_code}")
            print(f"✅ API 응답 데이터: {response.text}")

            if response.status_code == 429:  # 요청이 너무 많을 경우
                print(f"⚠️ 요청이 너무 많음! {attempt + 1}/{max_retries}회 재시도 중...")
                time.sleep(1)  # 1초 대기 후 재시도
                continue  # 다음 루프 실행

            return validate_response(response)

        except requests.exceptions.RequestException as e:
            print(f"🚨 {market} 지정가 매수 주문 실패: {e}")

    return {}  # ✅ 최종적으로 3회 실패 시 빈 딕셔너리 반환


### 📌 **지정가 매도**
def sell_limit(market: str, price: float, volume: float) -> dict:
    """📌 지정가 매도 주문 (보유한 코인 전량 매도)"""
    if not market or price <= 0:
        print(f"🚨 {market} 지정가 매도 주문 실패: price({price}), volume({volume})이 유효하지 않습니다.")
        return {}

    # ✅ 현재 잔고 확인 (지정가 매도 전에 잔고 부족 오류 방지)
    my_balance = get_my_exchange_account()
    available_volume = float(my_balance.get("assets", {}).get(market.replace("KRW-", ""), {}).get("balance", 0) or 0)

    if available_volume < volume:
        print(f"🚨 {market} 지정가 매도 주문 실패: 보유량 부족 (보유량: {available_volume}, 주문량: {volume})")
        return {}

    # ✅ 업비트 호가 단위에 맞춰 가격 조정
    adjusted_price = max(get_tick_size(price), 1)

    params = {
        "market": market,
        "side": "ask",
        "ord_type": "limit",  # ✅ 지정가 주문
        "price": str(adjusted_price),  # ✅ 호가 단위 적용된 가격
        "volume": str(volume),  # 보유한 모든 코인 개수로 매도
    }

    headers = generate_auth_headers(params)

    max_retries = 3  # 최대 3회 재시도
    for attempt in range(max_retries):
        try:
            response = requests.post(BASE_URL, json=params, headers=headers)

            if response.status_code == 429:  # 요청이 너무 많을 경우
                print(f"⚠️ 요청이 너무 많음! {attempt + 1}/{max_retries}회 재시도 중...")
                time.sleep(1)  # 1초 대기 후 재시도
                continue  # 다음 루프 실행

            return validate_response(response)

        except requests.exceptions.RequestException as e:
            print(f"🚨 {market} 지정가 매도 주문 실패: {e}")

    return {}  # ✅ 최종적으로 3회 실패 시 빈 딕셔너리 반환

def get_tick_size(price):
    """📌 업비트 호가 단위에 맞춰 주문 가격 반올림"""
    if price < 2000:
        return round(price, 0)  # 1원 단위
    elif price < 5000:
        return round(price / 5) * 5  # 5원 단위
    elif price < 10000:
        return round(price / 10) * 10  # 10원 단위
    elif price < 50000:
        return round(price / 50) * 50  # 50원 단위
    elif price < 100000:
        return round(price / 100) * 100  # 100원 단위
    elif price < 500000:
        return round(price / 500) * 500  # 500원 단위
    else:
        return round(price / 1000) * 1000  # 1000원 단위

def calculate_stop_loss_take_profit(buy_price: float, atr: float, fee_rate: float):
    """📌 변동성 기반 손절가(stop_loss) 및 익절가(take_profit) 계산"""

    # ✅ 최소 손절·익절 비율 설정 (변동성이 작을 경우 빠르게 익절·손절)
    min_stop_loss = buy_price * (1 - 0.02)  # 최소 -2% 손절
    min_take_profit = buy_price * (1 + 0.005)  # 최소 +0.5% 익절  / +3% 익절 하고싶으면 0.03 으로 설정

    # ✅ ATR 기본값 설정 (None 방지)
    if atr is None or atr <= 0:
        atr = buy_price * 0.005  # 최소 ATR 기본값 적용

    # ✅ 저가 코인 보정 (5000원 미만이면 더 넓은 손절폭)
    atr_multiplier = 3
    if buy_price < 5000:
        atr_multiplier = 5

    # ✅ 변동성이 작으면 빠르게 손절·익절, 변동성이 크면 넓은 손절·익절 적용
    stop_loss = max(buy_price - (atr * atr_multiplier), min_stop_loss) * (1 - fee_rate)
    take_profit = max(buy_price + (atr * 4), min_take_profit) * (1 - fee_rate)

    # ✅ 수수료 적용
    stop_loss *= (1 - fee_rate * 2)  # 매수 & 매도 수수료 반영
    take_profit *= (1 - fee_rate * 2)  # 매수 & 매도 수수료 반영

    return stop_loss, take_profit

def get_orderbook_data(market: str):
    """📌 업비트 API에서 주문장 데이터를 가져와 DataFrame으로 변환"""
    url = f"https://api.upbit.com/v1/orderbook?markets={market}"
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()[0]  # 첫 번째 마켓 데이터 사용
        orderbook_units = data["orderbook_units"]

        df_orderbook = pd.DataFrame(orderbook_units)

        df_orderbook.rename(columns={"bid_size": "buy_volume", "ask_size": "sell_volume"}, inplace=True)

        df_orderbook["sell_wall"] = df_orderbook["sell_volume"].rolling(5).mean()  # 최근 5개 평균

        df_orderbook["timestamp"] = pd.Timestamp.now()

        return df_orderbook
    except requests.RequestException as e:
        print(f"🚨 주문장 데이터 가져오기 실패: {e}")
        return pd.DataFrame()  # 비어 있는 DataFrame 반환

def get_avg_buy_price(order_uuid: str) -> Optional[float]:
    """📌 UUID 기반 평균 매수가 계산 (미체결이면 None)"""
    order_data = get_order_status(order_uuid)

    trades = order_data.get("trades", [])
    if not trades:
        print(f"⚠️ 체결 내역이 없거나 executed_volume == 0 → UUID: {order_uuid}")
        return None

    total_volume = sum(float(trade["volume"]) for trade in trades)
    total_cost = sum(float(trade["price"]) * float(trade["volume"]) for trade in trades)

    if total_volume == 0:
        return None

    return total_cost / total_volume

def get_avg_buy_price_from_balance(balance_data, ticker):
    """📌 업비트 API에서 평균 매수가(avg_buy_price)를 가져오되, 보유하지 않은 코인은 0으로 반환"""
    asset_info = balance_data.get("assets", {}).get(ticker, {})

    if not asset_info:
        return 0  # 보유하지 않은 경우 0 반환

    return float(asset_info.get("avg_buy_price", 0) or 0)  # 안전한 변환

def calculate_fixed_take_profit(buy_price: float, fee_rate: float):
    """고정 1% 익절가 계산"""
    return buy_price * 1.01 * (1 - fee_rate * 2)

def wait_for_limit_order(order_uuid, max_wait_time=10, interval=1):
    start = time.time()
    last_status = None

    while time.time() - start < max_wait_time:
        status = check_order_status(order_uuid, max_retries=1, wait_time=0.3)
        if status:
            last_status = status
            if status.get("state") == "done":
                print(f"✅ 지정가 체결 완료 - UUID: {order_uuid}")
                return True, status
        print(f"⏳ 지정가 미체결, 대기 중... ({int(time.time() - start)}초 경과)")
        time.sleep(interval)

    print(f"⛔ 지정가 체결 실패 - {max_wait_time}초 초과")
    return False, last_status

def execute_sell_partial(ticker: str, sell_ratio: float):
    balance_data = get_balance(ticker)
    total_amount = float(balance_data.get('balance', 0))

    # 💡 익절할 수량 계산
    amount_to_sell = total_amount * sell_ratio

    if amount_to_sell < 0.0001:  # 최소 주문 단위 체크 (예: BTC)
        print(f"⚠️ {ticker} 부분 익절 수량이 너무 적어 실행 취소: {amount_to_sell:.8f}")
        return

    # 📌 지정가 or 시장가 매도 실행
    sell_market(ticker, amount_to_sell)


# 예: 추가 진입 시 새로운 평단가 계산
def calculate_new_avg_buy_price(prev_price, prev_qty, new_price, new_qty):
    total_cost = (prev_price * prev_qty) + (new_price * new_qty)
    total_qty = prev_qty + new_qty
    return total_cost / total_qty if total_qty > 0 else new_price

def get_current_volume_ratio(ticker: str) -> float:
    """현재 보유량 비율 계산 (0~1)"""
    try:
        balance_data = get_balance(ticker)

        if not balance_data:
            print(f"[WARN] {ticker}의 balance_data가 None입니다.")
            return 0.0

        if isinstance(balance_data, float):
            balance_data = {'balance': balance_data, 'avg_buy_price': 0}

        total_amount = float(balance_data.get('balance', 0))
        avg_buy_price = float(balance_data.get('avg_buy_price', 0))

        krw_balance_data = get_balance("KRW")
        if isinstance(krw_balance_data, float):
            krw_balance = krw_balance_data
        elif krw_balance_data:
            krw_balance = float(krw_balance_data.get('balance', 0))
        else:
            krw_balance = 0.0

        total_valuation = total_amount * avg_buy_price
        total_allocated = total_valuation + krw_balance

        return total_valuation / total_allocated if total_allocated > 0 else 0

    except Exception as e:
        print(f"[ERROR] {ticker} get_current_volume_ratio 계산 실패: {e}")
        return 0.0








