/* Coluna "Realizado até o momento": o mês da competência, do banco de vendas, contra a meta da loja.
   Chega depois da página (o banco pode demorar) e o % acompanha a meta enquanto ela é editada. */
(function () {
  'use strict';
  var tela = document.getElementById('gmTela');
  if (!tela || !tela.dataset.urlAgora) return;
  var dados = null;

  function lerNumero(texto) {
    texto = String(texto == null ? '' : texto).replace(/R\$|\s| /g, '');
    if (!texto || texto === '—') return null;
    if (texto.indexOf(',') >= 0 && texto.indexOf('.') >= 0) {
      texto = texto.lastIndexOf(',') > texto.lastIndexOf('.') ? texto.replace(/\./g, '').replace(',', '.') : texto.replace(/,/g, '');
    } else if (texto.indexOf(',') >= 0) {
      texto = texto.replace(/\./g, '').replace(',', '.');
    } else if (/^-?[1-9]\d{0,2}(\.\d{3})+$/.test(texto)) {
      texto = texto.replace(/\./g, '');
    }
    var n = Number(texto);
    return isFinite(n) ? n : null;
  }
  function br(n, casas) {
    return Number(n).toLocaleString('pt-BR', {minimumFractionDigits: casas || 0, maximumFractionDigits: casas || 0});
  }

  // A meta que vale na linha: Fixa = nova meta (quantidade); os outros = meta em R$.
  function alvoDaLinha(tr, fixa) {
    if (fixa) {
      var nova = tr.querySelector('[data-c="nova_meta"], [data-s="nova_meta"]');
      return nova ? lerNumero(nova.textContent) : null;
    }
    var campo = tr.querySelector('[data-campo="meta"]');
    if (campo) return lerNumero(campo.value);
    var cel = tr.querySelector('[data-s="meta"]') || tr.querySelector('.meta-col');
    return cel ? lerNumero(cel.textContent) : null;
  }

  function celula(td, real, alvo, fixa, titulo) {
    if (real == null) { td.innerHTML = '<span class="gm-agora-vazio">—</span>'; td.title = ''; return; }
    var texto = fixa ? br(real) : br(real);
    var pct = alvo ? real / alvo : null;
    var classe = pct == null ? 'neutro' : pct >= 1 ? 'bateu' : pct >= 0.7 ? 'perto' : 'longe';
    td.innerHTML = '<strong class="gm-agora-valor">' + texto + '</strong>' +
      (pct == null ? '' : '<span class="gm-agora-barra ' + classe + '"><span style="width:' + Math.min(100, pct * 100).toFixed(1) + '%"></span></span>' +
        '<small class="gm-agora-pct ' + classe + '">' + br(pct * 100, 1) + '% da meta</small>');
    td.title = titulo || '';
  }

  function pintarPainel(painel) {
    var pilar = painel.dataset.pilar, fixa = pilar === 'fixa';
    var porLoja = (dados && dados.lojas && dados.lojas[pilar]) || {};
    var somas = {};
    painel.querySelectorAll('tbody tr[data-loja]').forEach(function (tr) {
      var td = tr.querySelector('[data-agora]');
      if (!td) return;
      if (!dados || dados.situacao === 'futuro' || dados.situacao === 'sem_dados') {
        td.innerHTML = '<span class="gm-agora-vazio">—</span>';
        return;
      }
      var d = porLoja[tr.dataset.loja];
      var real = d ? (fixa ? d.qtd : d.valor) : 0;
      var alvo = alvoDaLinha(tr, fixa);
      celula(td, real, alvo, fixa, fixa && d ? 'TV ' + br(d.tv) + ' · Fixa ' + br(d.fixa) + ' · R$ ' + br(d.valor, 2) : '');
      var regiao = tr.dataset.regiao || 'Sem região';
      [regiao, 'REDE'].forEach(function (g) {
        somas[g] = somas[g] || {real: 0, valor: 0};
        somas[g].real += real || 0;
        somas[g].valor += d ? d.valor : 0;
      });
    });
    painel.querySelectorAll('tfoot tr[data-grupo]').forEach(function (tf) {
      var td = tf.querySelector('[data-agora-total]');
      if (!td) return;
      var s = somas[tf.dataset.grupo];
      if (!s || !dados || dados.situacao === 'futuro' || dados.situacao === 'sem_dados') { td.innerHTML = ''; return; }
      celula(td, s.real, alvoDaLinha(tf, fixa), fixa, fixa ? 'R$ ' + br(s.valor, 2) : '');
    });
  }

  function pintarTudo() { tela.querySelectorAll('.gm-painel').forEach(pintarPainel); }

  function status(html) {
    tela.querySelectorAll('[data-agora-status]').forEach(function (p) { p.innerHTML = html; });
  }

  function carregar(forcar) {
    status('<i class="fas fa-circle-notch fa-spin"></i> Buscando o realizado de ' + tela.dataset.mesAgora + ' no banco de vendas…');
    fetch(tela.dataset.urlAgora + (forcar ? '?atualizar=1' : ''), {credentials: 'same-origin', headers: {'Accept': 'application/json'}})
      .then(function (r) { return r.json(); })
      .then(function (d) {
        dados = d;
        var hora = d.atualizado_em ? new Date(d.atualizado_em).toLocaleTimeString('pt-BR', {hour: '2-digit', minute: '2-digit'}) : '';
        var atualizar = ' <button type="button" class="gm-botao pequeno" data-agora-atualizar><i class="fas fa-rotate"></i>Atualizar</button>';
        if (d.situacao === 'futuro') status('<i class="fas fa-calendar"></i> Realizado até o momento: ' + tela.dataset.mesAgora + ' ainda não começou.');
        else if (d.situacao === 'banco') status('<i class="fas fa-database"></i> Realizado até o momento: banco de vendas, atualizado às ' + hora + '.' + atualizar);
        else if (d.situacao === 'gravado') status('<i class="fas fa-triangle-exclamation"></i> Banco de vendas fora do ar: mostrando o realizado já importado de ' + tela.dataset.mesAgora + '.' + atualizar);
        else status('<i class="fas fa-triangle-exclamation"></i> Sem o realizado de ' + tela.dataset.mesAgora + ': o banco de vendas não respondeu.' + atualizar);
        pintarTudo();
      })
      .catch(function () {
        status('<i class="fas fa-triangle-exclamation"></i> Não deu para buscar o realizado agora. <button type="button" class="gm-botao pequeno" data-agora-atualizar><i class="fas fa-rotate"></i>Tentar de novo</button>');
      });
  }

  tela.addEventListener('click', function (e) {
    if (e.target.closest('[data-agora-atualizar]')) carregar(true);
  });
  // A meta mudou (digitada, recalculada, preenchida): o % acompanha.
  var espera = null;
  function depois() { clearTimeout(espera); espera = setTimeout(pintarTudo, 150); }
  tela.addEventListener('input', depois);
  tela.addEventListener('change', depois);
  new MutationObserver(function (mudancas) {
    if (mudancas.some(function (m) { return !(m.target.closest && m.target.closest('[data-agora], [data-agora-total], [data-agora-status]')) && !(m.target.parentElement && m.target.parentElement.closest('[data-agora], [data-agora-total]')); })) depois();
  }).observe(tela, {subtree: true, childList: true, characterData: true});

  carregar(false);
})();
