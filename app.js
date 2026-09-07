const money = value => value.toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
const signals = document.querySelector('#signals');
const dialog = document.querySelector('#decision-dialog');
const form = document.querySelector('#decision-form');

function card(item) {
  const affordable = Object.entries(item.quantities).filter(([, quantity]) => quantity > 0).pop();
  const budget = affordable ? `Até ${affordable[1]} ação(ões) com R$ ${affordable[0]}` : 'Acima dos orçamentos definidos';
  return `<article class="signal-card">
    <div class="card-top"><div><span class="ticker">${item.ticker}</span><span class="company">${item.company}</span></div><span class="badge ${item.signal}">${item.signal}</span></div>
    <div class="price">${money(item.price)}</div><div class="confidence">Confiança do modelo: ${item.confidence}% · risco ${item.risk_percent}%</div>
    <div class="levels"><div><small>STOP</small><b>${money(item.stop)}</b></div><div><small>ALVO</small><b>${money(item.target)}</b></div><div><small>VALOR JUSTO</small><b>${money(item.fair_value)}</b></div></div>
    <div class="reasons">${item.reasons.join(' · ')}</div>
    <div class="card-foot"><span class="budget">${budget}</span><button class="outline" data-ticker="${item.ticker}">Registrar</button></div>
  </article>`;
}

async function loadSignals() {
  signals.innerHTML = '<div class="loading">Calculando leitura de mercado…</div>';
  try {
    let payload;
    try {
      const response = await fetch('./api/signals');
      if (!response.ok || !response.headers.get('content-type')?.includes('application/json')) throw new Error('API remota ausente');
      payload = await response.json();
    } catch (_) {
      const items = await window.scanB3();
      payload = { items, data_status: 'DADOS ONLINE', updated_at: new Date().toISOString() };
    }
    signals.innerHTML = payload.items.map(card).join('');
    for (const type of ['COMPRA', 'AGUARDAR', 'VENDA']) {
      const id = type === 'COMPRA' ? 'buy-count' : type === 'VENDA' ? 'sell-count' : 'wait-count';
      document.querySelector(`#${id}`).textContent = payload.items.filter(item => item.signal === type).length;
    }
    document.querySelector('#data-status').textContent = payload.data_status === 'DADOS ONLINE' ? 'ONLINE' : 'CONTINGÊNCIA';
    document.querySelector('#updated-at').textContent = `atualizado ${new Date(payload.updated_at).toLocaleString('pt-BR')}`;
  } catch (_) {
    signals.innerHTML = '<div class="loading">Não foi possível carregar a análise. Verifique se o servidor está ativo.</div>';
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
loadSignals();
