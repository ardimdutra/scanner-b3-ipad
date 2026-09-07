import json
import math
import os
import time
import io
import urllib.request
import zipfile
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path


ASSETS = (
    ("PETR4", "Petrobras PN"),
    ("VALE3", "Vale ON"),
    ("ITUB4", "Itaú Unibanco PN"),
    ("MGLU3", "Magazine Luiza ON"),
)
BUDGETS = (20, 30, 50, 100)
TOKEN = os.environ.get("BRAPI_TOKEN", "").strip()


def fetch_history(ticker: str, period: str, interval: str) -> list[dict]:
    url = f"https://brapi.dev/api/quote/{ticker}?range={period}&interval={interval}&fundamental=false"
    headers = {"User-Agent": "Scanner-B3-iPad/1.0"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    request = urllib.request.Request(url, headers=headers)
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


def evaluate(ticker: str, company: str, daily: list[dict], hourly: list[dict] | None, fundamental: dict | None = None) -> dict:
    hourly = hourly or []
    closes = [row["close"] for row in daily]
    hourly_closes = [row["close"] for row in hourly]
    price = closes[-1]
    ema9 = ema_series(closes, 9)[-1]
    ema21_series = ema_series(closes, 21)
    ema21 = ema21_series[-1]
    daily_line, daily_signal, daily_hist = macd_12_26_9(closes)
    has_hourly = len(hourly) >= 30
    hour_line, hour_signal, hour_hist = macd_12_26_9(hourly_closes) if has_hourly else (0, 0, 0)
    stoch_k, stoch_d = stochastic_14_3_3(hourly) if has_hourly else (0, 0)
    volumes = [row.get("volume") or 0 for row in daily[-21:-1]]
    average_volume = sum(volumes) / max(1, len(volumes))
    volume_ratio = (daily[-1].get("volume") or average_volume) / max(1, average_volume)

    daily_up = price > ema21 and ema9 > ema21 and ema21 > ema21_series[-4]
    daily_down = price < ema21 and ema9 < ema21 and ema21 < ema21_series[-4]
    daily_macd_buy = daily_line > daily_signal and daily_hist > 0
    daily_macd_sell = daily_line < daily_signal and daily_hist < 0
    hourly_buy = has_hourly and hour_line > hour_signal and hour_hist > 0 and stoch_k > stoch_d and stoch_k < 85
    hourly_sell = has_hourly and hour_line < hour_signal and hour_hist < 0 and stoch_k < stoch_d and stoch_k > 15
    volume_confirms = volume_ratio >= 1.10

    fundamental = fundamental or {}
    fundamental_score = fundamental.get("score", 0)
    if daily_up and daily_macd_buy and hourly_buy and fundamental_score >= -1:
        signal, score = "COMPRA", 5 + int(volume_confirms)
    elif daily_down and daily_macd_sell and hourly_sell:
        signal, score = "VENDA", -(5 + int(volume_confirms))
    else:
        signal = "AGUARDAR"
        score = (int(daily_up) + int(daily_macd_buy) + int(hourly_buy)) - (int(daily_down) + int(daily_macd_sell) + int(hourly_sell)) + max(-2, min(2, fundamental_score))

    changes = [abs(closes[i] / closes[i - 1] - 1) for i in range(max(1, len(closes) - 20), len(closes))]
    volatility = sum(changes) / max(1, len(changes))
    risk = max(0.025, min(0.08, volatility * 2.4))
    direction = -1 if signal == "VENDA" else 1
    trend_return = price / closes[-21] - 1
    reasons = [
        f"Tendência diária: {'positiva' if daily_up else 'negativa' if daily_down else 'lateral'}",
        f"MACD diário 12,26,9: {'comprador' if daily_macd_buy else 'vendedor' if daily_macd_sell else 'neutro'}",
        f"Volume: {volume_ratio:.2f}x a média de 20 períodos",
        f"60 min: {'confirma compra' if hourly_buy else 'confirma venda' if hourly_sell else 'não confirma entrada' if has_hourly else 'histórico indisponível na fonte atual'}",
        fundamental.get("interpretation", "Fundamentos: ainda não disponíveis para esta empresa"),
    ]
    def chart_rows(rows: list[dict], limit: int, include_stochastic: bool = False) -> list[dict]:
        chart_closes = [row["close"] for row in rows]
        chart_ema9 = ema_series(chart_closes, 9)
        chart_ema21 = ema_series(chart_closes, 21)
        fast = ema_series(chart_closes, 12)
        slow = ema_series(chart_closes, 26)
        macd_lines = [a - b for a, b in zip(fast, slow)]
        signal_lines = ema_series(macd_lines, 9)
        raw_k: list[float | None] = [None] * len(rows)
        smooth_k: list[float | None] = [None] * len(rows)
        smooth_d: list[float | None] = [None] * len(rows)
        bollinger_mid: list[float | None] = [None] * len(rows)
        bollinger_upper: list[float | None] = [None] * len(rows)
        bollinger_lower: list[float | None] = [None] * len(rows)
        if include_stochastic:
            valid_raw: list[float] = []
            valid_smooth: list[float] = []
            for index in range(13, len(rows)):
                window = rows[index - 13 : index + 1]
                high = max(row["high"] for row in window)
                low = min(row["low"] for row in window)
                value = 50 if high == low else (rows[index]["close"] - low) / (high - low) * 100
                raw_k[index] = value
                valid_raw.append(value)
                smooth = sma(valid_raw, 3)
                smooth_k[index] = smooth
                valid_smooth.append(smooth)
                smooth_d[index] = sma(valid_smooth, 3)
            for index in range(19, len(rows)):
                sample = chart_closes[index - 19 : index + 1]
                middle = sum(sample) / 20
                deviation = (sum((value - middle) ** 2 for value in sample) / 20) ** 0.5
                bollinger_mid[index], bollinger_upper[index], bollinger_lower[index] = middle, middle + 2 * deviation, middle - 2 * deviation
        start = max(0, len(rows) - limit)
        result = []
        for index in range(start, len(rows)):
            row = rows[index]
            result.append({
                "date": row.get("date"), "open": round(row.get("open") or row["close"], 2),
                "high": round(row.get("high") or row["close"], 2), "low": round(row.get("low") or row["close"], 2),
                "close": round(row["close"], 2), "volume": row.get("volume") or 0,
                "macd": round(macd_lines[index], 4), "macd_signal": round(signal_lines[index], 4),
                "macd_histogram": round(macd_lines[index] - signal_lines[index], 4),
                "stochastic_k": round(smooth_k[index], 2) if smooth_k[index] is not None else None,
                "stochastic_d": round(smooth_d[index], 2) if smooth_d[index] is not None else None,
                "ema9": round(chart_ema9[index], 2), "ema21": round(chart_ema21[index], 2),
                "bollinger_mid": round(bollinger_mid[index], 2) if bollinger_mid[index] is not None else None,
                "bollinger_upper": round(bollinger_upper[index], 2) if bollinger_upper[index] is not None else None,
                "bollinger_lower": round(bollinger_lower[index], 2) if bollinger_lower[index] is not None else None,
            })
        return result
    return {
        "ticker": ticker,
        "execution_ticker": f"{ticker}F",
        "company": company,
        "price": round(price, 2),
        "signal": signal,
        "score": score,
        "confidence": min(90 if has_hourly else 72, 55 + abs(score) * 6),
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
            "ema9": round(ema9, 2), "ema21": round(ema21, 2),
        },
        "states": {
            "daily_trend": "POSITIVA" if daily_up else "NEGATIVA" if daily_down else "LATERAL",
            "daily_macd": "COMPRADOR" if daily_macd_buy else "VENDEDOR" if daily_macd_sell else "NEUTRO",
            "hourly_confirmation": "COMPRA" if hourly_buy else "VENDA" if hourly_sell else "NÃO CONFIRMA" if has_hourly else "INDISPONÍVEL",
            "volume": "CONFIRMA" if volume_confirms else "ABAIXO DA MÉDIA",
        },
        "charts": {"daily": chart_rows(daily, 30), "hourly": chart_rows(hourly, 30, True) if has_hourly else []},
        "fundamentals": fundamental,
        "sources": {"daily": "B3 oficial", "hourly": "brapi" if has_hourly else "indisponível", "fundamentals": "Fundamentus" if fundamental else "indisponível"},
        "timeframe": "Diário + confirmação 60 min",
        "data_status": "DADOS ONLINE",
    }


class FundamentalTableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_body = self.in_row = self.in_cell = False
        self.cell, self.row, self.rows = [], [], []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tbody": self.in_body = True
        elif self.in_body and tag == "tr": self.in_row, self.row = True, []
        elif self.in_row and tag == "td": self.in_cell, self.cell = True, []

    def handle_data(self, data):
        if self.in_cell: self.cell.append(data)

    def handle_endtag(self, tag):
        if tag == "td" and self.in_cell:
            self.row.append(" ".join("".join(self.cell).split()))
            self.in_cell = False
        elif tag == "tr" and self.in_row:
            if self.row: self.rows.append(self.row)
            self.in_row = False
        elif tag == "tbody": self.in_body = False


def number_pt(value: str) -> float | None:
    value = value.replace("R$", "").replace("%", "").replace(".", "").replace(",", ".").strip()
    try: return float(value)
    except ValueError: return None


def fetch_fundamentals() -> dict[str, dict]:
    request = urllib.request.Request("https://www.fundamentus.com.br/resultado.php", headers={"User-Agent": "Mozilla/5.0 Scanner-B3-iPad"})
    with urllib.request.urlopen(request, timeout=45) as response:
        html = response.read().decode("latin-1", errors="ignore")
    parser = FundamentalTableParser(); parser.feed(html)
    result = {}
    for row in parser.rows:
        if len(row) < 21: continue
        ticker = row[0].upper()
        pe, pvp, dy, evebitda, margin, liquidity, roic, roe, daily_liquidity, debt, growth = map(number_pt, (row[2], row[3], row[5], row[11], row[13], row[14], row[15], row[16], row[17], row[19], row[20]))
        positives, cautions, score = [], [], 0
        if pe is not None and 0 < pe <= 18: positives.append("P/L moderado"); score += 1
        elif pe is not None and (pe <= 0 or pe > 35): cautions.append("P/L exige cautela"); score -= 1
        if roe is not None and roe >= 12: positives.append("ROE consistente"); score += 1
        elif roe is not None and roe < 5: cautions.append("ROE baixo"); score -= 1
        if roic is not None and roic >= 10: positives.append("ROIC saudável"); score += 1
        if liquidity is not None and liquidity >= 1.2: positives.append("liquidez corrente adequada"); score += 1
        elif liquidity is not None and liquidity < 1: cautions.append("liquidez corrente inferior a 1"); score -= 1
        if debt is not None and debt > 2: cautions.append("alavancagem elevada"); score -= 1
        if growth is not None and growth > 5: positives.append("receita em crescimento"); score += 1
        elif growth is not None and growth < -5: cautions.append("receita em retração"); score -= 1
        reading = "; ".join(positives[:3]) if positives else "sem reforço fundamental claro"
        if cautions: reading += "; atenção a " + ", ".join(cautions[:2])
        result[ticker] = {"score": score, "interpretation": f"Fundamentos: {reading}", "pe": pe, "pvp": pvp, "dividend_yield": dy, "ev_ebitda": evebitda, "net_margin": margin, "current_liquidity": liquidity, "roic": roic, "roe": roe, "daily_liquidity": daily_liquidity, "debt_equity": debt, "revenue_growth_5y": growth}
    return result


