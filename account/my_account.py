import requests, jwt, uuid, os, time
from dotenv import load_dotenv

load_dotenv()

# ✅ 업비트 API 엔드포인트
UPBIT_ACCOUNT_URL = "https://api.upbit.com/v1/accounts"
UPBIT_ORDER_URL = "https://api.upbit.com/v1/order"

# ✅ 환경 변수에서 API 키 로드
ACCESS_KEY = os.getenv("ACCESS_KEY", "")
SECRET_KEY = os.getenv("SECRET_KEY", "")

if not ACCESS_KEY or not SECRET_KEY:
    raise ValueError("🚨 API 키(ACCESS_KEY, SECRET_KEY)가 설정되지 않았습니다! .env 파일을 확인하세요.")

def generate_headers():
    """JWT 인증 헤더 생성 (SECRET_KEY가 없으면 오류 발생 방지)"""
    if not SECRET_KEY:
        raise ValueError("🚨 SECRET_KEY가 설정되지 않았습니다! .env 파일을 확인하세요.")

    token = jwt.encode({"access_key": ACCESS_KEY, "nonce": str(uuid.uuid4())}, SECRET_KEY)
    return {"Authorization": f"Bearer {token}"}

def check_order_status(order_uuid):
    """주문 UUID를 이용해 체결 여부 확인"""
    response = requests.get(UPBIT_ORDER_URL, params={"uuid": order_uuid}, headers=generate_headers())

    if response.status_code == 200:
        return response.json()  # ✅ 주문 상세 정보 반환
    else:
        print(f"❌ 주문 상태 조회 실패: {response.text}")
        return None

def get_my_exchange_account():
    """내 계좌 조회 (보유 코인 정보 포함)"""
    response = requests.get(UPBIT_ACCOUNT_URL, headers=generate_headers())

    if response.status_code == 403:
        print("🚨 API 접근이 금지되었습니다. API 키를 확인하세요!")
        return None
    if response.status_code == 429:
        print("🚨 요청이 너무 많습니다! 잠시 후 다시 시도하세요.")
        time.sleep(5)
        return get_my_exchange_account()  # 5초 후 재시도
    if response.status_code != 200:
        print(f"🚨 업비트 API 요청 실패: {response.text}")
        return None

    account_data = response.json()

    # ✅ 원화(KRW) 잔고 확인
    krw_account = next((item for item in account_data if item["currency"] == "KRW"), None)
    krw_balance = float(krw_account["balance"]) - float(krw_account["locked"]) if krw_account else 0

    # ✅ 보유 코인 정보 (원화 마켓 코인만 필터링)
    holdings = {}
    for asset in account_data:
        if asset["currency"] == "KRW":  # 원화는 별도로 저장
            continue

        holdings[asset["currency"]] = {
            "balance": float(asset["balance"]),  # 보유 수량
            "locked": float(asset["locked"]),  # 주문 중 묶인 수량
            "avg_buy_price": float(asset["avg_buy_price"]),  # 평균 매수가
        }

    return {"KRW": krw_balance, "assets": holdings}


def get_order_list(limit=10):
    """
    업비트 주문 리스트 조회 API를 사용하여 최근 주문 내역을 가져옴.
    체결된 주문만 필터링하여 반환.
    """
    url = "https://api.upbit.com/v1/orders"
    query = {
        "state": "done",  # 체결된 주문만 조회
        "page": 1,
        "limit": limit,  # 최근 주문 개수 조정 가능 (기본: 10개)
    }

    response = requests.get(url, params=query, headers=generate_headers())

    if response.status_code == 403:
        print("🚨 API 접근이 금지되었습니다. API 키를 확인하세요!")
        return []
    if response.status_code == 429:
        print("🚨 요청이 너무 많습니다! 5초 후 다시 시도합니다.")
        time.sleep(5)
        return get_order_list(limit)  # 5초 후 재시도
    if response.status_code != 200:
        print(f"🚨 주문 리스트 조회 실패! 상태 코드: {response.status_code}, 응답: {response.text}")
        return []

    try:
        orders = response.json()
        if isinstance(orders, list):
            return orders  # ✅ 주문 리스트 반환
        else:
            print("🚨 API 응답이 예상과 다릅니다! 빈 리스트 반환")
            return []
    except Exception as e:
        print(f"🚨 주문 리스트 데이터 파싱 실패: {e}")
        return []

def get_balance(market: str, account_data=None) -> float:
    """📌 특정 마켓(KRW-BTC)에서 보유한 코인 개수 조회 (불필요한 API 호출 방지)"""
    if account_data is None:
        account_data = get_my_exchange_account()
    if not account_data or "assets" not in account_data:
        return 0.0

    # ✅ 마켓명에서 "KRW-" 제거 (BTC, ETH 같은 코인 이름만 남김)
    coin_symbol = market.replace("KRW-", "")

    return float(account_data["assets"].get(coin_symbol, {}).get("balance", 0))


