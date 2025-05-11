from sqlalchemy import Column, Integer, String, Float, DateTime
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime

Base = declarative_base()

class TradeLog(Base):
    __tablename__ = 'trade_logs'

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    ticker = Column(String(10))
    strategy = Column(String(50))
    signal = Column(String(20))
    buy_price = Column(Float, nullable=True)
    sell_price = Column(Float, nullable=True)
    profit_rate = Column(Float, nullable=True)
    daily_profit = Column(Float, nullable=True)
    message = Column(String(255), nullable=True)

# ✅ TradeStatus 모델 추가
class TradeStatus(Base):
    __tablename__ = "trade_status"

    ticker = Column(String, primary_key=True)  # 티커명 (예: BTC)
    buy_price = Column(Float, nullable=True)  # 매수가
    partial_sell_count = Column(Integer, default=0)  # 부분 익절 횟수
    last_partial_sell_time = Column(DateTime, nullable=True)  # 마지막 부분 익절 시간
    consecutive_losses = Column(Integer, default=0)  # 손절 횟수
    last_sell_time = Column(DateTime, nullable=True)  # 마지막 손절 시간
    peak_price = Column(Float, nullable=True)  # ✅ 최고가 저장 (트레일링 스탑용)

    entry_reason = Column(String, nullable=True)  # 📌 매수 이유
    sell_reason = Column(String, nullable=True)   # 📌 매도 이유
