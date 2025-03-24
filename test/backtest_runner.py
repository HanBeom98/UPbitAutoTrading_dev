import pandas as pd
from upbit_data.candle import get_min_candle_data


# 👉 전략 함수 (단순화된 버전)
def trading_strategy(df_1m, df_5m, df_15m, df_orderbook, position, ticker, buy_price, fee_rate):
    latest_close = df_5m["close"].iloc[-1]

    # RSI 계산
    rsi = df_5m["close"].diff().gt(0).rolling(14).mean() / df_5m["close"].diff().abs().rolling(14).mean()
    rsi = 100 - (100 / (1 + rsi))
    rsi_val = rsi.iloc[-1]

    # 볼린저밴드 계산
    bb_mid = df_5m["close"].rolling(20).mean()
    bb_std = df_5m["close"].rolling(20).std()
    bb_lower = bb_mid - 2 * bb_std
    bb_signal = latest_close < bb_lower.iloc[-1]

    if position == 0:
        if bb_signal and rsi_val < 40:
            print(f"✅ {ticker} 매수 조건 충족 - 볼린저 하단 + RSI={rsi_val:.2f}")
            return {"signal": "buy", "message": f"볼린저 하단 + RSI={rsi_val:.2f}"}

    elif position == 1:
        take_profit = buy_price * 1.01 * (1 - fee_rate * 2)
        stop_loss = buy_price * 0.985 * (1 - fee_rate * 2)

        if latest_close >= take_profit:
            print(f"🎯 {ticker} 익절 조건 충족 → 현재가: {latest_close:.2f}")
            return {"signal": "sell", "message": "익절 실행"}
        elif latest_close <= stop_loss:
            print(f"🛑 {ticker} 손절 조건 충족 → 현재가: {latest_close:.2f}")
            return {"signal": "sell", "message": "손절 실행"}

    return {"signal": "", "message": ""}


# 👉 백테스트 실행 함수
def run_backtest(ticker: str, market: str = "KRW-", fee_rate: float = 0.0005):
    print(f"📊 백테스트 시작: {ticker}")
    full_market = f"{market}{ticker}"

    candle_data = get_min_candle_data(full_market, [5, 15])
    df_5m = candle_data.get(5)
    df_15m = candle_data.get(15)

    if df_5m is None or df_15m is None:
        print("❌ 캔들 데이터가 부족합니다.")
        return

    position = 0
    buy_price = None
    results = []

    for i in range(200, len(df_5m)):
        df_5m_slice = df_5m.iloc[i - 200:i]
        df_15m_slice = df_15m.iloc[-100:]

        dummy_orderbook = pd.DataFrame({
            "buy_volume": [100] * 15,
            "sell_volume": [80] * 15,
            "sell_wall": [50] * 15,
        })

        context = trading_strategy(
            df_1m=df_5m_slice[-14:],
            df_5m=df_5m_slice,
            df_15m=df_15m_slice,
            df_orderbook=dummy_orderbook,
            position=position,
            ticker=ticker,
            buy_price=buy_price,
            fee_rate=fee_rate
        )

        signal = context.get("signal", "")
        message = context.get("message", "")

        if signal == "buy" and position == 0:
            buy_price = df_5m_slice["close"].iloc[-1]
            position = 1
            results.append({"type": "BUY", "price": buy_price, "index": i, "msg": message})

        elif signal == "sell" and position == 1:
            sell_price = df_5m_slice["close"].iloc[-1]
            pnl = (sell_price - buy_price) / buy_price * 100
            results.append({"type": "SELL", "price": sell_price, "index": i, "pnl": pnl, "msg": message})
            position = 0
            buy_price = None

    # ✅ 결과 출력
    print("✅ 거래 내역:")
    for trade in results:
        print(trade)

    profits = [t["pnl"] for t in results if t["type"] == "SELL"]
    total_return = sum(profits)
    print(f"📈 총 수익률: {total_return:.2f}%")


# 👉 실행
if __name__ == "__main__":
    run_backtest("AVAX")
