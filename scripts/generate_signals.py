import json
import math
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path


ASSETS = (
    ("PETR4", "Petrobras PN"),
    ("VALE3", "Vale ON"),
    ("ITUB4", "Itaú Unibanco PN"),
    ("MGLU3", "Magazine Luiza ON"),
)
BUDGETS = (20, 30, 50, 100)


def fetch_history(ticker: str, period: str, interval: str) -> list[dict]:
    url = f"https://brapi.dev/api/quote/{ticker}?range={period}&interval={interval}&fundamental=false"
    request = urllib.request.Request(url, headers={"User-Agent": "Scanner-B3-iPad/1.0"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.load(response)
            return [row for row in payload["results"][0]["historicalDataPrice"] if row.get("close")]
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2**attempt)
    return []


def ema_series(values: list[float], period: int) -> list[float]:
    factor = 2 / (period + 1)
    result = [values[0]]
    for value in values[1:]:
        result.append(value * factor + result[-1] * (1 - factor))
    return result


def macd_12_26_9(closes: list[float]) -> tuple[float, float, float]:
    fast = ema_series(closes, 12)
    slow = ema_series(closes, 26)
    line = [a - b for a, b in zip(fast, slow)]
    signal = ema_series(line, 9)
    return line[-1], signal[-1], line[-1] - signal[-1]


def sma(values: list[float], period: int) -> float:
    sample = values[-period:]
    return sum(sample) / len(sample)


def stochastic_14_3_3(candles: list[dict]) -> tuple[float, float]:
    raw = []
    for index in range(13, len(candles)):
        window = candles[index - 13 : index + 1]
        high = max(row["high"] for row in window)
        low = min(row["low"] for row in window)
        raw.append(50 if high == low else (candles[index]["close"] - low) / (high - low) * 100)
    smooth_k_series = [sma(raw[: index + 1], min(3, index + 1)) for index in range(len(raw))]
    return smooth_k_series[-1], sma(smooth_k_series, 3)


def evaluate(ticker: str, company: str, daily: list[dict], hourly: list[dict]) -> dict:
    closes = [row["close"] for row in daily]
    hourly_closes = [row["close"] for row in hourly]
    price = closes[-1]
    ema9 = ema_series(closes, 9)[-1]
    ema21_series = ema_series(closes, 21)
    ema21 = ema21_series[-1]
    daily_line, daily_signal, daily_hist = macd_12_26_9(closes)
    hour_line, hour_signal, hour_hist = macd_12_26_9(hourly_closes)
    stoch_k, stoch_d = stochastic_14_3_3(hourly)
    volumes = [row.get("volume") or 0 for row in daily[-21:-1]]
    average_volume = sum(volumes) / max(1, len(volumes))
    volume_ratio = (daily[-1].get("volume") or average_volume) / max(1, average_volume)

    daily_up = price > ema21 and ema9 > ema21 and ema21 > ema21_series[-4]
    daily_down = price < ema21 and ema9 < ema21 and ema21 < ema21_series[-4]
    daily_macd_buy = daily_line > daily_signal and daily_hist > 0
    daily_macd_sell = daily_line < daily_signal and daily_hist < 0
    hourly_buy = hour_line > hour_signal and hour_hist > 0 and stoch_k > stoch_d and stoch_k < 85
    hourly_sell = hour_line < hour_signal and hour_hist < 0 and stoch_k < stoch_d and stoch_k > 15
    volume_confirms = volume_ratio >= 1.10

    if daily_up and daily_macd_buy and hourly_buy:
        signal, score = "COMPRA", 5 + int(volume_confirms)
    elif daily_down and daily_macd_sell and hourly_sell:
        signal, score = "VENDA", -(5 + int(volume_confirms))
    else:
        signal = "AGUARDAR"
        score = (int(daily_up) + int(daily_macd_buy) + int(hourly_buy)) - (int(daily_down) + int(daily_macd_sell) + int(hourly_sell))

    changes = [abs(closes[i] / closes[i - 1] - 1) for i in range(max(1, len(closes) - 20), len(closes))]
    volatility = sum(changes) / max(1, len(changes))
    risk = max(0.025, min(0.08, volatility * 2.4))
    direction = -1 if signal == "VENDA" else 1
    trend_return = price / closes[-21] - 1
    reasons = [
        f"Tendência diária: {'positiva' if daily_up else 'negativa' if daily_down else 'lateral'}",
        f"MACD diário 12,26,9: {'comprador' if daily_macd_buy else 'vendedor' if daily_macd_sell else 'neutro'}",
        f"Volume: {volume_ratio:.2f}x a média de 20 períodos",
        f"60 min: {'confirma compra' if hourly_buy else 'confirma venda' if hourly_sell else 'não confirma entrada'}",
    ]
    return {
        "ticker": ticker,
        "execution_ticker": f"{ticker}F",
        "company": company,
        "price": round(price, 2),
        "signal": signal,
        "score": score,
        "confidence": min(90, 55 + abs(score) * 6),
        "stop": round(price * (1 - direction * risk), 2),
        "target": round(price * (1 + direction * risk * 2), 2),
        "fair_value": round(price * (1 + max(-0.12, min(0.18, trend_return * 2))), 2),
        "risk_percent": round(risk * 100, 1),
        "quantities": {str(value): math.floor(value / price) for value in BUDGETS},
        "reasons": reasons,
        "indicators": {
            "daily_macd": round(daily_line, 4), "daily_signal": round(daily_signal, 4), "daily_histogram": round(daily_hist, 4),
            "hourly_macd": round(hour_line, 4), "hourly_signal": round(hour_signal, 4), "hourly_histogram": round(hour_hist, 4),
            "stochastic_k": round(stoch_k, 1), "stochastic_d": round(stoch_d, 1), "volume_ratio": round(volume_ratio, 2),
        },
        "timeframe": "Diário + confirmação 60 min",
        "data_status": "DADOS ONLINE",
    }


items = []
errors = []
for symbol, name in ASSETS:
    try:
        items.append(evaluate(symbol, name, fetch_history(symbol, "3mo", "1d"), fetch_history(symbol, "5d", "1h")))
    except Exception as error:
        errors.append(f"{symbol}: {error}")
items.sort(key=lambda row: (row["signal"] != "COMPRA", -row["score"]))
payload = {"mode": "PRODUÇÃO ASSISTIDA", "data_status": "DADOS ONLINE", "updated_at": datetime.now(UTC).isoformat(), "items": items, "errors": errors}
Path("signals.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
if not items:
    raise SystemExit("Nenhum ativo pôde ser atualizado")
