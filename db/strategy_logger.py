from db.session import SessionLocal
from db.models import TradeLog
from datetime import datetime
from trading.trading_strategy import trading_context

def log_trade_result(ticker, signal, buy_price=None, sell_price=None, profit_rate=None, message=""):
    session = SessionLocal()
    try:
        log = TradeLog(
            timestamp=datetime.now(),
            ticker=ticker,
            strategy="기본전략",
            signal=signal,
            buy_price=buy_price,
            sell_price=sell_price,
            profit_rate=profit_rate,
            daily_profit=getattr(trading_context, "daily_profit", None),
            message=message
        )
        session.add(log)
        session.commit()
    except Exception as e:
        session.rollback()
        print(f"🚨 DB 저장 실패: {e}")
    finally:
        session.close()