import os
import smtplib
import sqlite3
import ssl
from datetime import datetime
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import matplotlib.pyplot as plt
import pandas as pd
from dotenv import load_dotenv
from matplotlib import rc

# 환경 변수 로드
load_dotenv()

SMTP_SSL_PORT = 465
SMTP_SERVER = 'smtp.gmail.com'
SENDER_EMAIL = os.getenv('SENDER_EMAIL', '')
SENDER_PASSWORD = os.getenv('SENDER_PASSWORD', '')
RECEIVER_EMAIL = os.getenv('RECEIVER_EMAIL', '')

# 한글 폰트 설정 (Malgun Gothic 사용)
rc('font', family='Malgun Gothic')


def send_email_with_attachment(subject, body, attachment_paths):
    msg = MIMEMultipart()
    msg['From'] = SENDER_EMAIL
    msg['To'] = RECEIVER_EMAIL
    msg['Subject'] = subject

    msg.attach(MIMEText(body, 'plain'))

    for attachment_path in attachment_paths:
        with open(attachment_path, 'rb') as f:
            part = MIMEBase('application', 'octet-stream')
            part.set_payload(f.read())
            encoders.encode_base64(part)
            part.add_header('Content-Disposition', f'attachment; filename="{os.path.basename(attachment_path)}"')
            msg.attach(part)

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(SMTP_SERVER, SMTP_SSL_PORT, context=context) as server:
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.sendmail(SENDER_EMAIL, RECEIVER_EMAIL, msg.as_string())

def generate_daily_report():
    today = datetime.now().strftime("%Y-%m-%d")
    db_path = "trading.db"

    with sqlite3.connect(db_path) as conn:
        query = "SELECT * FROM trade_logs WHERE DATE(timestamp) = DATE('now', 'localtime')"
        df = pd.read_sql_query(query, conn)

    if df.empty:
        print("❌ 오늘 거래 내역이 없습니다.")
        return

    total_profit = df["profit_rate"].dropna().sum()
    avg_profit = df["profit_rate"].dropna().mean()
    trade_count = len(df)
    tickers = df["ticker"].nunique()

    grouped = df.groupby("ticker")["profit_rate"].mean().reset_index()
    grouped.columns = ["티커", "평균 수익률"]

    # ✅ 수익률 그래프 생성
    plt.figure(figsize=(8, 4))
    plt.bar(grouped["티커"], grouped["평균 수익률"], color='skyblue')
    plt.title("티커별 평균 수익률")
    plt.xlabel("티커")
    plt.ylabel("수익률 (%)")
    plt.grid(True)
    graph_path = f"logs/graph_{today}.png"
    plt.savefig(graph_path)
    plt.close()

    summary = pd.DataFrame({
        "날짜": [today],
        "총 수익률": [f"{total_profit:.2f}%"],
        "평균 수익률": [f"{avg_profit:.2f}%"],
        "매매 횟수": [trade_count],
        "거래 티커 수": [tickers],
    })

    report_path = f"logs/report_{today}.xlsx"
    with pd.ExcelWriter(report_path) as writer:
        summary.to_excel(writer, sheet_name="요약", index=False)
        grouped.to_excel(writer, sheet_name="티커별 요약", index=False)
        df.to_excel(writer, sheet_name="상세 매매 내역", index=False)

    print(f"✅ 리포트 저장 완료: {report_path}")

    # 이메일로 엑셀 파일과 그래프 파일을 모두 첨부
    send_email_with_attachment(
        subject="📈 자동매매 일일 리포트",
        body="오늘의 리포트와 티커별 수익률 그래프를 첨부합니다.",
        attachment_paths=[report_path, graph_path]
    )
    print("✅ 이메일 발송 완료")

if __name__ == "__main__":
    generate_daily_report()
