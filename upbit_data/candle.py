import requests
import pandas as pd
import logging
from tenacity import retry, stop_after_attempt, wait_fixed

headers = {"Accept": "application/json"}
logger = logging.getLogger(__name__)

# 🔹 ✅ **재시도 로직 추가**
@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))  # 3번 재시도, 2초 대기
def fetch_candle_data(url, params):
    """API 요청을 보내고 JSON 데이터를 반환 (실패 시 자동 재시도)"""
    response = requests.get(url, params=params, headers=headers)
    response.raise_for_status()
    try:
        return response.json()
    except requests.exceptions.JSONDecodeError:
        logger.error(f"🚨 JSON 디코딩 오류 발생 - 응답: {response.text}")
        return []

def get_min_candle_data(market: str, minutes: list):
    """
    특정 종목의 여러 분봉 데이터를 가져와 Dictionary로 반환

    :param market: 조회할 시장 (예: "KRW-BTC")
    :param minutes: 조회할 분봉 리스트 (예: [5, 15])
    :return: 각 분봉 데이터를 담은 dictionary (key: minute, value: DataFrame)
    """
    candle_data_by_minute = {}

    for minute in minutes:  # ✅ 각각의 분봉에 대해 별도 요청
        candle_min_url = f'https://api.upbit.com/v1/candles/minutes/{minute}'
        candle_all_data = None
        last_time = None

        for i in range(5):  # 최신 5 * 200개의 데이터 조회
            candle_min_params = {"market": market, "count": 200}
            if i > 0:
                candle_min_params["to"] = last_time  # 마지막 데이터의 시간을 기준으로 가져옴

            try:
                json_data = fetch_candle_data(candle_min_url, candle_min_params)  # ✅ 재시도 적용된 API 요청

                if not json_data:
                    logger.warning(f"[WARNING] {market} {minute}분봉 API 응답이 빈 리스트 []")
                    continue

                candle_min_data = pd.DataFrame(json_data)

                if candle_min_data.empty:
                    logger.warning(f"[WARNING] {market} {minute}분봉 데이터가 비어 있음.")
                    continue

                candle_min_data['datetime'] = pd.to_datetime(
                    candle_min_data['candle_date_time_kst'], format="%Y-%m-%dT%H:%M:%S", errors="coerce"
                )

                candle_min_data.rename(columns={
                    'opening_price': 'open',
                    'trade_price': 'close',
                    'high_price': 'high',
                    'low_price': 'low',
                    'candle_acc_trade_volume': 'volume'
                }, inplace=True)

                candle_min_data.drop(columns=['candle_date_time_utc', 'candle_date_time_kst', 'timestamp'], inplace=True)

                last_time = candle_min_data['datetime'].iloc[-1].strftime('%Y-%m-%dT%H:%M:%S')

                candle_all_data = (pd.concat([candle_all_data, candle_min_data], ignore_index=True)
                                   if candle_all_data is not None else candle_min_data)

            except requests.exceptions.RequestException as e:
                logger.error(f"[ERROR] {market} {minute}분봉 데이터를 가져오는 중 오류 발생: {e}")
                break

        if candle_all_data is not None and not candle_all_data.empty:
            candle_all_data = candle_all_data.sort_values(by='datetime').drop_duplicates(subset=['datetime'], keep='last')
            candle_data_by_minute[minute] = candle_all_data
        else:
            logger.error(f"[ERROR] {market} {minute}분봉 데이터가 없습니다.")

    return candle_data_by_minute  # ✅ 분봉별 DataFrame을 담은 Dictionary 반환
