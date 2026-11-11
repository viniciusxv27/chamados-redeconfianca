/* Gerenciamento de metas: abas de pilar, recálculo ao digitar e salvamento automático. */
(function () {
  'use strict';
  var tela = document.getElementById('gmTela');
  if (!tela) return;
  var urlSalvar = tela.dataset.urlSalvar;
  var editavel = tela.dataset.editavel === '1';
  var csrf = (tela.querySelector('input[name=csrfmiddlewaretoken]') || {}).value || '';

  // ---------- Abas ----------
  var botoes = tela.querySelectorAll('.gm-pilares button');
  function mostrar(pilar) {
    var achou = false;
    tela.querySelectorAll('.gm-painel').forEach(function (p) {
      var sim = p.dataset.pilar === pilar;
      p.hidden = !sim;
      achou = achou || sim;
    });
    if (!achou) return false;
    botoes.forEach(function (b) { b.classList.toggle('ativo', b.dataset.pilar === pilar); });
    try { history.replaceState(null, '', '#' + pilar); } catch (e) { /* sem histórico */ }
    return true;
  }
  botoes.forEach(function (b) { b.addEventListener('click', function () { mostrar(b.dataset.pilar); }); });
  if (location.hash) mostrar(location.hash.slice(1));

  // ---------- Números ----------
  function lerNumero(texto) {
    texto = String(texto == null ? '' : texto).replace(/R\$|\s| /g, '');
    if (!texto) return null;
    if (texto.indexOf(',') >= 0 && texto.indexOf('.') >= 0) {
      if (texto.lastIndexOf(',') > texto.lastIndexOf('.')) texto = texto.replace(/\./g, '').replace(',', '.');
      else texto = texto.replace(/,/g, '');
    } else if (texto.indexOf(',') >= 0) {
      texto = texto.replace(/\./g, '').replace(',', '.');
    } else if (/^-?[1-9]\d{0,2}(\.\d{3})+$/.test(texto)) {
      texto = texto.replace(/\./g, '');  // "17.000" = dezessete mil
    }
    var n = Number(texto);
    return isFinite(n) ? n : NaN;
  }
  function dado(el, nome) {
    var v = el.dataset[nome];
    return v === undefined || v === '' ? null : Number(v);
  }
  function br(n, casas) {
    if (n == null || isNaN(n)) return '—';
    return n.toLocaleString('pt-BR', { minimumFractionDigits: casas || 0, maximumFractionDigits: casas || 0 });
  }
  function variacao(meta, base) {
    if (meta == null) return null;
    if (!base) return 0;
    return meta / base - 1;
  }
  function pintar(chip, v) {
    if (!chip) return;
    chip.classList.remove('sobe', 'desce', 'neutro', 'vazio');
    if (v == null) { chip.textContent = '—'; chip.classList.add('vazio'); return; }
    chip.textContent = (v > 0 ? '+' : '') + br(v * 100, 1) + '%';
    chip.classList.add(v > 0.0005 ? 'sobe' : (v < -0.0005 ? 'desce' : 'neutro'));
  }
  function comparar(tr, alvo) {
    pintar(tr.querySelector('[data-c=mxm]'), variacao(alvo, dado(tr, 'ultimo')));
    pintar(tr.querySelector('[data-c=yxy]'), variacao(alvo, dado(tr, 'ano')));
    pintar(tr.querySelector('[data-c=mxquad]'), variacao(alvo, dado(tr, 'quad')));
    pintar(tr.querySelector('[data-c=mxmeta]'), variacao(alvo, dado(tr, 'ant')));
  }
  function cima(x) { return Math.ceil(x - 1e-9); }
  function baixo(x) { return Math.floor(x + 1e-9); }

  // A linha como está na tela: meta e, na Fixa, a conta da quantidade (mesma regra do servidor).
  function recalcularLinha(tr) {
    var entrada = tr.querySelector('[data-campo=meta]');
    var meta = entrada ? lerNumero(entrada.value) : null;
    if (meta !== null && isNaN(meta)) meta = null;
    tr._meta = meta;
    var painel = tr.closest('.gm-painel');
    if (painel.dataset.pilar !== 'fixa') {
      comparar(tr, meta);
      return;
    }
    var ticket = dado(tr, 'ticket');
    var cnsEl = tr.querySelector('[data-campo=qtd_cns]');
    var cns = cnsEl ? lerNumero(cnsEl.value) : null;
    var dec = tr.querySelector('[data-campo=decisao]');
    var r = { qtd: null, cima: null, baixo: null, nova: null };
    if (meta != null && ticket) {
      r.qtd = cima(meta / ticket);
      if (cns) {
        r.cima = cima(r.qtd / cns);
        r.baixo = baixo(r.qtd / cns);
        r.nova = (dec && dec.value === 'BAIXO' ? r.baixo : r.cima) * cns;
      } else {
        r.nova = r.qtd;
      }
    }
    tr._fixa = { meta: meta, qtd_cns: cns, qtd: r.qtd, cima: r.cima, baixo: r.baixo, nova_meta: r.nova };
    tr.querySelector('[data-c=qtd]').textContent = br(r.qtd);
    tr.querySelector('[data-c=cima]').textContent = br(r.cima);
    tr.querySelector('[data-c=baixo]').textContent = br(r.baixo);
    tr.querySelector('[data-c=nova_meta]').textContent = br(r.nova);
    comparar(tr, r.nova);
  }

  // Subtotais RJ / ES / SP e REDE: somam as linhas das lojas.
  function recalcularTotais(painel) {
    var fixa = painel.dataset.pilar === 'fixa';
    var linhas = painel.querySelectorAll('tbody tr[data-loja]');
    painel.querySelectorAll('tfoot tr[data-grupo]').forEach(function (tf) {
      var grupo = tf.dataset.grupo;
      var somas = {};
      var campos = fixa ? ['meta', 'qtd_cns', 'qtd', 'cima', 'baixo', 'nova_meta'] : ['meta'];
      linhas.forEach(function (tr) {
        if (grupo !== 'REDE' && tr.dataset.regiao !== grupo) return;
        campos.forEach(function (c) {
          var v = fixa ? (tr._fixa || {})[c] : tr._meta;
          if (v != null && !isNaN(v)) somas[c] = (somas[c] || 0) + v;
        });
      });
      campos.forEach(function (c) {
        var cel = tf.querySelector('[data-s=' + c + ']');
        if (cel) cel.textContent = br(somas[c] == null ? null : somas[c]);
      });
      comparar(tf, fixa ? (somas.nova_meta == null ? null : somas.nova_meta) : (somas.meta == null ? null : somas.meta));
    });
  }

  // Só leitura: o que o servidor mostrou fica como está.
  if (!editavel) return;
  tela.querySelectorAll('.gm-painel').forEach(function (painel) {
    painel.querySelectorAll('tbody tr[data-loja]').forEach(recalcularLinha);
  });

  // ---------- Salvamento automático ----------
  var pendentes = 0;
  function aviso(painel, texto, classe) {
    var el = painel.querySelector('[data-salvo]');
    if (!el) return;
    el.textContent = texto;
    el.className = 'gm-salvo' + (classe ? ' ' + classe : '');
  }
  function salvar(entrada) {
    var tr = entrada.closest('tr');
    var painel = entrada.closest('.gm-painel');
    var valor = entrada.value;
    if (entrada.dataset.campo !== 'decisao') {
      var n = lerNumero(valor);
      if (n !== null && (isNaN(n) || n < 0)) {
        entrada.classList.add('erro');
        aviso(painel, 'Número inválido em ' + tr.querySelector('.loja strong').textContent + '.', 'erro');
        return;
      }
    }
    entrada.classList.remove('erro', 'salvo');
    entrada.classList.add('salvando');
    pendentes += 1;
    aviso(painel, 'Salvando…', 'salvando');
    fetch(urlSalvar, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf, 'Accept': 'application/json' },
      body: JSON.stringify({ loja: tr.dataset.loja, pilar: painel.dataset.pilar, campo: entrada.dataset.campo, valor: valor })
    }).then(function (r) {
      return r.json().catch(function () { return { ok: false, erro: 'Resposta inválida (' + r.status + ').' }; });
    }).then(function (resp) {
      pendentes -= 1;
      entrada.classList.remove('salvando');
      if (!resp.ok) {
        entrada.classList.add('erro');
        aviso(painel, resp.erro || 'Não salvou.', 'erro');
        return;
      }
      entrada.classList.add('salvo');
      setTimeout(function () { entrada.classList.remove('salvo'); }, 1200);
      var l = resp.linha || {};
      if (painel.dataset.pilar === 'fixa' && tr._fixa) {
        // O servidor manda: se a conta dele divergir (ex.: ticket mudou), fica a dele.
        tr.querySelector('[data-c=nova_meta]').textContent = br(l.nova_meta);
        tr._fixa.nova_meta = l.nova_meta;
        comparar(tr, l.nova_meta);
        recalcularTotais(painel);
      }
      if (!pendentes) aviso(painel, 'Tudo salvo às ' + resp.salvo_em + '.', '');
    }).catch(function () {
      pendentes -= 1;
      entrada.classList.remove('salvando');
      entrada.classList.add('erro');
      aviso(painel, 'Sem conexão: a última alteração não foi salva. Digite de novo.', 'erro');
    });
  }

  tela.querySelectorAll('.gm-entrada').forEach(function (entrada) {
    var tr = entrada.closest('tr');
    var painel = entrada.closest('.gm-painel');
    var relogio = null;
    var ultimo = entrada.value;
    function agendar(ms) {
      clearTimeout(relogio);
      relogio = setTimeout(function () {
        if (entrada.value === ultimo) return;
        ultimo = entrada.value;
        salvar(entrada);
      }, ms);
    }
    entrada.addEventListener('input', function () {
      recalcularLinha(tr);
      recalcularTotais(painel);
      agendar(900);
    });
    entrada.addEventListener('change', function () { recalcularLinha(tr); recalcularTotais(painel); agendar(0); });
    entrada.addEventListener('keydown', function (ev) {
      if (ev.key !== 'Enter') return;
      ev.preventDefault();
      var todas = Array.prototype.slice.call(painel.querySelectorAll('.gm-entrada[data-campo=' + entrada.dataset.campo + ']'));
      var prox = todas[todas.indexOf(entrada) + (ev.shiftKey ? -1 : 1)];
      if (prox) { prox.focus(); if (prox.select) prox.select(); }
    });
  });
  window.addEventListener('beforeunload', function (ev) {
    if (pendentes > 0) { ev.preventDefault(); ev.returnValue = ''; }
  });
})();