def b3_market_cache() -> dict:
    path = Path("market_cache.json")
    if path.exists() and os.environ.get("DAILY_REVIEW", "").lower() != "true":
        return json.loads(path.read_text(encoding="utf-8"))
    year = datetime.now(UTC).year
    url = f"https://bvmf.bmfbovespa.com.br/InstDados/SerHist/COTAHIST_A{year}.ZIP"
    request = urllib.request.Request(url, headers={"User-Agent": "Scanner-B3-iPad/1.0"})
    with urllib.request.urlopen(request, timeout=180) as response:
        archive = zipfile.ZipFile(io.BytesIO(response.read()))
    histories, names = {}, {}
    with archive.open(archive.namelist()[0]) as source:
        for raw in source:
            line = raw.decode("latin-1")
            if line[:2] != "01" or line[24:27] != "010": continue
            ticker = line[12:24].strip()
            if not ticker or ticker.endswith("F") or not ticker[-1:].isdigit(): continue
            row = {"date": int(datetime.strptime(line[2:10], "%Y%m%d").replace(tzinfo=UTC).timestamp()), "open": int(line[56:69]) / 100, "high": int(line[69:82]) / 100, "low": int(line[82:95]) / 100, "close": int(line[108:121]) / 100, "volume": int(line[170:188]) / 100}
            histories.setdefault(ticker, []).append(row); names[ticker] = line[27:39].strip()
    liquid = sorted(histories, key=lambda ticker: sum(row["volume"] for row in histories[ticker][-20:]) / max(1, len(histories[ticker][-20:])), reverse=True)
    cache = {"updated_at": datetime.now(UTC).isoformat(), "assets": [{"ticker": ticker, "company": names[ticker], "daily": histories[ticker][-100:]} for ticker in liquid[:80] if len(histories[ticker]) >= 35]}
    path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return cache


