const money = value => value.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
const signals = document.querySelector('#signals');
const dialog = document.querySelector('#decision-dialog');
const form = document.querySelector('#decision-form');
let currentItems = [];

const stateClass = value => ['POSITIVA', 'COMPRADOR', 'COMPRA', 'CONFIRMA'].includes(value) ? 'positive' : ['NEGATIVA', 'VENDEDOR', 'VENDA'].includes(value) ? 'negative' : 'neutral';
const indicator = (label, value, detail, state) => `<div class="indicator"><small>${label}</small><strong class="${stateClass(state)}">${value}</strong><span>${detail}</span></div>`;

function card(item) {
  const affordable = Object.entries(item.quantities).filter(([, quantity]) => quantity > 0).pop();
  const budget = affordable ? `Até ${affordable[1]} ação(ões) com R$ ${affordable[0]}` : 'Acima dos orçamentos definidos';
  return `<article class="signal-card">
    <div class="card-top"><div><span class="ticker">${item.ticker}</span><span class="fractional">Fracionário: ${item.execution_ticker || item.ticker + 'F'}</span><span class="company">${item.company}</span></div><span class="badge ${item.signal}">${item.signal}</span></div>
    <div class="price">${money(item.price)}</div><div class="confidence">Confiança do modelo: ${item.confidence}% · risco ${item.risk_percent}%</div>
    <div class="indicator-grid">
      ${indicator('TENDÊNCIA DIÁRIA', item.states.daily_trend, `MME9 ${money(item.indicators.ema9)} · MME21 ${money(item.indicators.ema21)}`, item.states.daily_trend)}
      ${indicator('MACD DIÁRIO · 12,26,9', item.states.daily_macd, `MACD ${item.indicators.daily_macd} · sinal ${item.indicators.daily_signal}`, item.states.daily_macd)}
      ${indicator('VOLUME DIÁRIO', `${item.indicators.volume_ratio}× MÉDIA`, item.states.volume, item.states.volume)}
      ${indicator('CONFIRMAÇÃO · 60 MIN', item.states.hourly_confirmation, `MACD hist. ${item.indicators.hourly_histogram}`, item.states.hourly_confirmation)}
      ${indicator('ESTOCÁSTICO · 14,3,3', `K ${item.indicators.stochastic_k} · D ${item.indicators.stochastic_d}`, item.indicators.stochastic_k > item.indicators.stochastic_d ? 'K acima de D' : 'K abaixo de D', item.states.hourly_confirmation)}
    </div>
    <div class="charts"><figure><figcaption>DIÁRIO · CANDLE + MACD + VOLUME</figcaption><canvas data-ticker="${item.ticker}" data-frame="daily"></canvas></figure><figure><figcaption>60 MIN · CANDLE + MACD + ESTOCÁSTICO + VOLUME</figcaption><canvas data-ticker="${item.ticker}" data-frame="hourly"></canvas></figure></div>
    <div class="levels"><div><small>STOP</small><b>${money(item.stop)}</b></div><div><small>ALVO</small><b>${money(item.target)}</b></div><div><small>VALOR JUSTO</small><b>${money(item.fair_value)}</b></div></div>
    <div class="reasons">${item.reasons.join(' · ')}</div>
    <div class="card-foot"><span class="budget">${budget}</span><button class="outline" data-ticker="${item.ticker}">Registrar</button></div>
  </article>`;
}

