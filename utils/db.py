import psycopg2
import logging
from datetime import datetime
from account.my_account import get_my_exchange_account  # ✅ 업비트 API 호출

# ✅ PostgreSQL 연결 정보
DB_CONFIG = {
  "dbname": "coin",
  "user": "postgres",
  "password": "systempass",
  "host": "localhost",  # EC2 사용 시 퍼블릭 IP 입력
  "port": 5432  # PostgreSQL 기본 포트
}

# ✅ 로깅 설정
logger = logging.getLogger(__name__)
if not logger.hasHandlers():
  log_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
  console_handler = logging.StreamHandler()
  console_handler.setFormatter(log_formatter)
  logger.addHandler(console_handler)
  logger.setLevel(logging.INFO)

def get_db_connection():
  """📌 PostgreSQL 데이터베이스 연결"""
  try:
    conn = psycopg2.connect(**DB_CONFIG)
    return conn
  except psycopg2.Error as e:
    logger.error(f"🚨 PostgreSQL 연결 실패: {e}")
    return None

def sync_holdings_with_upbit():
  """📌 업비트 API에서 최신 보유 코인 정보를 가져와 holdings 테이블을 업데이트"""
  conn = get_db_connection()
  if conn is None:
    logger.error("🚨 PostgreSQL 연결 실패! holdings 동기화 불가.")
    return

  try:
    my_balance = get_my_exchange_account()
    logger.info(f"🔍 업비트 API 보유 코인 정보: {my_balance}")  # ✅ API에서 가져온 원본 데이터 확인

    # ✅ API에서 "assets" 키를 정상적으로 가져왔는지 확인
    if not my_balance or "assets" not in my_balance or not isinstance(my_balance["assets"], dict):
      logger.error("🚨 업비트 API에서 보유 코인 데이터를 가져오지 못함.")
      return

    with conn.cursor() as cur:
      for ticker, asset in my_balance["assets"].items():
        try:
          avg_buy_price = float(asset.get("avg_buy_price", 0))  # ✅ 평균 매수가 (값이 없으면 0)
          volume = float(asset.get("balance", 0))  # ✅ 보유량 (값이 없으면 0)

          # ✅ 평균 매수가 또는 보유량이 0이면 업데이트 생략
          if avg_buy_price == 0 or volume == 0:
            logger.warning(f"⚠️ {ticker}: 평균 매수가({avg_buy_price}) 또는 보유량({volume})이 0 → 업데이트 생략")
            continue

          # ✅ holdings 테이블에서 기존 데이터 조회
          cur.execute("SELECT buy_price, volume FROM holdings WHERE ticker = %s", (ticker,))
          result = cur.fetchone()

          if result:
            prev_buy_price, prev_volume = result

            # ✅ 평균 매수가 또는 보유량이 변경되었을 경우만 업데이트
            if round(prev_buy_price, 2) != round(avg_buy_price, 2) or prev_volume != volume:
              cur.execute("""
                                UPDATE holdings
                                SET buy_price = %s, volume = %s, updated_at = CURRENT_TIMESTAMP
                                WHERE ticker = %s
                            """, (avg_buy_price, volume, ticker))
              conn.commit()
              logger.info(f"✅ {ticker} 업데이트됨! 매수가: {prev_buy_price} → {avg_buy_price}, 수량: {prev_volume} → {volume}")
            else:
              logger.info(f"✅ {ticker}: 변경 없음 (매수가: {avg_buy_price}, 수량: {volume})")
          else:
            # ✅ holdings 테이블에 없는 신규 코인 추가
            cur.execute("""
                            INSERT INTO holdings (ticker, buy_price, volume)
                            VALUES (%s, %s, %s)
                        """, (ticker, avg_buy_price, volume))
            conn.commit()
            logger.info(f"✅ {ticker} 신규 추가! 매수가: {avg_buy_price}, 수량: {volume}")

        except Exception as e:
          logger.error(f"🚨 {ticker} 동기화 중 오류 발생: {e}")

  except Exception as e:
    logger.error(f"🚨 holdings 동기화 실패: {e}")
  finally:
    conn.close()

def remove_holdings(ticker, volume):
  """📌 매도 시 보유 코인 수량 감소 또는 제거"""
  conn = get_db_connection()
  if conn is None:
    logger.error("🚨 PostgreSQL 연결 실패! holdings 삭제 불가.")
    return

  try:
    with conn.cursor() as cur:
      cur.execute("SELECT volume FROM holdings WHERE ticker = %s", (ticker,))
      result = cur.fetchone()

      if not result:
        logger.warning(f"⚠️ {ticker} 보유 내역 없음. 삭제 스킵.")
        return

      current_volume = result[0]

      if volume >= current_volume:
        cur.execute("DELETE FROM holdings WHERE ticker = %s", (ticker,))
        conn.commit()
        logger.info(f"❌ {ticker} 전체 매도 완료! 보유 내역 삭제됨.")
      else:
        new_volume = current_volume - volume
        cur.execute("""
                    UPDATE holdings 
                    SET volume = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE ticker = %s
                """, (new_volume, ticker))
        conn.commit()
        logger.info(f"🔄 {ticker} 보유량 감소! 남은 수량: {new_volume}")

  except Exception as e:
    logger.error(f"🚨 PostgreSQL holdings 삭제 실패: {e}")
  finally:
    conn.close()

def save_trade_record(ticker, trade_type, buy_price=None, sell_price=None, volume=None):
  """📌 매매 내역을 trade_history 테이블에 저장"""
  now = datetime.now()

  # ✅ 수익률 계산 (매도 시에만 적용)
  profit_percent = None
  if trade_type == "매도" and buy_price and sell_price:
    profit_percent = round(((sell_price - buy_price) / buy_price) * 100, 2)

  conn = get_db_connection()
  if conn is None:
    logger.error("🚨 PostgreSQL 연결 실패! 데이터 저장 불가.")
    return

  try:
    with conn.cursor() as cur:
      sql = """
                INSERT INTO trade_history (trade_time, ticker, trade_type, buy_price, sell_price, volume, profit_percent)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """
      cur.execute(sql, (now, ticker, trade_type, buy_price, sell_price, volume or 0, profit_percent))
      conn.commit()

    logger.info(f"✅ {ticker} {trade_type} 내역 저장 완료! 매수가: {buy_price}, 매도가: {sell_price}, 수익률: {profit_percent}%")

  except Exception as e:
    logger.error(f"🚨 PostgreSQL 데이터 저장 실패: {e}")
  finally:
    conn.close()
