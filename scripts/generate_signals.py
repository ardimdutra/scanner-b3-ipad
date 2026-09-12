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


def fetch_yahoo_hourly(ticker: str) -> list[dict]:
    last_error = None
    for host in ("query2.finance.yahoo.com", "query1.finance.yahoo.com"):
        try:
            url = f"https://{host}/v8/finance/chart/{ticker}.SA?range=1mo&interval=60m"
            request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 Scanner-B3-iPad"})
            with urllib.request.urlopen(request, timeout=35) as response:
                chart = json.load(response)["chart"]["result"][0]
            timestamps = chart.get("timestamp", [])
            quote = chart["indicators"]["quote"][0]
            rows = []
            for index, timestamp in enumerate(timestamps):
                values = {key: quote.get(key, [None] * len(timestamps))[index] for key in ("open", "high", "low", "close", "volume")}
                if all(values[key] is not None for key in ("open", "high", "low", "close")):
                    rows.append({"date": timestamp, **values})
            if len(rows) >= 30:
                return rows
        except Exception as error:
            last_error = error
    if last_error:
        raise last_error
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


def evaluate(ticker: str, company: str, daily: list[dict], hourly: list[dict] | None, fundamental: dict | None = None, hourly_source: str | None = None) -> dict:
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
    buy_blocks = [daily_up, daily_macd_buy, volume_confirms, hourly_buy, fundamental_score > 0]
    sell_blocks = [daily_down, daily_macd_sell, volume_confirms, hourly_sell, fundamental_score < 0]
    buy_strength = int(daily_up) * 25 + int(daily_macd_buy) * 20 + int(volume_confirms) * 10 + int(hourly_buy) * 30 + max(0, min(15, fundamental_score * 3))
    sell_strength = int(daily_down) * 25 + int(daily_macd_sell) * 20 + int(volume_confirms) * 10 + int(hourly_sell) * 30 + max(0, min(15, -fundamental_score * 3))
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
    if daily_up and daily_macd_buy:
        technical_reading = "A estrutura diária é altista e o MACD reforça continuidade compradora"
    elif daily_down and daily_macd_sell:
        technical_reading = "A estrutura diária é baixista e o MACD reforça pressão vendedora"
    else:
        technical_reading = "Tendência e momentum diário ainda não formam uma tese convergente"
    volume_reading = "com participação acima da média" if volume_confirms else "mas com volume insuficiente para validar força"
    if hourly_buy: timing = "O gráfico de 60 minutos confirma timing de compra."
    elif hourly_sell: timing = "O gráfico de 60 minutos confirma timing de venda."
    elif has_hourly: timing = "O gráfico de 60 minutos diverge ou ainda não confirma entrada."
    else: timing = "Sem histórico de 60 minutos, a leitura permanece candidata e não entrada confirmada."
    fundamental_reading = fundamental.get("interpretation", "Fundamentos ainda não disponíveis").removeprefix("Fundamentos: ")
    analysis = f"{technical_reading}, {volume_reading}. {timing} Na camada fundamentalista, {fundamental_reading}."
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
        "analysis": analysis,
        "ranking": {"buy_strength": buy_strength, "sell_strength": sell_strength, "buy_blocks": sum(buy_blocks), "sell_blocks": sum(sell_blocks), "total_blocks": 5, "hourly_available": has_hourly},
        "sources": {"daily": "B3 oficial", "hourly": hourly_source if has_hourly else "indisponível", "fundamentals": "Fundamentus" if fundamental else "indisponível"},
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


def is_equity_ticker(ticker: str) -> bool:
    return (len(ticker) == 5 and ticker[:4].isalpha() and ticker[-1] in "3456") or (len(ticker) == 6 and ticker[:4].isalpha() and ticker.endswith("11"))


def fetch_json(url: str) -> dict | list:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 Scanner-B3-iPad"})
    with urllib.request.urlopen(request, timeout=35) as response:
        return json.load(response)


