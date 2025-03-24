import sqlite3
from datetime import datetime
import os  # os 모듈을 추가하여 디렉토리 확인 및 생성
import pandas as pd

DB_FILE = "trading.db"

def export_logs_to_excel():
  try:
    # 'logs' 디렉토리가 존재하는지 확인하고, 없으면 생성
    if not os.path.exists("logs"):
      os.makedirs("logs")

    with sqlite3.connect(DB_FILE) as conn:
      df = pd.read_sql_query("SELECT * FROM trade_logs", conn)

    if df.empty:
      print("❌ 추출할 데이터가 없습니다.")
      return

    today = datetime.now().strftime("%Y-%m-%d")
    filename = f"logs/strategy_logs_export_{today}.xlsx"
    df.to_excel(filename, index=False)
    print(f"✅ 로그가 성공적으로 엑셀로 저장되었습니다 → {filename}")
  except Exception as e:
    print(f"🚨 엑셀 저장 중 오류: {e}")

if __name__ == "__main__":
  export_logs_to_excel()
