# settings.py

# 자동매매 대상 코인 목록
TRADE_TICKERS = ['AVAX', 'PENDLE', 'SUI', 'XRP', 'SOL', 'ATOM']

# 매매 조건 설정
MAX_TOTAL_INVEST = 1500000     # 전체 투자금 한도
MAX_INVEST_AMOUNT = 300000      # 코인당 최대 투자 금액
MIN_ORDER_AMOUNT = 5000         # 최소 주문 금액
MAX_INVEST_PER_TICKER_RATIO = 0.3
#COOLDOWN_TIME = 300             # 쿨다운 시간 (초)