def build_macro_snapshot() -> None:
    selic_rows = fetch_json("https://api.bcb.gov.br/dados/serie/bcdata.sgs.432/dados/ultimos/1?formato=json")
    ipca_rows = fetch_json("https://api.bcb.gov.br/dados/serie/bcdata.sgs.433/dados/ultimos/12?formato=json")
    selic = float(selic_rows[-1]["valor"])
    inflation_factor = math.prod(1 + float(row["valor"]) / 100 for row in ipca_rows)
    ipca_12m = (inflation_factor - 1) * 100
    real_rate = ((1 + selic / 100) / inflation_factor - 1) * 100
    if selic >= 10:
        regime = "JUROS REAIS ELEVADOS"
        interpretation = "O prêmio da renda fixa pós-fixada é elevado. Liquidez e proteção do capital devem ocupar o núcleo; bolsa exige seleção por qualidade, geração de caixa e preço."
        categories = [
            {"name": "Reserva e liquidez", "priority": "ALTA", "instruments": "Tesouro Selic; CDB com liquidez diária próximo ou acima de 100% do CDI", "rationale": "Captura juros altos com baixa oscilação e disponibilidade."},
            {"name": "Renda fixa tributariamente eficiente", "priority": "ALTA", "instruments": "LCI/LCA cobertas pelo FGC, comparadas pela taxa líquida equivalente ao CDB", "rationale": "A isenção pode superar CDBs nominais maiores; respeitar carência e emissor."},
            {"name": "Proteção de longo prazo", "priority": "MÉDIA", "instruments": "Tesouro IPCA+ com vencimento compatível com o objetivo", "rationale": "Trava juro real, mas sofre marcação a mercado antes do vencimento."},
            {"name": "Ações e dividendos", "priority": "SELETIVA", "instruments": "Empresas líquidas, lucrativas, pouco alavancadas e com dividendos recorrentes", "rationale": "Precisam oferecer retorno esperado superior ao juro real e suportar custo de capital alto."},
            {"name": "FIIs e crédito privado", "priority": "SELETIVA", "instruments": "Fundos com desconto patrimonial, vacância controlada e crédito de qualidade", "rationale": "Rendimentos não eliminam risco de mercado, liquidez e crédito."},
            {"name": "Diversificação internacional", "priority": "ESTRATÉGICA", "instruments": "ETFs amplos e exposição cambial em parcela compatível com o perfil", "rationale": "Reduz concentração em Brasil, juros locais e risco fiscal."},
        ]
    else:
        regime = "JUROS MODERADOS"
        interpretation = "O prêmio do pós-fixado é menos dominante. Diversificação entre inflação, duration e ativos de risco ganha importância, sempre conforme prazo e tolerância a perdas."
        categories = []
    Path("macro.json").write_text(json.dumps({"updated_at": datetime.now(UTC).isoformat(), "selic": selic, "selic_reference_date": selic_rows[-1]["data"], "ipca_12m": round(ipca_12m, 2), "real_rate_estimate": round(real_rate, 2), "regime": regime, "interpretation": interpretation, "categories": categories, "sources": ["Banco Central do Brasil — SGS 432 e 433", "Tesouro Direto", "Portal do Investidor/CVM"]}, ensure_ascii=False, indent=2), encoding="utf-8")


