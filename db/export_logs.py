# export_logs.py

import sqlite3
from datetime import datetime

import pandas as pd

DB_FILE = "trading.db"

def export_logs_to_excel():
  try:
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

#엑셀 파일 추출 하려면 이거 실행