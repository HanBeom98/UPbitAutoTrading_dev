import pandas as pd
from datetime import datetime, timedelta
from collections import defaultdict

# 실시간 가격 저장 버퍼
price_buffer = defaultdict(list)  # {ticker: [{"price": 1234, "volume": 1.2, "timestamp": datetime}]}
current_candle = {}

def update_price_buffer(ticker: str, price: float, volume: float):
  now = datetime.now()
  price_buffer[ticker].append({
    "price": price,
    "volume": volume,
    "timestamp": now
  })

def build_1m_candle(ticker: str) -> pd.DataFrame:
  now = datetime.now()
  one_min_ago = now - timedelta(minutes=1)

  recent_trades = [p for p in price_buffer[ticker] if p["timestamp"] >= one_min_ago]

  if not recent_trades:
    return pd.DataFrame()

  df = pd.DataFrame(recent_trades)

  open_price = df["price"].iloc[0]
  high_price = df["price"].max()
  low_price = df["price"].min()
  close_price = df["price"].iloc[-1]
  volume_sum = df["volume"].sum()

  candle = pd.DataFrame([{
    "datetime": now.replace(second=0, microsecond=0),
    "open": open_price,
    "high": high_price,
    "low": low_price,
    "close": close_price,
    "volume": volume_sum
  }])

  # 업데이트 후 2분 전 데이터는 삭제
  price_buffer[ticker] = [p for p in price_buffer[ticker] if p["timestamp"] >= one_min_ago]

  return candle
