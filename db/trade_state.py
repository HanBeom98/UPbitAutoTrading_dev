from db.session import SessionLocal
from db.models import TradeStatus

# ✅ 상태 불러오기
def load_trade_status(ticker: str):
  session = SessionLocal()
  try:
    return session.query(TradeStatus).filter_by(ticker=ticker).first()
  finally:
    session.close()

# ✅ 상태 저장 또는 업데이트
def save_trade_status(ticker: str, **kwargs):
  session = SessionLocal()
  try:
    status = session.query(TradeStatus).filter_by(ticker=ticker).first()
    if not status:
      status = TradeStatus(ticker=ticker)
      session.add(status)

    for key, value in kwargs.items():
      if hasattr(status, key):
        setattr(status, key, value)

    session.commit()
  except Exception as e:
    session.rollback()
    print(f"🚨 TradeStatus 저장 실패: {e}")
  finally:
    session.close()



