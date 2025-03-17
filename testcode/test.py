import pandas as pd
import numpy as np
from trading.trading_strategy import trading_strategy

# ✅ 가상의 캔들 데이터 생성 (200개, 1분봉 기준)
np.random.seed(42)  # 재현성을 위한 랜덤 시드 설정
initial_price = 1000  # 초기 가격 (매수가)

# ✅ 캔들 데이터 시뮬레이션 (상승 추세)
close_prices = np.linspace(initial_price, initial_price * 1.02, 200)  # 2% 상승 시뮬레이션
high_prices = close_prices + np.random.uniform(1, 3, 200)  # 랜덤 고가
low_prices = close_prices - np.random.uniform(1, 3, 200)  # 랜덤 저가
volumes = np.random.randint(100, 300, 200)  # 랜덤 거래량

# ✅ DataFrame 생성
df = pd.DataFrame({
  "close": close_prices,
  "high": high_prices,
  "low": low_prices,
  "volume": volumes
})

# ✅ 매수 가격: 1000원, 보유 상태 (position = 1)
buy_price = 1000
position = 1
ticker = "TEST"

# ✅ 매매 전략 실행 (수익 1.5% 이상이면 매도 나오는지 확인)
result = trading_strategy(df, position, ticker, buy_price)

# ✅ 결과 출력
print("📌 테스트 결과:", result)