function drawCandles(canvas, candles) {
  if (!candles?.length) return;
  const hourly = canvas.dataset.frame === 'hourly';
  const ratio = window.devicePixelRatio || 1, width = canvas.clientWidth, height = hourly ? 390 : 320, pad = 12;
  canvas.style.height = `${height}px`;
  canvas.width = width * ratio; canvas.height = height * ratio;
  const context = canvas.getContext('2d'); context.scale(ratio, ratio); context.clearRect(0, 0, width, height);
  context.font = '9px -apple-system, sans-serif'; context.textBaseline = 'top';
  const pricePanel = { top: 16, bottom: hourly ? 172 : 160 };
  const macdPanel = { top: pricePanel.bottom + 22, bottom: pricePanel.bottom + 86 };
  const stochPanel = hourly ? { top: macdPanel.bottom + 22, bottom: macdPanel.bottom + 82 } : null;
  const volumePanel = { top: (stochPanel?.bottom || macdPanel.bottom) + 22, bottom: height - 8 };
  const panelLine = (label, y) => { context.fillStyle = '#7890ad'; context.fillText(label, 3, y - 13); context.strokeStyle = '#20314a'; context.beginPath(); context.moveTo(0, y); context.lineTo(width, y); context.stroke(); };
  panelLine('PREÇO', pricePanel.top); panelLine('MACD 12,26,9', macdPanel.top); if (stochPanel) panelLine('ESTOCÁSTICO 14,3,3', stochPanel.top); panelLine('VOLUME', volumePanel.top);
  const high = Math.max(...candles.map(row => row.high)), low = Math.min(...candles.map(row => row.low)), spread = high - low || 1;
  const y = value => pricePanel.top + (high - value) / spread * (pricePanel.bottom - pricePanel.top);
  context.strokeStyle = '#20314a';
  [0.25, 0.5, 0.75].forEach(step => { const gridY = pricePanel.top + (pricePanel.bottom-pricePanel.top)*step; context.beginPath(); context.moveTo(0, gridY); context.lineTo(width, gridY); context.stroke(); });
  const slot = width / candles.length, body = Math.max(2, slot * .55);
  candles.forEach((row, index) => { const x = slot * index + slot / 2, color = row.close >= row.open ? '#4bd6c5' : '#ff6e7a'; context.strokeStyle = color; context.fillStyle = color; context.beginPath(); context.moveTo(x, y(row.high)); context.lineTo(x, y(row.low)); context.stroke(); const top = Math.min(y(row.open), y(row.close)); context.fillRect(x - body / 2, top, body, Math.max(2, Math.abs(y(row.open) - y(row.close)))); });
  const drawOscillator = (panel, keys, colors, fixedRange = null) => {
    const values = candles.flatMap(row => keys.map(key => row[key])).filter(value => value !== null && Number.isFinite(value));
    const min = fixedRange ? fixedRange[0] : Math.min(0, ...values), max = fixedRange ? fixedRange[1] : Math.max(0, ...values), range = max - min || 1;
    const scaleY = value => panel.top + (max - value) / range * (panel.bottom - panel.top);
    if (!fixedRange) { const zero = scaleY(0); context.strokeStyle = '#31445d'; context.beginPath(); context.moveTo(0, zero); context.lineTo(width, zero); context.stroke(); candles.forEach((row,index) => { const value=row.macd_histogram; context.fillStyle=value>=0?'#285f59':'#69313b'; const barY=scaleY(value); context.fillRect(index*slot+slot*.25,Math.min(zero,barY),slot*.5,Math.max(1,Math.abs(zero-barY))); }); }
    if (fixedRange) [20,80].forEach(level => { context.setLineDash([3,3]); context.strokeStyle='#31445d'; context.beginPath(); context.moveTo(0,scaleY(level)); context.lineTo(width,scaleY(level)); context.stroke(); context.setLineDash([]); });
    keys.forEach((key,keyIndex) => { context.strokeStyle=colors[keyIndex]; context.lineWidth=1.4; context.beginPath(); let started=false; candles.forEach((row,index) => { const value=row[key]; if(value===null||!Number.isFinite(value)) return; const x=index*slot+slot/2, pointY=scaleY(value); if(!started){context.moveTo(x,pointY);started=true;}else context.lineTo(x,pointY); }); context.stroke(); });
  };
  drawOscillator(macdPanel, ['macd','macd_signal'], ['#6ba8ff','#ffbf5f']);
  if (stochPanel) drawOscillator(stochPanel, ['stochastic_k','stochastic_d'], ['#4bd6c5','#ffbf5f'], [0,100]);
  const maxVolume = Math.max(...candles.map(row => row.volume || 0), 1);
  candles.forEach((row,index) => { const barHeight=(row.volume||0)/maxVolume*(volumePanel.bottom-volumePanel.top); context.fillStyle=row.close>=row.open?'#285f59':'#69313b'; context.fillRect(index*slot+slot*.2,volumePanel.bottom-barHeight,slot*.6,barHeight); });
}

function renderCharts() {
  document.querySelectorAll('canvas[data-ticker]').forEach(canvas => { const item = currentItems.find(row => row.ticker === canvas.dataset.ticker); drawCandles(canvas, item?.charts?.[canvas.dataset.frame]); });
}

async function loadSignals() {
  signals.innerHTML = '<div class="loading">Calculando leitura de mercado…</div>';
  try {
    const response = await fetch(`./signals.json?v=${Date.now()}`, { cache: 'no-store' });
    if (!response.ok) throw new Error('Arquivo de análise indisponível');
    const payload = await response.json();
    currentItems = payload.items;
    signals.innerHTML = payload.items.map(card).join('');
    renderCharts();
    for (const type of ['COMPRA', 'AGUARDAR', 'VENDA']) {
      const id = type === 'COMPRA' ? 'buy-count' : type === 'VENDA' ? 'sell-count' : 'wait-count';
      document.querySelector(`#${id}`).textContent = payload.items.filter(item => item.signal === type).length;
    }
    document.querySelector('#data-status').textContent = payload.data_status === 'DADOS ONLINE' ? 'ONLINE' : 'CONTINGÊNCIA';
    document.querySelector('#updated-at').textContent = `atualizado ${new Date(payload.updated_at).toLocaleString('pt-BR')}`;
  } catch (_) {
    signals.innerHTML = '<div class="loading">A atualização automática ainda não foi concluída. Tente novamente em alguns minutos.</div>';
  }
}

signals.addEventListener('click', event => {
  const button = event.target.closest('[data-ticker]');
  if (!button) return;
  form.reset();
  document.querySelector('#ticker').value = button.dataset.ticker;
  document.querySelector('#dialog-title').textContent = button.dataset.ticker;
  document.querySelector('#form-status').textContent = '';
  dialog.showModal();
});

form.addEventListener('submit', async event => {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(form));
  let saved = false;
  try {
    const response = await fetch('./api/executions', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
    saved = response.ok;
  } catch (_) {}
  if (!saved) {
    const records = JSON.parse(localStorage.getItem('scanner-b3-executions') || '[]');
    records.unshift({ ...data, recorded_at: new Date().toISOString() });
    localStorage.setItem('scanner-b3-executions', JSON.stringify(records.slice(0, 250)));
    saved = true;
  }
  document.querySelector('#form-status').textContent = saved ? 'Decisão registrada neste iPad.' : 'Revise os campos e tente novamente.';
  if (saved) setTimeout(() => dialog.close(), 900);
});

document.querySelector('#refresh').addEventListener('click', loadSignals);
if ('serviceWorker' in navigator) navigator.serviceWorker.register('./service-worker.js');
window.addEventListener('resize', () => requestAnimationFrame(renderCharts));
loadSignals();
