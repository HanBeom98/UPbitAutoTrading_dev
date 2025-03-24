

# 전제: df_1m, df_5m, df_15m은 DataFrame이며 close 컬럼을 포함
def trading_strategy(df_1m, df_5m, df_15m, df_orderbook, position, ticker, buy_price, fee_rate):
    latest_close = df_5m["close"].iloc[-1]
    rsi = df_5m["close"].diff().gt(0).rolling(14).mean() / df_5m["close"].diff().abs().rolling(14).mean()
    rsi = 100 - (100 / (1 + rsi))
    rsi_val = rsi.iloc[-1]

    bb_mid = df_5m["close"].rolling(20).mean()
    bb_std = df_5m["close"].rolling(20).std()
    bb_lower = bb_mid - 2 * bb_std
    bb_signal = latest_close < bb_lower.iloc[-1]

    if position == 0:
        if bb_signal and rsi_val < 40:
            print(f"✅ {ticker} 매수 조건 충족 - 볼린저 하단 + RSI={rsi_val:.2f}")
            return {"signal": "buy", "message": f"볼린저 하단 + RSI={rsi_val:.2f}"}

    elif position == 1:
        # 고정 익절 / 손절 조건 적용
        take_profit = buy_price * 1.01 * (1 - fee_rate * 2)
        stop_loss = buy_price * 0.985 * (1 - fee_rate * 2)

        if latest_close >= take_profit:
            print(f"🎯 {ticker} 익절 조건 충족 → 현재가: {latest_close:.2f}")
            return {"signal": "sell", "message": "익절 실행"}
        elif latest_close <= stop_loss:
            print(f"🛑 {ticker} 손절 조건 충족 → 현재가: {latest_close:.2f}")
            return {"signal": "sell", "message": "손절 실행"}

    return {"signal": "", "message": ""}
