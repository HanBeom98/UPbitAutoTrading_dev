# UPbitAutoTrading_dev/main.py

import threading
import time

from utils.balance_util import get_total_balance
from trading.trading_strategy import trading_context
from websocket_client import run_websocket_client

from db.models import Base
from db.session import engine

def init_db():
  Base.metadata.create_all(bind=engine)

if __name__ == "__main__":

  init_db()

  trading_context.total_start_balance = get_total_balance()
  print(f"📌 거래 시작 - 총 자산 기준금액: {trading_context.total_start_balance:,.0f}원")

  print("🚀 웹소켓 기반 실시간 자동매매 시스템 시작!")
  threading.Thread(target=run_websocket_client, daemon=True).start()

  # 종료되지 않도록 유지
  while True:
    time.sleep(1)
