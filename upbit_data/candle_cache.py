from collections import defaultdict

# 실시간 + API 기반 분봉 통합 저장
# 예: {"BTC": {"1m": DataFrame, "5m": DataFrame, "15m": DataFrame}}
market_data_cache = defaultdict(dict)

def update_candle_cache(ticker: str, minute: str, df):
  market_data_cache[ticker][minute] = df