items, errors = [], []
market = b3_market_cache()
fundamentals = fetch_fundamentals()
universe = market["assets"]
for asset in universe:
    symbol, name, daily = asset["ticker"], asset["company"], asset["daily"]
    try:
        try: hourly = fetch_history(symbol, "5d", "1h") if symbol in {row[0] for row in ASSETS} else []
        except Exception: hourly = []
        items.append(evaluate(symbol, name, daily, hourly, fundamentals.get(symbol)))
    except Exception as error:
        errors.append(f"{symbol}: {error}")
items.sort(key=lambda row: (row["signal"] != "COMPRA", -row["score"]))
payload = {"mode": "PRODUÇÃO ASSISTIDA", "data_status": "DADOS ONLINE", "updated_at": datetime.now(UTC).isoformat(), "items": items, "errors": errors}
payload["universe_size"] = len(items)
payload["requested_universe_size"] = len(universe)
payload["sources"] = ["B3 oficial (diário)", "brapi (60 min quando disponível)", "Fundamentus (fundamentos)"]
payload["universe_mode"] = "AMPLIADO" if len(items) > len(ASSETS) else "GRATUITO LIMITADO"
Path("signals.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def update_tracking(opportunities: list[dict]) -> None:
    path = Path("tracking.json")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        state = {"operations": [], "reviews": []}
    now = datetime.now(UTC)
    today = now.date().isoformat()
    by_ticker = {row["ticker"]: row for row in opportunities}
    review_mode = os.environ.get("DAILY_REVIEW", "").lower() == "true"
    for operation in state["operations"]:
        quote = by_ticker.get(operation["ticker"])
        if not quote or operation["status"] != "ABERTA":
            continue
        direction = 1 if operation["side"] == "COMPRA" else -1
        operation["current_price"] = quote["price"]
        operation["result_percent"] = round(direction * (quote["price"] / operation["entry_price"] - 1) * 100, 2)
        if not review_mode:
            continue
        candle = quote["charts"]["daily"][-1]
        hit_stop = candle["low"] <= operation["stop"] if direction == 1 else candle["high"] >= operation["stop"]
        hit_target = candle["high"] >= operation["target"] if direction == 1 else candle["low"] <= operation["target"]
        if hit_stop and hit_target:
            operation["status"], operation["assessment"] = "REVISÃO MANUAL", "AMBÍGUA"
        elif hit_stop:
            operation["status"], operation["exit_price"], operation["assessment"] = "ENCERRADA", operation["stop"], "INCORRETA"
        elif hit_target:
            operation["trailing_active"], operation["assessment"] = True, "CORRETA"
            operation["stop"] = round(max(operation["stop"], quote["price"] * .97), 2) if direction == 1 else round(min(operation["stop"], quote["price"] * 1.03), 2)
        else:
            operation["assessment"] = "CORRETA" if operation["result_percent"] > 0 else "INCORRETA" if operation["result_percent"] < 0 else "NEUTRA"
        operation["last_review_at"] = now.isoformat()
        review_key = f'{today}:{operation["ticker"]}:{operation["opened_at"]}'
        if not any(row.get("key") == review_key for row in state["reviews"]):
            state["reviews"].append({"key": review_key, "date": today, "ticker": operation["ticker"], "side": operation["side"], "result_percent": operation["result_percent"], "assessment": operation["assessment"], "status": operation["status"]})
    open_tickers = {row["ticker"] for row in state["operations"] if row["status"] == "ABERTA"}
    for quote in opportunities:
        if quote["signal"] not in ("COMPRA", "VENDA") or quote["ticker"] in open_tickers:
            continue
        state["operations"].append({"ticker": quote["ticker"], "side": quote["signal"], "status": "ABERTA", "opened_at": now.isoformat(), "entry_price": quote["price"], "current_price": quote["price"], "stop": quote["stop"], "target": quote["target"], "result_percent": 0, "assessment": "EM ACOMPANHAMENTO", "trailing_active": False})
    state["updated_at"] = now.isoformat()
    state["review_time"] = "18:00 America/Sao_Paulo"
    state["disclaimer"] = "Simulação retrospectiva; não representa operação executada nem garantia de resultado."
    state["reviews"] = state["reviews"][-500:]
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


update_tracking(items)
if not items:
    raise SystemExit("Nenhum ativo pôde ser atualizado")
