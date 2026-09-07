const TICKERS = [
  ['PETR4', 'Petrobras PN'],
  ['VALE3', 'Vale ON'],
  ['ITUB4', 'Itaú Unibanco PN'],
  ['MGLU3', 'Magazine Luiza ON'],
];
const BUDGETS = [20, 30, 50, 100];

const round = value => Math.round(value * 100) / 100;
const ema = (values, period) => {
  const factor = 2 / (period + 1);
  return values.reduce((acc, value, index) => index ? value * factor + acc * (1 - factor) : value, values[0]);
};
const macd = closes => ema(closes.slice(-26), 12) - ema(closes.slice(-26), 26);
const stochastic = candles => {
  const sample = candles.slice(-14);
  const high = Math.max(...sample.map(item => item.high));
  const low = Math.min(...sample.map(item => item.low));
  return high === low ? 50 : ((sample.at(-1).close - low) / (high - low)) * 100;
};

function evaluate(ticker, company, daily, hourly) {
  const price = daily.at(-1).close;
  const base = daily.at(-21)?.close || daily[0].close;
  const trend = (price - base) / base;
  const dailyMacd = macd(daily.map(item => item.close));
  const hourlyMacd = macd(hourly.map(item => item.close));
  const volumes = daily.slice(-21, -1).map(item => item.volume || 0);
  const averageVolume = volumes.reduce((sum, value) => sum + value, 0) / Math.max(1, volumes.length);
  const volumeRatio = (daily.at(-1).volume || averageVolume) / Math.max(1, averageVolume);
  const stoch = stochastic(hourly);
  let score = 0;
  const reasons = [];
  if (trend > 0) { score += 2; reasons.push('tendência diária positiva'); } else { score -= 2; reasons.push('tendência diária negativa'); }
  if (dailyMacd > 0) { score += 2; reasons.push('MACD diário comprador'); } else { score -= 2; reasons.push('MACD diário vendedor'); }
  if (volumeRatio >= 1.15) { score += trend > 0 ? 1 : -1; reasons.push('volume acima da média'); }
  if (hourlyMacd > 0) { score += 1; reasons.push('60 min confirma força'); } else { score -= 1; reasons.push('60 min não confirma'); }
  if (stoch >= 20 && stoch <= 75) score += 1;
  else if (stoch > 85) score -= 1;
  const signal = score >= 4 ? 'COMPRA' : score <= -4 ? 'VENDA' : 'AGUARDAR';
  const returns = daily.slice(-20).map((item, index, items) => index ? Math.abs(item.close / items[index - 1].close - 1) : 0);
  const volatility = returns.reduce((sum, value) => sum + value, 0) / Math.max(1, returns.length - 1);
  const risk = Math.max(.025, Math.min(.08, volatility * 2.4));
  const direction = signal === 'VENDA' ? -1 : 1;
  return {
    ticker, company, price: round(price), signal, score,
    confidence: Math.min(92, 52 + Math.abs(score) * 7),
    stop: round(price * (1 - direction * risk)),
    target: round(price * (1 + direction * risk * 2)),
    fair_value: round(price * (1 + Math.max(-.12, Math.min(.18, trend * 2)))),
    risk_percent: round(risk * 100),
    quantities: Object.fromEntries(BUDGETS.map(value => [String(value), Math.floor(value / price)])),
    reasons: reasons.slice(0, 4), timeframe: 'Diário + confirmação 60 min', data_status: 'DADOS ONLINE',
  };
}

async function history(ticker, range, interval) {
  const endpoint = `https://brapi.dev/api/quote/${ticker}?range=${range}&interval=${interval}&fundamental=false`;
  const response = await fetch(endpoint, { cf: { cacheTtl: interval === '1d' ? 900 : 300 } });
  if (!response.ok) throw new Error(`Fonte de dados indisponível para ${ticker}`);
  const payload = await response.json();
  return (payload.results?.[0]?.historicalDataPrice || []).filter(item => item.close && item.high && item.low);
}

async function scan() {
  const items = await Promise.all(TICKERS.map(async ([ticker, company]) => {
    const [daily, hourly] = await Promise.all([history(ticker, '3mo', '1d'), history(ticker, '5d', '1h')]);
    if (daily.length < 26 || hourly.length < 14) throw new Error(`Histórico insuficiente para ${ticker}`);
    return evaluate(ticker, company, daily, hourly);
  }));
  items.sort((a, b) => (a.signal !== 'COMPRA') - (b.signal !== 'COMPRA') || b.score - a.score);
  return items;
}

window.scanB3 = scan;
