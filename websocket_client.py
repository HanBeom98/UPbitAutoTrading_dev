# UPbitAutoTrading_dev/websocket_client.py

import json
import time
from datetime import datetime

import websocket

from account.my_account import get_my_exchange_account
from example import market_data_cache, position, process_ticker
from settings import TRADE_TICKERS
from trading.trade import get_orderbook_data
from upbit_data.candle import get_min_candle_data
from upbit_data.candle_builder import update_price_buffer, build_1m_candle
from upbit_data.candle_cache import update_candle_cache


def on_message(ws, message):
  data = json.loads(message)
  code = data.get("code", "")
  price = data.get("trade_price", 0)
  ticker = code.replace("KRW-", "")

  if ticker not in TRADE_TICKERS:
    return

  print(f"[{datetime.now().strftime('%H:%M:%S')}] 실시간 가격 수신: {ticker} = {price}")

  # 🔹 실시간 1분봉 버퍼에 누적
  volume_value = data.get("trade_volume", 0)
  update_price_buffer(ticker, price, volume_value)

  # 🔹 1분마다 실시간 캔들 생성 시도
  now = datetime.now()
  if now.second == 0:
    df_1m_live = build_1m_candle(ticker)
    if not df_1m_live.empty:
      update_candle_cache(ticker, "1m", df_1m_live)

  # 🔹 기존 5m, 15m API 캔들 가져오기
  candle_data = get_min_candle_data(code, [5, 15])
  if candle_data:
    update_candle_cache(ticker, "5m", candle_data.get(5))
    update_candle_cache(ticker, "15m", candle_data.get(15))

  # 1분, 5분, 15분봉 업데이트
  try:
    candle_data = get_min_candle_data(code, [1, 5, 15])
    if candle_data:
      market_data_cache[ticker] = {
        "1m": candle_data.get(1),
        "5m": candle_data.get(5),
        "15m": candle_data.get(15),
      }

      df_orderbook = get_orderbook_data(code)
      if df_orderbook is None or df_orderbook.empty:
        print(f"⚠️ {ticker} 주문장 없음")
        return

      account_data = get_my_exchange_account()
      if not account_data:
        print("🚨 잔고 조회 실패")
        return

      available_krw = account_data.get("KRW", 0)
      position.update(account_data.get("assets", {}))

      # 전략 실행
      process_ticker(ticker, account_data, available_krw)
  except Exception as e:
    print(f"🚨 {ticker} 데이터 처리 중 오류: {e}")

def on_error(ws, error):
  print("🚨 웹소켓 에러:", error)

def on_close(ws, close_status_code, close_msg):
  print("❌ 웹소켓 연결 종료됨")

def on_open(ws):
  payload = [
    {"ticket": "realtime-ticker"},
    {"type": "ticker", "codes": [f"KRW-{ticker}" for ticker in TRADE_TICKERS]},
  ]
  ws.send(json.dumps(payload))

def run_websocket_client():
  while True:
    try:
      ws = websocket.WebSocketApp(
          "wss://api.upbit.com/websocket/v1",
          on_open=on_open,
          on_message=on_message,
          on_error=on_error,
          on_close=on_close
      )
      ws.run_forever()
    except Exception as e:
      print(f"❌ 웹소켓 재연결 시도 중 오류 발생: {e}")
      time.sleep(5)  # 재연결 대기
