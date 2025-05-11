import threading
import time

from utils.balance_util import get_total_balance, get_krw_balance
from trading.trading_strategy import trading_context
from websocket_client import run_websocket_client
from settings import TRADE_TICKERS

from db.models import Base
from db.session import engine


def init_db():
  Base.metadata.create_all(bind=engine)


# 🔄 웹소켓이 끊겨도 자동 재연결 시도
def start_websocket():
  while True:
    try:
      run_websocket_client()
    except Exception as e:
      print(f"[❗오류] 웹소켓 종료됨: {e} — 3초 후 재시도...")
      time.sleep(3)


if __name__ == "__main__":
  init_db()

  for ticker in TRADE_TICKERS:
    from trading.trading_strategy import initialize_context_for_ticker
    initialize_context_for_ticker(ticker)

  total_balance = get_total_balance()
  krw_balance = get_krw_balance()
  trading_context.total_start_balance = total_balance

  print("📌 거래 시작")
  print(f"📊 총 평가 자산 (현금 + 코인): {total_balance:,.0f}원 → 기준 자산 설정 완료")

  print("🚀 웹소켓 기반 실시간 자동매매 시스템 시작!")
  threading.Thread(target=start_websocket, daemon=True).start()

  while True:
    time.sleep(1)