def build_dividend_ranking(assets: list[dict], fundamental_data: dict[str, dict]) -> None:
    path = Path("dividends.json")
    if path.exists() and os.environ.get("DAILY_REVIEW", "").lower() != "true":
        return
    liquid = sorted(assets, key=lambda asset: fundamental_data.get(asset["ticker"], {}).get("dividend_yield") or 0, reverse=True)[:25]
    ranking, errors = [], []
    now_timestamp = now = datetime.now(UTC).timestamp()
    for asset in liquid:
        ticker = asset["ticker"]
        try:
            url = f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}.SA?range=5y&interval=1mo&events=div"
            chart = fetch_json(url)["chart"]["result"][0]
            events = list(chart.get("events", {}).get("dividends", {}).values())
            payments = sorted([{"date": datetime.fromtimestamp(row["date"], UTC).date().isoformat(), "amount": float(row["amount"])} for row in events], key=lambda row: row["date"])
            recent = [row for row in payments if datetime.fromisoformat(row["date"]).replace(tzinfo=UTC).timestamp() >= now_timestamp - 365 * 86400]
            annual_counts = {}
            for row in payments:
                annual_counts[row["date"][:4]] = annual_counts.get(row["date"][:4], 0) + 1
            typical = round(sum(annual_counts.values()) / max(1, len(annual_counts)))
            cadence = "mensal" if typical >= 10 else "bimestral" if typical >= 5 else "trimestral" if typical >= 3 else "semestral" if typical == 2 else "anual" if typical == 1 else "irregular"
            amount_12m = sum(row["amount"] for row in recent)
            price = asset["daily"][-1]["close"]
            ranking.append({"ticker": ticker, "company": asset["company"], "price": price, "amount_per_share_12m": round(amount_12m, 4), "yield_12m": round(amount_12m / price * 100, 2) if price else 0, "cadence": cadence, "payments_12m": len(recent), "last_payment": payments[-1] if payments else None, "history": payments[-20:], "source": "Yahoo Finance — eventos de dividendos"})
            time.sleep(.12)
        except Exception as error:
            errors.append(f"{ticker}: {error}")
    ranking.sort(key=lambda row: row["yield_12m"], reverse=True)
    path.write_text(json.dumps({"updated_at": datetime.now(UTC).isoformat(), "items": ranking[:15], "errors": errors, "note": "Valores históricos por ação; frequência estimada pelo padrão dos últimos cinco anos. Não é promessa de pagamentos futuros."}, ensure_ascii=False, indent=2), encoding="utf-8")


items, errors = [], []
market = b3_market_cache()
fundamentals = fetch_fundamentals()
universe = [asset for asset in market["assets"] if is_equity_ticker(asset["ticker"])]
assets_by_ticker = {asset["ticker"]: asset for asset in universe}
for asset in universe:
    symbol, name, daily = asset["ticker"], asset["company"], asset["daily"]
    try:
        items.append(evaluate(symbol, name, daily, [], fundamentals.get(symbol)))
    except Exception as error:
        errors.append(f"{symbol}: {error}")
pre_buy = sorted(items, key=lambda row: row["ranking"]["buy_strength"], reverse=True)[:15]
pre_sell = sorted(items, key=lambda row: row["ranking"]["sell_strength"], reverse=True)[:15]
candidates = {row["ticker"] for row in pre_buy + pre_sell}
base_symbols = {row[0] for row in ASSETS}
for index, symbol in enumerate(candidates):
    asset = assets_by_ticker[symbol]
    hourly, source = [], None
    if symbol in base_symbols:
        try:
            hourly, source = fetch_history(symbol, "1mo", "1h"), "brapi"
        except Exception:
            pass
    if len(hourly) < 30:
        try:
            hourly, source = fetch_yahoo_hourly(symbol), "Yahoo Finance"
            time.sleep(.15)
        except Exception as error:
            errors.append(f"{symbol} 60min: {error}")
    refreshed = evaluate(symbol, asset["company"], asset["daily"], hourly, fundamentals.get(symbol), source)
    items[items.index(next(row for row in items if row["ticker"] == symbol))] = refreshed
