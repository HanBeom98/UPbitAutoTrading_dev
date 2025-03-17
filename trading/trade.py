import requests
import time
import jwt
import uuid
import hashlib
import os
import numpy as np
#from main import logger # 지정가 매매 할떄 주석 해제
from urllib.parse import urlencode, unquote
from dotenv import load_dotenv


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

    query_string = unquote(urlencode(query_params, doseq=True)).encode("utf-8")
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
    """📌 시장가 매수 주문"""
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
    return validate_response(response)

def sell_market(market: str, volume: float) -> dict:
    """📌 시장가 매도 주문"""
    if not market or volume is None or np.isnan(volume) or np.isinf(volume) or volume <= 0:
        print(f"🚨 {market} 시장가 매도 주문 실패: volume({volume})이 유효하지 않습니다.")
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
    """📌 주문 상태 확인 후 체결된 가격 가져오기"""
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

            data = response.json()

            if "trades" in data and data["trades"]:  # ✅ 체결된 거래 내역이 있을 경우
                executed_price = float(data["trades"][0]["price"])  # ✅ 체결 가격 가져오기
                return {"uuid": uuid, "price": executed_price}

            return data  # ✅ 정상 응답 시 반환

        except requests.exceptions.RequestException as e:
            print(f"🚨 {uuid} 주문 상태 조회 요청 실패: {e}")

    print(f"🚨 {uuid} 주문 상태 조회 3회 실패!")
    return {}


def check_order_status(order_uuid, max_retries=5, wait_time=1):
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
            order_timestamp = time.mktime(time.strptime(created_at, "%Y-%m-%dT%H:%M:%S.%f"))  # ✅ 밀리초까지 처리
        except ValueError:
            order_timestamp = time.mktime(time.strptime(created_at, "%Y-%m-%dT%H:%M:%S"))  # ✅ 밀리초 없는 경우

        if current_time - order_timestamp > max_wait_time:

            cancel_result = cancel_order(order_uuid)

            # ✅ 주문 취소 결과 로그 추가
            if cancel_result.get("state") == "cancel":
                logger.info(f"✅ {market} 미체결 주문 취소 완료 - 주문 UUID: {order_uuid}")
            else:
                logger.warning(f"⚠️ {market} 미체결 주문 취소 실패 - 주문 UUID: {order_uuid}")


def cancel_order(order_uuid):
    """📌 미체결 주문 취소"""
    if not order_uuid:
        print("🚨 주문 UUID가 제공되지 않았습니다.")
        return {}

    params = {"uuid": order_uuid}
    headers = generate_auth_headers(params)
    response = requests.delete(ORDER_STATUS_URL, params=params, headers=headers)
    return validate_response(response)

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
            response = requests.post(BASE_URL, json=params, headers=headers)

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

def get_current_price(market: str) -> float:
    """📌 현재가 조회 (업비트 Ticker API)"""
    try:
        params = {"markets": market}
        response = requests.get(TICKER_URL, params=params, timeout=3)
        response.raise_for_status()
        data = response.json()
        return float(data[0]["trade_price"])
    except requests.exceptions.RequestException as e:
        print(f"🚨 현재가 조회 오류: {e}")
        return 1.0  # ✅ None 대신 기본값 반환 (ZeroDivisionError 방지)

def get_min_trade_volume(market: str) -> float:
    """📌 최소 거래 수량 계산 (Rate Limit 처리 추가)"""
    max_retries = 3  # 최대 3회 재시도
    for attempt in range(max_retries):
        try:
            # ✅ 현재가 조회 (1.0 이상의 값이 보장됨)
            trade_price = get_current_price(market)

            # ✅ 혹시라도 1.0 미만 값이 나오면 안전한 기본값 사용
            if trade_price <= 0:
                print(f"⚠️ {market} 현재가 조회 실패 또는 0 이하 값 반환. 기본값 사용.")
                return 0.01  # 기본값 설정 (API 오류 시)

            # 최소 거래 금액 조회
            params = {"market": market}
            headers = generate_auth_headers(params)
            response = requests.get(ORDERS_CHANCE_URL, params=params, headers=headers)

            if response.status_code == 429:  # 요청이 너무 많을 경우
                print(f"⚠️ 요청이 너무 많음! {attempt + 1}/{max_retries}회 재시도 중...")
                time.sleep(1)  # 1초 대기 후 재시도
                continue  # 다음 루프로 이동

            response.raise_for_status()
            data = response.json()

            # ✅ KeyError 방지 및 최소 거래 금액 기본값 보장
            min_total = float(data.get("market", {}).get("bid", {}).get("min_total", 5000.0))

            # ✅ 최소 거래 금액이 0 이하라면 기본값으로 설정
            if min_total <= 0:
                print(f"⚠️ API 응답 이상: 최소 거래 금액이 0 이하. 기본값(5000.0) 사용")
                min_total = 5000.0  # 기본값 설정

            # 최소 거래 수량 계산
            min_trade_volume = min_total / trade_price
            return max(min_trade_volume, 0.01)

        except requests.exceptions.RequestException as e:
            print(f"🚨 업비트 API 오류: {e}")

    return 0.01  # 기본값 설정 (API 오류 시)

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

