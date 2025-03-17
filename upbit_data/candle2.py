import requests
import pandas as pd
import logging

headers = {"Accept": "application/json"}

logger = logging.getLogger(__name__)

def get_min_candle2_data(market: str, minute: int, count: int = 200):
  """ 특정 종목의 5분봉 데이터를 가져와 정리된 DataFrame으로 반환 (페이징 처리 추가) """

  candle_min_url = f'https://api.upbit.com/v1/candles/minutes/{minute}'
  candle_all_data = None
  last_time = None  # 가장 마지막 캔들의 시간 (페이징 처리)

  for i in range(5):  # 최대 5번 요청하여 과거 데이터까지 가져오기
    candle_min_params = {"market": market, "count": count}
    if i > 0:
      candle_min_params["to"] = last_time  # 이전 요청 데이터의 마지막 시간을 기준으로 추가 요청

    try:
      response = requests.get(candle_min_url, params=candle_min_params, headers=headers)
      response.raise_for_status()

      if not response.text:
        logger.warning(f"[WARNING] {market} API 응답이 비어 있음")
        continue

      json_data = response.json()
      if not json_data:
        logger.warning(f"[WARNING] {market} API 응답이 빈 리스트 []")
        continue

      candle_min_data = pd.DataFrame(json_data)

      if candle_min_data.empty:
        logger.warning(f"[WARNING] {market} 캔들 데이터가 비어 있음.")
        continue

      candle_min_data['datetime'] = pd.to_datetime(candle_min_data['candle_date_time_kst'])

      candle_min_data.rename(columns={
        'opening_price': 'open',
        'trade_price': 'close',
        'high_price': 'high',
        'low_price': 'low',
        'candle_acc_trade_volume': 'volume'
      }, inplace=True)

      # 최신 데이터를 기준으로 페이징을 위한 시간 설정
      last_time = candle_min_data['datetime'].iloc[-1].strftime('%Y-%m-%dT%H:%M:%S')

      # 여러 요청 데이터를 하나의 DataFrame으로 합치기
      candle_all_data = (pd.concat([candle_all_data, candle_min_data], ignore_index=True)
                         if candle_all_data is not None else candle_min_data)

    except requests.exceptions.RequestException as e:
      logger.error(f"[ERROR] {market} 캔들 데이터를 가져오는 중 오류 발생: {e}")
      break

  if candle_all_data is not None and not candle_all_data.empty:
    candle_all_data = candle_all_data.sort_values(by='datetime').drop_duplicates(subset=['datetime'], keep='last')
  else:
    logger.error(f"[ERROR] {market} 캔들 데이터가 없습니다.")

  return candle_all_data
