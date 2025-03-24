import requests

import time
from account.my_account import get_my_exchange_account
from trading.trade import generate_auth_headers, ORDERS_CHANCE_URL
from utils.constants import TICKER_URL
from utils.constants import ORDERS_CHANCE_URL


def get_total_balance(account_data=None) -> float:
  """💰 내 전체 자산 (현금 + 보유 코인 평가금액) 계산"""
  if account_data is None:
    account_data = get_my_exchange_account()

  total_balance = 0.0

  if not account_data or "assets" not in account_data:
    return total_balance

  for symbol, info in account_data["assets"].items():
    balance = float(info.get("balance", 0))
    if symbol == "KRW":
      total_balance += balance
    else:
      market = f"KRW-{symbol}"
      current_price = get_current_price(market)
      total_balance += balance * current_price

  return total_balance

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