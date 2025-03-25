# auto_export.py

from apscheduler.schedulers.background import BackgroundScheduler
from export_logs import export_logs_to_excel
from generate_report import generate_daily_report
import time
import logging

# 로그 설정 (옵션)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")

def start_scheduler():
  scheduler = BackgroundScheduler()

  # ✅ 두 가지 작업 모두 예약
  scheduler.add_job(export_logs_to_excel, 'cron', hour=19, minute=00)
  scheduler.add_job(generate_daily_report, 'cron', hour=19, minute=00)

  scheduler.start()
  logging.info("📅 자동 로그 및 리포트 추출 스케줄러 시작됨 (매일 19:00)")

  try:
    while True:
      time.sleep(1)
  except (KeyboardInterrupt, SystemExit):
    logging.info("⏹️ 자동 추출 스케줄러 종료 중...")
    scheduler.shutdown()