items.sort(key=lambda row: (row["signal"] != "COMPRA", -row["score"]))
payload = {"mode": "PRODUÇÃO ASSISTIDA", "data_status": "DADOS ONLINE", "updated_at": datetime.now(UTC).isoformat(), "items": items, "errors": errors}
payload["universe_size"] = len(items)
payload["requested_universe_size"] = len(universe)
payload["sources"] = ["B3 oficial (diário)", "brapi + Yahoo Finance (60 min validado)", "Fundamentus (fundamentos)"]
payload["universe_mode"] = "AMPLIADO" if len(items) > len(ASSETS) else "GRATUITO LIMITADO"
Path("signals.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
try:
    build_macro_snapshot()
except Exception as error:
    errors.append(f"Macro: {error}")
try:
    build_dividend_ranking(universe, fundamentals)
except Exception as error:
    errors.append(f"Dividendos: {error}")


def update_tracking(opportunities: list[dict]) -> None:
    path = Path("tracking.json")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        state = {"operations": [], "reviews": [], "candidates": {}}
    state.setdefault("candidates", {})
    state.setdefault("signal_log", [])
    now = datetime.now(UTC)
    today = now.date().isoformat()
    by_ticker = {row["ticker"]: row for row in opportunities}
    review_mode = os.environ.get("DAILY_REVIEW", "").lower() == "true"
    for operation in state["operations"]:
        if "method_version" not in operation:
            operation["method_version"] = "v1-legado"
            operation["audit_status"] = "NÃO AUDITÁVEL — regra antiga usava candle diário completo"
        quote = by_ticker.get(operation["ticker"])
        direction = 1 if operation["side"] == "COMPRA" else -1
        if operation["status"] == "ENCERRADA" and operation.get("exit_price"):
            result_price = operation["exit_price"]
        elif quote and quote["charts"].get("hourly"):
            result_price = quote["charts"]["hourly"][-1]["close"]
            operation["current_price"] = result_price
        elif quote:
            result_price = quote["price"]
            operation["current_price"] = result_price
        else:
            # Preserve a financial result for legacy operations even when the
            # ticker is no longer present in today's screened universe.
            result_price = operation.get("current_price", operation["entry_price"])
        operation["result_percent"] = round(direction * (result_price / operation["entry_price"] - 1) * 100, 2)
        quantity = operation.setdefault("quantity_reference", math.floor(1000 / operation["entry_price"]))
        operation["pnl_per_share"] = round(direction * (result_price - operation["entry_price"]), 2)
        operation["financial_result"] = round(operation["pnl_per_share"] * quantity, 2)
        if operation["status"] == "ABERTA" and operation["method_version"] == "v2" and quote and quote["charts"].get("hourly"):
            opened_timestamp = datetime.fromisoformat(operation["opened_at"]).timestamp()
            for candle in [row for row in quote["charts"]["hourly"] if row["date"] > opened_timestamp]:
                hit_stop = candle["low"] <= operation["stop"] if direction == 1 else candle["high"] >= operation["stop"]
                hit_target = candle["high"] >= operation["target"] if direction == 1 else candle["low"] <= operation["target"]
                if hit_stop and hit_target:
                    operation["status"], operation["assessment"] = "REVISÃO MANUAL", "AMBÍGUA"
                    break
                if hit_stop:
                    operation.update({"status": "ENCERRADA", "exit_price": operation["stop"], "closed_at": datetime.fromtimestamp(candle["date"], UTC).isoformat(), "assessment": "INCORRETA"})
                    break
                if hit_target:
                    operation["trailing_active"], operation["assessment"] = True, "CORRETA"
                    operation["stop"] = operation["entry_price"]
            if operation["status"] == "ABERTA" and operation.get("trailing_active"):
                operation["stop"] = round(max(operation["stop"], result_price * .97), 2) if direction == 1 else round(min(operation["stop"], result_price * 1.03), 2)
            if operation["status"] == "ENCERRADA":
                result_price = operation["exit_price"]
                operation["result_percent"] = round(direction * (result_price / operation["entry_price"] - 1) * 100, 2)
                operation["pnl_per_share"] = round(direction * (result_price - operation["entry_price"]), 2)
                operation["financial_result"] = round(operation["pnl_per_share"] * quantity, 2)
        if review_mode:
            operation["last_review_at"] = now.isoformat()
        review_key = f'{today}:{operation["ticker"]}:{operation["opened_at"]}'
        if review_mode and not any(row.get("key") == review_key for row in state["reviews"]):
            state["reviews"].append({"key": review_key, "date": today, "ticker": operation["ticker"], "side": operation["side"], "result_percent": operation["result_percent"], "financial_result": operation["financial_result"], "assessment": operation["assessment"], "status": operation["status"], "method_version": operation["method_version"]})
    open_tickers = {row["ticker"] for row in state["operations"] if row["status"] == "ABERTA"}
    for quote in opportunities:
        ticker, side = quote["ticker"], quote["signal"]
        if side not in ("COMPRA", "VENDA") or not quote["charts"].get("hourly"):
            state["candidates"].pop(ticker, None)
            continue
        last_bar = quote["charts"]["hourly"][-1]
        candidate = state["candidates"].get(ticker, {})
        if candidate.get("side") != side:
            candidate = {"side": side, "confirmations": 0, "first_seen": now.isoformat(), "last_bar": None}
        if candidate.get("last_bar") != last_bar["date"]:
            candidate["confirmations"] += 1
            candidate["last_bar"] = last_bar["date"]
        candidate["last_seen"] = now.isoformat()
        state["candidates"][ticker] = candidate
        log_key = f'{ticker}:{side}:{last_bar["date"]}'
        if not any(row.get("key") == log_key for row in state["signal_log"]):
            state["signal_log"].append({"key": log_key, "ticker": ticker, "side": side, "bar_time": last_bar["date"], "observed_at": now.isoformat(), "price": last_bar["close"], "buy_strength": quote["ranking"]["buy_strength"], "sell_strength": quote["ranking"]["sell_strength"], "daily_thesis_date": quote["charts"]["daily"][-1]["date"]})
        recent_closed = [row for row in state["operations"] if row["ticker"] == ticker and row["status"] == "ENCERRADA"]
        cooldown = recent_closed and (now - datetime.fromisoformat(recent_closed[-1].get("closed_at", recent_closed[-1]["opened_at"]))).total_seconds() < 72 * 3600
        if ticker in open_tickers or candidate["confirmations"] < 2 or cooldown:
            continue
        entry = last_bar["close"]
        risk = quote["risk_percent"] / 100
        direction = 1 if side == "COMPRA" else -1
        operation = {"ticker": ticker, "side": side, "status": "ABERTA", "opened_at": datetime.fromtimestamp(last_bar["date"], UTC).isoformat(), "entry_price": entry, "current_price": entry, "stop": round(entry * (1 - direction * risk), 2), "target": round(entry * (1 + direction * risk * 2), 2), "result_percent": 0, "pnl_per_share": 0, "quantity_reference": math.floor(1000 / entry), "financial_result": 0, "assessment": "EM ACOMPANHAMENTO", "trailing_active": False, "method_version": "v2", "confirmation_bars": 2, "daily_thesis_date": quote["charts"]["daily"][-1]["date"]}
        state["operations"].append(operation)
        open_tickers.add(ticker)
        state["candidates"].pop(ticker, None)
    valid_closed = [row for row in state["operations"] if row.get("method_version") == "v2" and row["status"] == "ENCERRADA"]
    valid_open = [row for row in state["operations"] if row.get("method_version") == "v2" and row["status"] == "ABERTA"]
    state["performance"] = {"capital_reference": 1000, "closed_operations": len(valid_closed), "open_operations": len(valid_open), "correct": sum(row.get("financial_result", 0) > 0 for row in valid_closed), "incorrect": sum(row.get("financial_result", 0) < 0 for row in valid_closed), "realized_result": round(sum(row.get("financial_result", 0) for row in valid_closed), 2), "open_result": round(sum(row.get("financial_result", 0) for row in valid_open), 2), "legacy_operations": sum(row.get("method_version") == "v1-legado" for row in state["operations"])}
    state["updated_at"] = now.isoformat()
    state["review_time"] = "18:00 America/Sao_Paulo"
    state["methodology"] = "v2: tese diária congelada, dois candles horários consecutivos e cooldown de 72 horas"
    state["disclaimer"] = "Simulação com capital de referência de R$ 1.000 por operação; sem custos, impostos ou slippage. Não representa operação executada nem garantia de resultado."
    state["reviews"] = state["reviews"][-500:]
    state["signal_log"] = state["signal_log"][-1500:]
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


update_tracking(items)
if not items:
    raise SystemExit("Nenhum ativo pôde ser atualizado")
