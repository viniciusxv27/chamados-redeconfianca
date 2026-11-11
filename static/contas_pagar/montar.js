/* Contas a Pagar — montagem do "Pagar hoje" (portado do app.js do sistema antigo). */
(function () {
  'use strict';
  var raiz = document.getElementById('cpMontar');
  if (!raiz) return;

  // ------------------------------------------------------------------ utils
  var $ = function (s, el) { return (el || document).querySelector(s); };
  var $$ = function (s, el) { return Array.prototype.slice.call((el || document).querySelectorAll(s)); };
  var esc = function (s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); };
  var BRL = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' });
  var NUM = new Intl.NumberFormat('pt-BR', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  var brl = function (v) { return BRL.format(v || 0); };
  var parseBR = function (s) {
    if (typeof s === 'number') return s;
    s = String(s || '').replace(/[R$\s]/g, '');
    if (!s) return 0;
    if (s.indexOf(',') >= 0 || /^-?\d{1,3}(\.\d{3})+$/.test(s)) s = s.replace(/\./g, '').replace(',', '.');
    var n = parseFloat(s); return isNaN(n) ? 0 : n;
  };
  var fmtInput = function (v) { return v ? NUM.format(v) : ''; };
  var sum = function (arr, f) { return arr.reduce(function (a, x) { return a + (f ? f(x) : x); }, 0); };
  var iso = function (d) { return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); };
  var fromIso = function (s) { var p = s.split('-').map(Number); return new Date(p[0], p[1] - 1, p[2]); };
  var addDays = function (s, n) { var d = fromIso(s); d.setDate(d.getDate() + n); return iso(d); };
  var dBR = function (s) { return s ? s.split('-').reverse().join('/') : ''; };
  var splitBanco = function (s) { var i = s.indexOf(' - AG:'); return i > 0 ? [s.slice(0, i), s.slice(i + 3)] : [s, '']; };
  var HOJE = raiz.dataset.hoje;
  var CSRF = raiz.dataset.csrf;

  function toast(msg, erro) {
    var t = document.createElement('div');
    t.className = 'cp-toast' + (erro ? ' erro' : ''); t.textContent = msg;
    document.body.appendChild(t); setTimeout(function () { t.remove(); }, 4000);
  }
  function api(url, opt) {
    opt = opt || {};
    var cab = { 'X-CSRFToken': CSRF };
    if (opt.body !== undefined) cab['Content-Type'] = 'application/json';
    return fetch(url, { method: opt.method || 'GET', headers: cab, credentials: 'same-origin',
                        body: opt.body !== undefined ? JSON.stringify(opt.body) : undefined })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (j) {
          if (!r.ok) throw new Error(j.erro || ('Erro ' + r.status));
          return j;
        });
      });
  }

  // ------------------------------------------------------------------ estado
  var O = { up: null, titulos: [], saldos: {}, bancos: [], excl: new Set(), verTodos: false, sujo: false };

  api(raiz.dataset.api).then(function (d) {
    O.up = d.upload; O.titulos = d.titulos; O.bancos = d.bancos;
    O.saldos = {}; d.saldos.forEach(function (s) { O.saldos[s.banco_id] = s; });
    O.excl = new Set(d.titulos.filter(function (t) { return t.excluido; }).map(function (t) { return t.id; }));
    var criado = new Date(O.up.criado_em).toLocaleString('pt-BR');
    $('#cpCabecalho').innerHTML = esc(O.up.arquivo) + ' · importado em ' + criado + ' · ' + O.titulos.length +
      ' títulos na base <span class="cp-selo aviso">rascunho</span>';
    $('#fPag').value = O.up.data_pagamento; $('#fIni').value = O.up.venc_ini; $('#fFim').value = O.up.venc_fim;
    try { var aviso = sessionStorage.getItem('cpAviso'); if (aviso) { toast(aviso, true); sessionStorage.removeItem('cpAviso'); } } catch (e) { /* sem storage */ }
    montar();
  }).catch(function (e) { $('#cpCabecalho').textContent = e.message; toast(e.message, true); });

  function montar() {
    var venc = O.titulos.map(function (t) { return t.vencimento; }).filter(Boolean).sort();
    var minV = venc[0];
    var setRange = function (a, b) { $('#fIni').value = a; $('#fFim').value = b; O.sujo = true; renderOp(); };
    $$('#atalhos button').forEach(function (b) {
      b.onclick = function () {
        var p = $('#fPag').value || HOJE;
        var dw = fromIso(p).getDay();
        ({ hoje: function () { setRange(p, p); },
           atraso: function () { setRange(minV && minV < p ? minV : p, p); },
           fds: function () { setRange(p, addDays(p, dw === 0 ? 0 : 7 - dw)); },
           amanha: function () { setRange(p, addDays(p, 1)); } })[b.dataset.a]();
      };
    });
    ['#fPag', '#fIni', '#fFim'].forEach(function (s) { $(s).onchange = function () { O.sujo = true; renderOp(); }; });
    $('#bSalvar').onclick = function () { salvarOp(false); };
    $('#bPub').onclick = function () { salvarOp(true); };
    $('#bDel').onclick = function () {
      if (!confirm('Descartar esta importação? Ela será removida.')) return;
      api(raiz.dataset.descartar, { method: 'POST', body: {} }).then(function () {
        O.sujo = false; location.href = raiz.dataset.voltar;
      }).catch(function (e) { toast(e.message, true); });
    };
    window.addEventListener('beforeunload', function (e) { if (O.sujo) { e.preventDefault(); e.returnValue = ''; } });
    renderOp();
  }

  function opSelecionados() {
    var ini = $('#fIni').value, fim = $('#fFim').value;
    return O.titulos.filter(function (t) {
      return t.vencimento && t.vencimento >= ini && t.vencimento <= fim && (t.situacao || 'A Pagar').toLowerCase().indexOf('a pagar') === 0;
    });
  }

  function renderOp() {
    var sel = opSelecionados();
    var ini = $('#fIni').value, fim = $('#fFim').value, pag = $('#fPag').value;
    var lista = O.verTodos ? O.titulos : sel;
    var valor = sum(sel.filter(function (t) { return !O.excl.has(t.id); }), function (t) { return t.valor; });
    var todosMarcados = sel.every(function (t) { return !O.excl.has(t.id); });
    $('#oTit').innerHTML = '<table class="cp-tabela" data-rc-wrapped="1"><thead><tr>' +
      '<th style="width:34px"><input type="checkbox" class="cp-chk" id="chkAll" aria-label="Marcar todos" ' + (todosMarcados ? 'checked' : '') + '></th>' +
      '<th>Vencimento</th><th>Fornecedor</th><th>Observações</th><th>Filial</th><th>Categoria</th><th class="num">Valor líquido</th><th>Banco pagador</th></tr></thead><tbody>' +
      (lista.map(function (t) {
        var dentro = sel.indexOf(t) >= 0;
        return '<tr class="' + (!dentro || O.excl.has(t.id) ? 'dim' : '') + '">' +
          '<td>' + (dentro ? '<input type="checkbox" class="cp-chk" data-t="' + t.id + '" aria-label="Incluir título" ' + (O.excl.has(t.id) ? '' : 'checked') + '>' : '') + '</td>' +
          '<td class="nw">' + dBR(t.vencimento) + (t.vencimento && t.vencimento < pag ? ' <span class="cp-selo aviso">vencido</span>' : '') + '</td>' +
          '<td>' + esc(t.fornecedor) + '</td><td class="obs">' + esc(t.observacoes) + '</td><td>' + esc(t.filial) + '</td>' +
          '<td><span class="cp-selo ' + (t.tipo_macro === 'Não Classificado' ? 'aviso' : 'cinza') + '">' + esc(t.tipo_macro) + '</span></td>' +
          '<td class="num"><b>' + brl(t.valor) + '</b></td>' +
          '<td class="cp-banco-sub">' + (t.banco_id ? esc(splitBanco(t.banco)[0]) : '<span class="cp-selo aviso" title="' + esc(t.debito) + '">Não informado</span>') + '</td></tr>';
      }).join('') || '<tr><td colspan="8" class="mudo" style="text-align:center;padding:24px">Nenhum título vence entre ' + dBR(ini) + ' e ' + dBR(fim) + '.</td></tr>') +
      '</tbody></table>';
    var nSel = sel.filter(function (t) { return !O.excl.has(t.id); }).length;
    $('#oTitPe').innerHTML = '<span><b>' + nSel + '</b> de ' + sel.length + ' títulos selecionados · <b class="neg">' + brl(valor) + '</b></span>' +
      '<label class="mudo" style="cursor:pointer"><input type="checkbox" class="cp-chk" id="verTodos" ' + (O.verTodos ? 'checked' : '') + '> mostrar todos os ' + O.titulos.length + ' títulos da base</label>';
    $('#verTodos').onchange = function (e) { O.verTodos = e.target.checked; renderOp(); };
    $('#chkAll').onchange = function (e) { sel.forEach(function (t) { if (e.target.checked) O.excl.delete(t.id); else O.excl.add(t.id); }); O.sujo = true; renderOp(); };
    $$('#oTit [data-t]').forEach(function (c) {
      c.onchange = function () { var id = Number(c.dataset.t); if (c.checked) O.excl.delete(id); else O.excl.add(id); O.sujo = true; renderOp(); };
    });
    renderSaldosOp();
  }

  function pagamentosPorBanco() {
    var p = {};
    opSelecionados().filter(function (t) { return !O.excl.has(t.id); }).forEach(function (t) {
      var k = t.banco_id == null ? 'null' : t.banco_id; p[k] = (p[k] || 0) + t.valor;
    });
    return p;
  }
  function calcSaldo(b, pag) {
    var s = O.saldos[b.id] || {};
    var tot = (+s.aplicacao || 0) + (+s.garantida || 0) + (+s.saldo || 0);
    var pg = pag[b.id] || 0;
    return { s: s, tot: tot, pg: pg, fin: tot - pg + (+s.transferencia || 0) };
  }

  function renderSaldosOp() {
    var inp = function (b, k, v, rot) { return '<td class="num"><input class="cp-campo dinheiro" data-b="' + b.id + '" data-k="' + k + '" value="' + fmtInput(v) + '" placeholder="0,00" inputmode="decimal" aria-label="' + rot + '"></td>'; };
    var rows = O.bancos.map(function (b) {
      var s = O.saldos[b.id] || {};
      var nm = splitBanco(b.nome);
      return '<tr><td><div class="cp-banco-nome">' + esc(nm[0]) + '</div><div class="cp-banco-sub">' + esc(nm[1]) + '</div></td>' +
        inp(b, 'aplicacao', s.aplicacao, 'Aplicação') + inp(b, 'garantida', s.garantida, 'Conta garantida') + inp(b, 'saldo', s.saldo, 'Saldo') +
        '<td class="num" data-c="tot-' + b.id + '"></td><td class="num neg" data-c="pg-' + b.id + '"></td>' +
        inp(b, 'transferencia', s.transferencia, 'Transferência') +
        '<td class="num" data-c="fin-' + b.id + '"></td></tr>';
    }).join('');
    $('#oSaldos').innerHTML = '<table class="cp-tabela" data-rc-wrapped="1"><thead><tr><th>Banco</th><th class="num">Aplicação / Investimentos</th><th class="num">Utilização conta garantida</th><th class="num">Saldo</th>' +
      '<th class="num">Total disponível</th><th class="num">Pagamentos</th><th class="num">Transferência</th><th class="num">Saldo final</th></tr></thead>' +
      '<tbody>' + rows +
      '<tr id="rowSemBanco"><td><div class="cp-banco-nome aviso">Não informado</div><div class="cp-banco-sub">títulos sem conta de débito cadastrada (ex.: Caixa)</div></td>' +
      '<td></td><td></td><td></td><td></td><td class="num neg" data-c="pg-null"></td><td></td><td class="num neg" data-c="fin-null"></td></tr>' +
      '</tbody><tfoot><tr id="oFoot"></tr></tfoot></table>';
    atualizarCalcSaldos();
    $$('#oSaldos input.dinheiro').forEach(function (i) {
      i.onfocus = function () { i.select(); };
      i.onchange = function () {
        var b = i.dataset.b, k = i.dataset.k;
        O.saldos[b] = O.saldos[b] || { banco_id: Number(b) };
        O.saldos[b][k] = parseBR(i.value); O.sujo = true;
        atualizarCalcSaldos();
        i.value = fmtInput(O.saldos[b][k]);
      };
      i.onkeydown = function (e) {
        if (e.key !== 'Enter') return;
        e.preventDefault();
        var coluna = $$('#oSaldos input.dinheiro').filter(function (x) { return x.dataset.k === i.dataset.k; });
        var nxt = coluna[coluna.indexOf(i) + 1];
        i.blur(); if (nxt) nxt.focus();
      };
    });
  }

  function atualizarCalcSaldos() {
    var pag = pagamentosPorBanco();
    var T = { apl: 0, gar: 0, sal: 0, tot: 0, pg: 0, trf: 0, fin: 0 };
    var negs = [];
    var algumaTrf = false;
    O.bancos.forEach(function (b) {
      var c = calcSaldo(b, pag), s = c.s;
      T.apl += +s.aplicacao || 0; T.gar += +s.garantida || 0; T.sal += +s.saldo || 0;
      T.tot += c.tot; T.pg += c.pg; T.trf += +s.transferencia || 0; T.fin += c.fin;
      if (+s.transferencia) algumaTrf = true;
      if (c.fin < -0.005) negs.push(splitBanco(b.nome)[0]);
      $('[data-c="tot-' + b.id + '"]').textContent = brl(c.tot);
      $('[data-c="pg-' + b.id + '"]').textContent = c.pg ? brl(c.pg) : '—';
      var f = $('[data-c="fin-' + b.id + '"]');
      f.innerHTML = '<b>' + brl(c.fin) + '</b>'; f.className = 'num ' + (c.fin < 0 ? 'neg' : 'pos');
    });
    var sb = pag['null'] || 0;
    $('#rowSemBanco').style.display = sb ? '' : 'none';
    $('[data-c="pg-null"]').textContent = brl(sb);
    $('[data-c="fin-null"]').textContent = brl(-sb);
    $('#oFoot').innerHTML = '<td>Total</td><td class="num">' + brl(T.apl) + '</td><td class="num">' + brl(T.gar) + '</td><td class="num">' + brl(T.sal) + '</td>' +
      '<td class="num">' + brl(T.tot) + '</td><td class="num neg">' + brl(T.pg + sb) + '</td><td class="num">' + brl(T.trf) + '</td><td class="num">' + brl(T.fin - sb) + '</td>';
    $('#trfCheck').innerHTML = Math.abs(T.trf) < 0.005 ? (algumaTrf ? '<span class="cp-selo ok">Transferências fecham em zero</span>' : '')
      : '<span class="cp-selo aviso">Transferências não fecham: ' + brl(T.trf) + '</span>';
    $('#oResumo').innerHTML = 'Pagamentos: <b class="neg">' + brl(T.pg + sb) + '</b> · Saldo final: <b>' + brl(T.fin - sb) + '</b>' +
      (negs.length ? ' · <span class="aviso">⚠ saldo negativo em ' + negs.map(esc).join(', ') + '</span>' : '');
  }

  function salvarOp(publicar) {
    var ini = $('#fIni').value, fim = $('#fFim').value, pag = $('#fPag').value;
    if (!pag || !ini || !fim || ini > fim) { toast('Verifique a data de pagamento e o período de vencimento.', true); return; }
    var body = {
      data_pagamento: pag, venc_ini: ini, venc_fim: fim, excluidos: Array.from(O.excl),
      saldos: Object.keys(O.saldos).map(function (k) { return O.saldos[k]; }),
    };
    if (publicar) {
      if (!confirm('Publicar a programação de ' + dBR(pag) + '? Se já existir uma programação para essa data, ela será substituída.')) return;
      body.status = 'publicado';
    }
    var botoes = $$('.cp-barra-fixa button'); botoes.forEach(function (b) { b.disabled = true; });
    api(raiz.dataset.api, { method: 'POST', body: body }).then(function (r) {
      O.sujo = false;
      if (publicar) { toast(r.titulos + ' títulos de ' + dBR(pag) + ' gravados no histórico'); location.href = r.url; }
      else toast('Rascunho salvo');
    }).catch(function (e) { toast(e.message, true); })
      .then(function () { botoes.forEach(function (b) { b.disabled = false; }); });
  }
})();
