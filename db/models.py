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