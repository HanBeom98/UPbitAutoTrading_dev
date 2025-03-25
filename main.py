# UPbitAutoTrading_dev/main.py

import threading
import time

from utils.balance_util import get_total_balance, get_krw_balance
from trading.trading_strategy import trading_context
from websocket_client import run_websocket_client
from trading.trading_strategy import initialize_context_for_ticker

from db.models import Base
from db.session import engine

def init_db():
  Base.metadata.create_all(bind=engine)

if __name__ == "__main__":

  init_db()

  for ticker in ['AVAX', 'PENDLE', 'SUI', 'XRP', 'SOL', 'ATOM']:
    initialize_context_for_ticker(ticker)

  total_balance = get_total_balance()
  krw_balance = get_krw_balance()
  trading_context.total_start_balance = total_balance

  print("📌 거래 시작")
  print(f"📊 총 평가 자산 (현금 + 코인): {total_balance:,.0f}원 → 기준 자산 설정 완료")

  print("🚀 웹소켓 기반 실시간 자동매매 시스템 시작!")
  threading.Thread(target=run_websocket_client, daemon=True).start()

  # 종료되지 않도록 유지
  while True:
    time.sleep(1)
