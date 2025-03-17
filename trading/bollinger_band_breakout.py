import pandas as pd
from ta.volatility import BollingerBands


def trading_strategy(
    df: pd.DataFrame,
    position: int,
) -> dict:
    """
    코인 트레이딩 전략 함수 - Bollinger Band Breakout

    Args:
        df (pd.DataFrame): 가격 데이터프레임
        position (int): 현재 포지션 (0: 매수 가능, 1: 매도 가능)

    Returns:
        dict: 트레이딩 액션 {'signal': 'buy' | 'sell' | '', 'bull_market': bool, 'message': str}
    """

    # ✅ 필수 컬럼 검증 (datetime 활용)
    required_columns = ['close', 'datetime', 'volume']
    if not all(col in df.columns for col in required_columns):
        raise ValueError(f"DataFrame은 {required_columns} 컬럼을 포함해야 합니다.")

    # ✅ 최소 200개 데이터 필요
    if len(df) < 200:
        print('데이터가 부족합니다 (최소 200개 필요).')
        return {"signal": "", "bull_market": False, "message": ""}

    # ✅ EMA 계산
    df['EMA50'] = df['close'].ewm(span=50, adjust=False).mean()
    df['EMA200'] = df['close'].ewm(span=200, adjust=False).mean()

    # ✅ 상승장 판단
    df['EMA_diff'] = df['EMA50'] - df['EMA200']
    is_bull_market = df['EMA_diff'].iloc[-1] > 0  # 상승장 여부

    print(f'[DEBUG] is_bull_market : {is_bull_market}')

    # ✅ 볼린저밴드 계산
    bollinger = BollingerBands(df['close'])
    df['BB_upper'] = bollinger.bollinger_hband()
    df['BB_mid'] = bollinger.bollinger_mavg()
    df['BB_lower'] = bollinger.bollinger_lband()

    # ✅ 최근 20개 캔들 추출
    recent_df = df.tail(20)

    # ✅ 이전 캔들 기준 볼린저밴드 돌파 여부 확인
    prev_candle = recent_df.iloc[-2]
    current_candle = recent_df.iloc[-1]

    bb_lower_breakout = prev_candle['close'] < prev_candle['BB_lower']
    bb_upper_breakout = prev_candle['close'] > prev_candle['BB_upper']

    print(f'[DEBUG] position : {position}')
    print(f'[DEBUG] bb_lower_breakout : {bb_lower_breakout}')
    print(f'[DEBUG] bb_upper_breakout : {bb_upper_breakout}')

    # ✅ 매수 조건 (position == 0)
    if position == 0 and bb_lower_breakout:
        is_recent_positive_candle = current_candle['close'] > current_candle['open']

        if is_recent_positive_candle:
            buy_msg = '이전 캔들이 볼린저밴드 하단 돌파, 현재 캔들이 양봉'
            print(f'[BUY] {buy_msg}')
            return {"signal": "buy", "bull_market": is_bull_market, "message": buy_msg}

    # ✅ 매도 조건 (position == 1)
    elif position == 1 and bb_upper_breakout:
        is_recent_negative_candle = current_candle['close'] < current_candle['open']

        if is_recent_negative_candle:
            sell_msg = '이전 캔들이 볼린저밴드 상단 돌파, 현재 캔들이 음봉'
            print(f'[SELL] {sell_msg}')
            return {"signal": "sell", "bull_market": is_bull_market, "message": sell_msg}

    return {"signal": "", "bull_market": is_bull_market, "message": ""}
