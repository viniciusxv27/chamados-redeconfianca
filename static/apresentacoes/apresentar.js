/*
 * Assistente de Apresentações — modo apresentação (window.APRES_APRESENTAR).
 *
 * Serve a página /apresentacoes/<id>/apresentar/ (aba própria, com tela de
 * início) e o botão "Apresentar" do editor, que abre por cima do editor com o
 * documento da memória — inclusive o que ainda não foi salvo. O desenho do
 * slide é sempre do APRES_RENDER, o mesmo do editor e da exportação.
 *
 * Teclas: → ↓ Espaço PageDown Enter avançam (primeiro as animações "ao clicar",
 * depois o próximo slide); ← ↑ PageUp Backspace voltam; Home/End; número +
 * Enter vai ao slide; N notas; A avanço automático (com a narração); F tela
 * cheia; B tela preta; Esc sai. No toque: lado direito avança, esquerdo volta,
 * arrastar troca de slide.
 */
(function () {
  'use strict';

  const R = window.APRES_RENDER;
  const LARG = 1920;
  const ALT = 1080;
  // Modo automático: tempo de um slide sem narração e o respiro depois que a fala termina.
  const ESPERA_SEM_NARRACAO = 6000;
  const PAUSA_DEPOIS_DA_FALA = 900;

  // ------------------------------------------------------------------ utilidades

  function num(v, padrao) { const n = Number(v); return Number.isFinite(n) ? n : padrao; }

  function h(tag, props) {
    const n = document.createElement(tag);
    const p = props || {};
    Object.keys(p).forEach((k) => {
      const v = p[k];
      if (v == null || v === false) return;
      if (k === 'class') n.className = v;
      else if (k === 'text') n.textContent = v;
      else if (k === 'attrs') Object.keys(v).forEach((a) => { if (v[a] != null && v[a] !== false) n.setAttribute(a, v[a] === true ? '' : v[a]); });
      else if (k === 'on') Object.keys(v).forEach((ev) => n.addEventListener(ev, v[ev]));
      else if (k === 'dica') { n.setAttribute('title', v); n.setAttribute('aria-label', v); }
      else if (k in n) n[k] = v;
      else n.setAttribute(k, v);
    });
    for (let i = 2; i < arguments.length; i += 1) {
      const f = arguments[i];
      if (f == null || f === false) continue;
      n.appendChild(f instanceof Node ? f : document.createTextNode(String(f)));
    }
    return n;
  }
  function icone(classe) { return h('i', { class: classe, attrs: { 'aria-hidden': 'true' } }); }

  /**
   * Documento pronto para tocar: listas sempre listas e todo slide com id.
   * Um registro antigo ou gravado fora do fluxo não pode derrubar a apresentação.
   */
  function normalizar(bruto) {
    const doc = bruto && typeof bruto === 'object' ? bruto : {};
    const tema = doc.tema && typeof doc.tema === 'object' ? doc.tema : {};
    const vistos = new Set();
    const slides = (Array.isArray(doc.slides) ? doc.slides : []).filter((s) => s && typeof s === 'object').map((s, i) => {
      let id = typeof s.id === 'string' && s.id ? s.id : 's-slide' + (i + 1);
      if (vistos.has(id)) id = id + '-' + (i + 1);
      vistos.add(id);
      return Object.assign({}, s, {
        id,
        oculto: !!s.oculto,
        elementos: Array.isArray(s.elementos) ? s.elementos.filter((el) => el && typeof el === 'object') : [],
      });
    });
    return {
      formato: doc.formato || 1, largura: LARG, altura: ALT,
      tema: Object.assign({}, tema, { cores: Object.assign({}, tema.cores && typeof tema.cores === 'object' ? tema.cores : {}) }),
      slides,
    };
  }

  /** GET de JSON com limite de tempo e mensagens em português (sessão expirada, servidor fora). */
  async function buscarJson(url, limite) {
    if (!url) throw new Error('Endereço da apresentação ausente.');
    const controle = typeof AbortController === 'function' ? new AbortController() : null;
    const relogio = setTimeout(() => { if (controle) controle.abort(); }, limite || 30000);
    let resp;
    try {
      resp = await fetch(url, { credentials: 'same-origin', headers: { Accept: 'application/json' }, signal: controle ? controle.signal : undefined });
    } catch (e) {
      throw new Error(e && e.name === 'AbortError' ? 'O servidor demorou demais para responder. Tente de novo.' : 'Sem conexão com o servidor. Confira a internet e tente de novo.');
    } finally {
      clearTimeout(relogio);
    }
    let dados = null;
    try { dados = await resp.json(); } catch (e) { dados = null; }
    if (!dados && resp.redirected && /login/i.test(resp.url)) {
      throw new Error('Sua sessão expirou. Entre de novo no portal e recarregue esta página.');
    }
    if (!resp.ok || !dados || dados.ok === false) {
      throw new Error((dados && dados.erro) || (resp.status >= 500 ? 'O servidor teve um problema. Tente de novo em instantes.' : 'Não foi possível abrir a apresentação (erro ' + resp.status + ').'));
    }
    return dados;
  }

  function telaCheiaAtiva() { return !!(document.fullscreenElement || document.webkitFullscreenElement); }

  function entrarTelaCheia() {
    const alvo = document.documentElement;
    const pedir = alvo.requestFullscreen || alvo.webkitRequestFullscreen;
    if (!pedir) return Promise.resolve(false);
    if (telaCheiaAtiva()) return Promise.resolve(true);
    try {
      return Promise.resolve(pedir.call(alvo)).then(() => true, () => false);
    } catch (e) {
      return Promise.resolve(false);
    }
  }

  function sairTelaCheia() {
    if (!telaCheiaAtiva()) return;
    const sair = document.exitFullscreen || document.webkitExitFullscreen;
    try {
      const p = sair && sair.call(document);
      if (p && p.catch) p.catch(() => {});
    } catch (e) { /* já saiu */ }
  }

  /** Busca as imagens de todos os slides enquanto a tela de início está aberta (sem buraco na hora de passar). */
  function preCarregarImagens(doc) {
    const urls = new Set();
    const add = (src) => { const u = R.resolverSrc(src); if (u) urls.add(u); };
    doc.slides.forEach((s) => {
      if (s.fundo) add(s.fundo.imagem);
      s.elementos.forEach((el) => {
        if (el.tipo === 'imagem') add(el.src);
        if (el.tipo === 'video') add(el.poster);
      });
    });
    urls.forEach((u) => { const img = new Image(); img.decoding = 'async'; img.src = u; });
  }

  // ------------------------------------------------------------------ apresentação

  /**
   * Abre a apresentação por cima da página. opcoes: titulo, inicioId (slide de onde começar),
   * template (mostra todos os layouts e as marcações do template), narracao (liga o automático),
   * telaCheia (padrão true), rotuloSair, aoFechar(idDoSlideAtual).
   */
  function abrir(docBruto, opcoes) {
    const o = opcoes || {};
    const doc = normalizar(docBruto);
    const template = !!o.template;
    const lista = template ? doc.slides : doc.slides.filter((s) => !s.oculto);
    const temNarracao = lista.some((s) => s.narracao && R.resolverSrc(s.narracao.url));

    const E = {
      indice: -1, passo: 0, plano: [[]], nodo: null, ocupado: false, pendente: null, animando: null,
      notas: false, auto: false, audio: null, timersAuto: [], tokenAuto: 0, fim: null, preto: null,
      fechado: false, telaCheiaPropria: false, digitos: '', timerDigitos: null, timerControles: null,
      timerAviso: null, proximo: null, ultimaRoda: 0, toque: null, tipoPonteiro: 'mouse',
    };

    // Depois do clique o foco volta para a apresentação: senão o Espaço seguinte "aperta" o botão de novo.
    const botao = (classe, dica, acao) => h('button', {
      type: 'button', dica,
      on: { click: (ev) => { ev.stopPropagation(); acao(); if (ev.detail > 0 && !E.fim) raiz.focus({ preventScroll: true }); } },
    }, icone(classe));
    const palco = h('div', { class: 'ap-palco' });
    const progresso = h('div', { class: 'ap-progresso', attrs: { 'aria-hidden': 'true' } });
    const falado = h('div', { class: 'ed-sr', attrs: { 'aria-live': 'polite' } });
    const notas = h('div', { class: 'ap-notas', hidden: true });
    const aviso = h('div', { class: 'ap-aviso', hidden: true, attrs: { role: 'status' } });
    const bAnterior = botao('fa-solid fa-chevron-left', 'Voltar (←)', () => voltar());
    const contador = h('span', { class: 'ap-contador', attrs: { 'aria-hidden': 'true' } });
    const bProximo = botao('fa-solid fa-chevron-right', 'Avançar (→ ou Espaço)', () => avancar());
    const bNotas = botao('fa-solid fa-note-sticky', 'Notas do apresentador (N)', () => alternarNotas());
    const bAuto = botao(temNarracao ? 'fa-solid fa-microphone' : 'fa-solid fa-forward',
      temNarracao ? 'Narração automática (A)' : 'Avançar sozinho (A)', () => alternarAuto());
    const bTela = botao('fa-solid fa-expand', 'Tela cheia (F)', () => alternarTelaCheia());
    const bSair = botao('fa-solid fa-xmark', (o.rotuloSair || 'Sair') + ' (Esc)', () => fechar());
    const controles = h('div', { class: 'ap-controles', attrs: { role: 'toolbar', 'aria-label': 'Controles da apresentação' } },
      bAnterior, contador, bProximo, bNotas, bAuto, bTela, bSair);
    const raiz = h('div', {
      class: 'ap-raiz',
      attrs: { role: 'region', 'aria-roledescription': 'apresentação', 'aria-label': o.titulo || 'Apresentação', tabindex: '-1' },
    }, palco, progresso, controles, notas, aviso, falado);

    // -------------------------------------------------------------- desenho e escala

    function ajustar() {
      const w = raiz.clientWidth || window.innerWidth;
      const hh = raiz.clientHeight || window.innerHeight;
      const s = Math.min(w / LARG, hh / ALT);
      palco.style.transform = 'translate(' + ((w - LARG * s) / 2) + 'px, ' + ((hh - ALT * s) / 2) + 'px) scale(' + s + ')';
    }

    function criarNodo(i) {
      const s = lista[i];
      const numero = template ? { n: i + 1, total: lista.length } : R.numeracao(doc, s.id);
      return R.criarSlide(s, doc, { modo: 'apresentacao', template, indice: numero.n, total: numero.total });
    }

    function tocarMidias(nodo) {
      if (!nodo) return;
      nodo.querySelectorAll('video[data-autoplay]').forEach((v) => {
        try { v.currentTime = 0; } catch (e) { /* ainda sem metadados */ }
        const p = v.play();
        // Vídeo com som pode ser barrado pelo navegador: toca mudo em vez de ficar parado.
        if (p && p.catch) p.catch(() => { if (!v.muted) { v.muted = true; v.play().catch(() => {}); } });
      });
    }
    function pausarMidias(nodo) {
      if (nodo) nodo.querySelectorAll('video, audio').forEach((m) => { try { m.pause(); } catch (e) { /* ok */ } });
    }

    function mostrarAviso(texto, ms) {
      aviso.textContent = texto;
      aviso.hidden = false;
      clearTimeout(E.timerAviso);
      E.timerAviso = setTimeout(() => { aviso.hidden = true; }, ms || 2600);
    }

    function atualizarInterface() {
      const total = lista.length;
      const i = Math.max(0, E.indice);
      contador.textContent = (i + 1) + ' / ' + total;
      progresso.style.width = ((E.fim ? total : i + 1) / total) * 100 + '%';
      bAnterior.disabled = i === 0 && !E.fim;
      bProximo.disabled = !!E.fim;
      bNotas.setAttribute('aria-pressed', E.notas ? 'true' : 'false');
      bAuto.setAttribute('aria-pressed', E.auto ? 'true' : 'false');
      const cheia = telaCheiaAtiva();
      bTela.firstChild.className = cheia ? 'fa-solid fa-compress' : 'fa-solid fa-expand';
      bTela.setAttribute('title', cheia ? 'Sair da tela cheia (F)' : 'Tela cheia (F)');
      bTela.setAttribute('aria-label', bTela.getAttribute('title'));
      const s = lista[i];
      notas.textContent = '';
      notas.append(h('b', { text: 'Notas · slide ' + (i + 1) }), document.createTextNode((s && typeof s.notas === 'string' && s.notas.trim()) || 'Sem notas neste slide.'));
      notas.hidden = !E.notas;
    }

    // -------------------------------------------------------------- animações do slide

    function mostrarSemAnimar(nodoEl) {
      const anim = nodoEl && nodoEl.querySelector('.apres-anim');
      if (!anim) return;
      anim.classList.remove('apres-anim-pendente');
      if (anim._apresAnimacao) { try { anim._apresAnimacao.finish(); } catch (e) { /* já terminou */ } }
    }

    /** Toca um passo (lotes em sequência; cada lote entra junto). Um avanço no meio completa o passo. */
    async function rodarPasso(k) {
      const nodo = E.nodo;
      const passo = E.plano[k] || [];
      E.passo = Math.max(E.passo, k + 1);
      const execucao = { pular: false, pares: [] };
      E.animando = execucao;
      try {
        for (const lote of passo) {
          const pares = lote.map((el) => ({ el, n: nodo.querySelector('.apres-el[data-id="' + R.cssEscape(el.id) + '"]') })).filter((p) => p.n);
          if (execucao.pular || E.fechado || E.nodo !== nodo) {
            pares.forEach((p) => mostrarSemAnimar(p.n));
            continue;
          }
          execucao.pares = pares;
          await Promise.all(pares.map((p) => R.animarEntrada(p.n, p.el.animacao)));
        }
      } finally {
        if (E.animando === execucao) E.animando = null;
      }
    }

    function completarPasso() {
      const execucao = E.animando;
      if (!execucao) return;
      execucao.pular = true;
      execucao.pares.forEach((p) => mostrarSemAnimar(p.n));
      E.animando = null;
    }

    function mostrarPassoSemAnimar(k) {
      const nodo = E.nodo;
      (E.plano[k] || []).forEach((lote) => lote.forEach((el) => {
        mostrarSemAnimar(nodo && nodo.querySelector('.apres-el[data-id="' + R.cssEscape(el.id) + '"]'));
      }));
      E.passo = Math.max(E.passo, k + 1);
    }

    /** Mostra o que falta do slide de uma vez (usado ao sair do slide no automático). */
    function completarSlide() {
      completarPasso();
      if (E.nodo) R.finalizarAnimacoes(E.nodo);
      E.passo = E.plano.length;
    }

    // -------------------------------------------------------------- navegação

    /** Navegação durante uma transição fica na fila (a última pedida vale). */
    function executar(fn) {
      if (E.fechado) return;
      if (E.ocupado) { E.pendente = fn; return; }
      fn();
    }

    async function irPara(i, opcoesIr) {
      const op = opcoesIr || {};
      if (E.fechado || i < 0 || i >= lista.length) return;
      esconderFim();
      pararAuto();
      const anterior = E.nodo;
      const indiceAnterior = E.indice;
      const direcao = op.direcao || (i >= indiceAnterior ? 1 : -1);
      pausarMidias(anterior);
      const reaproveitar = E.proximo && E.proximo.i === i && !op.finalizado;
      const nodo = reaproveitar ? E.proximo.nodo : criarNodo(i);
      E.proximo = null;
      E.indice = i;
      E.plano = R.planoAnimacoes(lista[i], { template });
      E.passo = 0;
      E.nodo = nodo;
      E.animando = null;
      if (op.finalizado) { R.finalizarAnimacoes(nodo); E.passo = E.plano.length; }
      palco.appendChild(nodo);
      atualizarInterface();
      falado.textContent = 'Slide ' + (i + 1) + ' de ' + lista.length;
      if (anterior && anterior !== nodo) {
        E.ocupado = true;
        try {
          // Um instante para as imagens do próximo slide: a transição não mostra buraco.
          await R.aguardarMidias(nodo, 700);
          if (E.fechado) return;
          const transicao = op.semTransicao ? null : direcao > 0 ? lista[i].transicao : (lista[indiceAnterior] || {}).transicao;
          await R.transicionar(anterior, nodo, transicao, direcao);
        } finally {
          anterior.remove();
          E.ocupado = false;
        }
        if (E.fechado) return;
      }
      tocarMidias(nodo);
      if (E.pendente) {
        // Avanço pedido durante a transição: o que entra "ao abrir" aparece já pronto e o pedido segue.
        const fn = E.pendente;
        E.pendente = null;
        if (!op.finalizado) mostrarPassoSemAnimar(0);
        fn();
      } else if (!op.finalizado) {
        await rodarPasso(0);
      }
      if (E.fechado || E.nodo !== nodo || E.ocupado) return;
      if (E.indice + 1 < lista.length && !E.proximo) E.proximo = { i: E.indice + 1, nodo: criarNodo(E.indice + 1) };
      if (E.auto && !E.fim) agendarAuto();
    }

    function avancar() {
      if (E.fechado || E.fim) return;
      if (E.ocupado) { E.pendente = avancar; return; }
      if (E.animando) { completarPasso(); return; }
      if (E.passo < E.plano.length) { rodarPasso(E.passo); return; }
      if (E.indice < lista.length - 1) irPara(E.indice + 1, { direcao: 1 });
      else mostrarFim();
    }

    function voltar() {
      if (E.fechado) return;
      if (E.fim) { esconderFim(); atualizarInterface(); return; }
      if (E.ocupado) { E.pendente = voltar; return; }
      if (E.indice > 0) irPara(E.indice - 1, { direcao: -1, finalizado: true });
    }

    /** Link "#slide-N": N é a posição no documento (conta os ocultos, como o editor oferece). */
    function irParaPosicaoNoDocumento(n) {
      const alvo = doc.slides[n - 1];
      if (!alvo) { mostrarAviso('O slide ' + n + ' não existe nesta apresentação.'); return; }
      let i = lista.indexOf(alvo);
      if (i < 0) {
        const posicao = doc.slides.indexOf(alvo);
        i = lista.findIndex((s) => doc.slides.indexOf(s) > posicao);
        if (i < 0) i = lista.length - 1;
      }
      executar(() => irPara(i, { direcao: i >= E.indice ? 1 : -1 }));
    }

    function seguirLink(link) {
      const valor = String(link || '').trim();
      const m = /^#slide-(\d{1,4})$/.exec(valor);
      if (m) { irParaPosicaoNoDocumento(Number(m[1])); return true; }
      if (R.linkValido(valor)) { window.open(valor, '_blank', 'noopener,noreferrer'); return true; }
      return false;
    }

    function mostrarFim() {
      if (E.fim) return;
      pararAuto();
      pausarMidias(E.nodo);
      const acao = (classe, texto, fn) => h('button', { type: 'button', on: { click: (ev) => { ev.stopPropagation(); fn(); } } }, icone(classe), ' ' + texto);
      E.fim = h('div', { class: 'ap-fim', attrs: { role: 'dialog', 'aria-label': 'Fim da apresentação' } },
        h('h2', { text: 'Fim da apresentação' }),
        h('div', {},
          acao('fa-solid fa-chevron-left', 'Voltar ao último slide', () => voltar()),
          acao('fa-solid fa-rotate-left', 'Recomeçar', () => executar(() => irPara(0, { direcao: -1 }))),
          acao('fa-solid fa-xmark', o.rotuloSair || 'Sair', () => fechar())));
      raiz.appendChild(E.fim);
      atualizarInterface();
      falado.textContent = 'Fim da apresentação';
      const primeiro = E.fim.querySelector('button');
      if (primeiro) primeiro.focus({ preventScroll: true });
    }

    function esconderFim() {
      if (!E.fim) return;
      E.fim.remove();
      E.fim = null;
      raiz.focus({ preventScroll: true });
    }

    // -------------------------------------------------------------- notas, automático, tela preta, tela cheia

    function alternarNotas() {
      E.notas = !E.notas;
      atualizarInterface();
    }

    function pararAuto() {
      E.tokenAuto += 1;
      E.timersAuto.forEach((t) => clearTimeout(t));
      E.timersAuto = [];
      if (E.audio) {
        const a = E.audio;
        E.audio = null;
        a.onended = null;
        a.onerror = null;
        try { a.pause(); } catch (e) { /* ok */ }
      }
    }

    function depois(ms, fn) { E.timersAuto.push(setTimeout(fn, ms)); }

    /** Automático: a fala do slide (ou um tempo fixo) e, no meio dela, as animações "ao clicar". */
    function agendarAuto() {
      pararAuto();
      if (!E.auto || E.fechado || E.fim || E.indice < 0) return;
      const token = E.tokenAuto;
      const s = lista[E.indice];
      const url = s.narracao && R.resolverSrc(s.narracao.url);
      const restantes = Math.max(0, E.plano.length - E.passo);
      const proximo = () => {
        if (token !== E.tokenAuto || !E.auto || E.fechado) return;
        completarSlide();
        if (E.indice < lista.length - 1) irPara(E.indice + 1, { direcao: 1 });
        else { E.auto = false; mostrarFim(); }
      };
      const espalharPassos = (total) => {
        for (let k = 1; k <= restantes; k += 1) {
          depois((k * total) / (restantes + 1), () => {
            if (token === E.tokenAuto && E.passo < E.plano.length) { completarPasso(); rodarPasso(E.passo); }
          });
        }
      };
      if (!url) {
        const tempo = Math.max(ESPERA_SEM_NARRACAO, restantes * 1500 + 2500);
        espalharPassos(tempo);
        depois(tempo, proximo);
        return;
      }
      const audio = new Audio(url);
      E.audio = audio;
      const duracao = num(s.narracao.duracao, 0) * 1000;
      espalharPassos(duracao > 0 ? duracao : ESPERA_SEM_NARRACAO);
      audio.onended = () => { if (token === E.tokenAuto) depois(PAUSA_DEPOIS_DA_FALA, proximo); };
      audio.onerror = () => {
        if (token !== E.tokenAuto) return;
        mostrarAviso('A narração deste slide não carregou; seguindo sem ela.');
        depois(duracao > 0 ? duracao : ESPERA_SEM_NARRACAO, proximo);
      };
      const p = audio.play();
      if (p && p.catch) {
        p.catch(() => {
          if (token !== E.tokenAuto) return;
          E.auto = false;
          pararAuto();
          atualizarInterface();
          mostrarAviso('O navegador bloqueou o áudio. Aperte A para ligar a narração de novo.', 4000);
        });
      }
    }

    function alternarAuto() {
      E.auto = !E.auto;
      atualizarInterface();
      if (E.auto) {
        mostrarAviso(temNarracao ? 'Narração automática ligada' : 'Avanço automático ligado');
        if (!E.ocupado && !E.fim) agendarAuto();
      } else {
        pararAuto();
        mostrarAviso('Automático desligado');
      }
    }

    function alternarPreto() {
      if (E.preto) { E.preto.remove(); E.preto = null; return; }
      E.preto = h('div', { class: 'ap-preto', attrs: { 'aria-label': 'Tela preta (B volta)' } });
      raiz.appendChild(E.preto);
    }

    function alternarTelaCheia() {
      if (telaCheiaAtiva()) { sairTelaCheia(); return; }
      entrarTelaCheia().then((ok) => {
        if (ok) E.telaCheiaPropria = true;
        else mostrarAviso('O navegador não liberou a tela cheia. Tente F11.');
      });
    }

    function aoMudarTelaCheia() {
      ajustar();
      atualizarInterface();
    }

    // -------------------------------------------------------------- eventos

    function mexeu() {
      raiz.classList.add('ap-mostrar-controles');
      raiz.classList.remove('ap-cursor-oculto');
      clearTimeout(E.timerControles);
      E.timerControles = setTimeout(() => {
        raiz.classList.remove('ap-mostrar-controles');
        raiz.classList.add('ap-cursor-oculto');
      }, 2600);
    }

    function dentroDaInterface(alvo) {
      return !!(alvo && alvo.closest && alvo.closest('.ap-controles, .ap-fim, .ap-notas'));
    }

    function aoPressionar(ev) {
      E.tipoPonteiro = ev.pointerType || 'mouse';
      mexeu();
      if (ev.pointerType === 'mouse' && ev.button !== 0) return;
      if (dentroDaInterface(ev.target)) return;
      E.toque = { x: ev.clientX, y: ev.clientY, id: ev.pointerId };
    }

    function aoSoltar(ev) {
      const t = E.toque;
      if (!t || ev.pointerId !== t.id) return;
      E.toque = null;
      if (dentroDaInterface(ev.target)) return;
      if (E.preto) { alternarPreto(); return; }
      const dx = ev.clientX - t.x;
      const dy = ev.clientY - t.y;
      if (Math.abs(dx) > 60 && Math.abs(dx) > Math.abs(dy) * 1.2) { if (dx < 0) avancar(); else voltar(); return; }
      if (Math.hypot(dx, dy) > 14) return;
      const alvo = ev.target;
      // Link de texto para site abre sozinho (target _blank); aqui só não deixa o clique avançar.
      const ancora = alvo.closest && alvo.closest('a[href]');
      if (ancora) {
        const href = ancora.getAttribute('href') || '';
        if (/^#slide-\d+$/.test(href)) seguirLink(href);
        return;
      }
      const comLink = alvo.closest && alvo.closest('.apres-com-link');
      if (comLink && seguirLink(comLink.getAttribute('data-link'))) return;
      if (alvo.closest && alvo.closest('video[controls]')) return;
      if (ev.pointerType !== 'mouse' && ev.clientX < raiz.clientWidth * 0.3) voltar();
      else avancar();
    }

    function aoClicar(ev) {
      // "#slide-N" dentro de texto não pode virar âncora da página.
      const ancora = ev.target.closest && ev.target.closest('a[href]');
      if (ancora && /^#/.test(ancora.getAttribute('href') || '')) ev.preventDefault();
    }

    function aoMenuContexto(ev) {
      ev.preventDefault();
      if (E.tipoPonteiro === 'mouse' && !dentroDaInterface(ev.target)) voltar();
    }

    function aoRolar(ev) {
      if (Math.abs(ev.deltaY) < 8 || dentroDaInterface(ev.target)) return;
      ev.preventDefault();
      const agora = Date.now();
      if (agora - E.ultimaRoda < 450) return;
      E.ultimaRoda = agora;
      if (ev.deltaY > 0) avancar(); else voltar();
    }

    function aoTeclar(ev) {
      if (E.fechado || ev.defaultPrevented) return;
      if (ev.ctrlKey || ev.metaKey || ev.altKey) return;
      const naInterface = dentroDaInterface(ev.target) && ev.target.tagName === 'BUTTON';
      if (naInterface && (ev.key === 'Enter' || ev.key === ' ')) return;
      let tratado = true;
      mexeu();
      switch (ev.key) {
        case 'ArrowRight': case 'ArrowDown': case 'PageDown': case ' ': case 'Spacebar':
          avancar(); break;
        case 'Enter':
          if (E.digitos) {
            const n = Number(E.digitos);
            E.digitos = '';
            if (n >= 1 && n <= lista.length) executar(() => irPara(n - 1, { direcao: n - 1 >= E.indice ? 1 : -1 }));
            else mostrarAviso('Não há slide ' + n + '.');
          } else avancar();
          break;
        case 'ArrowLeft': case 'ArrowUp': case 'PageUp': case 'Backspace':
          voltar(); break;
        case 'Home':
          executar(() => irPara(0, { direcao: -1 })); break;
        case 'End':
          executar(() => irPara(lista.length - 1, { direcao: 1, finalizado: true })); break;
        case 'n': case 'N':
          alternarNotas(); break;
        case 'a': case 'A':
          alternarAuto(); break;
        case 'f': case 'F':
          alternarTelaCheia(); break;
        case 'b': case 'B': case '.':
          alternarPreto(); break;
        case 'Escape':
          if (E.preto) alternarPreto(); else fechar();
          break;
        default:
          if (/^[0-9]$/.test(ev.key)) {
            E.digitos = (E.digitos + ev.key).slice(-4);
            clearTimeout(E.timerDigitos);
            E.timerDigitos = setTimeout(() => { E.digitos = ''; }, 3000);
            mostrarAviso('Ir para o slide ' + E.digitos + ' — aperte Enter');
          } else {
            tratado = false;
          }
      }
      if (tratado) { ev.preventDefault(); ev.stopPropagation(); }
    }

    function fechar() {
      if (E.fechado) return;
      E.fechado = true;
      pararAuto();
      pausarMidias(E.nodo);
      [E.timerControles, E.timerAviso, E.timerDigitos].forEach((t) => clearTimeout(t));
      window.removeEventListener('keydown', aoTeclar, true);
      window.removeEventListener('resize', ajustar);
      document.removeEventListener('fullscreenchange', aoMudarTelaCheia);
      document.removeEventListener('webkitfullscreenchange', aoMudarTelaCheia);
      if (E.telaCheiaPropria) sairTelaCheia();
      raiz.remove();
      const s = lista[E.indice];
      if (o.aoFechar) o.aoFechar(s ? s.id : null);
    }

    raiz.addEventListener('pointermove', mexeu);
    raiz.addEventListener('pointerdown', aoPressionar);
    raiz.addEventListener('pointerup', aoSoltar);
    raiz.addEventListener('pointercancel', () => { E.toque = null; });
    raiz.addEventListener('click', aoClicar);
    raiz.addEventListener('contextmenu', aoMenuContexto);
    raiz.addEventListener('wheel', aoRolar, { passive: false });
    window.addEventListener('keydown', aoTeclar, true);
    window.addEventListener('resize', ajustar);
    document.addEventListener('fullscreenchange', aoMudarTelaCheia);
    document.addEventListener('webkitfullscreenchange', aoMudarTelaCheia);

    const controle = {
      fechar,
      irPara: (i) => executar(() => irPara(i)),
      get indice() { return E.indice; },
      get total() { return lista.length; },
    };

    document.body.appendChild(raiz);
    if (!lista.length) {
      // Quem chama já confere; se ainda assim não houver slide, a tela explica em vez de ficar preta.
      E.fim = h('div', { class: 'ap-fim', attrs: { role: 'alert' } },
        h('h2', { text: doc.slides.length ? 'Todos os slides estão ocultos' : 'Não há slides para apresentar' }),
        h('div', {}, h('button', { type: 'button', on: { click: fechar } }, icone('fa-solid fa-xmark'), ' ' + (o.rotuloSair || 'Sair'))));
      raiz.appendChild(E.fim);
      return controle;
    }
    ajustar();
    if (o.telaCheia !== false) entrarTelaCheia().then((ok) => { if (ok) { E.telaCheiaPropria = true; ajustar(); atualizarInterface(); } });
    let inicio = 0;
    if (o.inicioId) {
      const alvo = doc.slides.find((s) => s.id === o.inicioId);
      const i = alvo ? lista.indexOf(alvo) : -1;
      if (i >= 0) inicio = i;
      else if (alvo) {
        // Começar num slide oculto: vai para o próximo visível.
        const posicao = doc.slides.indexOf(alvo);
        const seguinte = lista.findIndex((s) => doc.slides.indexOf(s) > posicao);
        inicio = seguinte >= 0 ? seguinte : lista.length - 1;
      }
    }
    E.auto = !!o.narracao;
    raiz.focus({ preventScroll: true });
    mexeu();
    irPara(inicio, { direcao: 1 });
    return controle;
  }

  // ------------------------------------------------------------------ página própria (/apresentar/)

  function limpar(app) {
    app.textContent = '';
    app.removeAttribute('aria-busy');
    app.hidden = false;
  }

  /** Tela de mensagem no lugar da apresentação: carregando, nada para mostrar ou erro com o que fazer. */
  function mostrarMensagem(app, m) {
    limpar(app);
    const caixa = h('div', { class: 'ap-inicio', attrs: { role: m.carregando ? 'status' : 'alert' } });
    if (m.carregando) {
      app.setAttribute('aria-busy', 'true');
      caixa.appendChild(h('span', { class: 'ed-giro', attrs: { 'aria-hidden': 'true' } }));
    } else if (m.icone) {
      caixa.appendChild(h('i', { class: m.icone + ' ap-icone-grande', attrs: { 'aria-hidden': 'true' } }));
    }
    if (m.titulo) caixa.appendChild(h('h1', { text: m.titulo }));
    if (m.texto) caixa.appendChild(h('p', { text: m.texto }));
    if (m.acoes && m.acoes.length) {
      const acoes = h('div', { class: 'ap-acoes' });
      m.acoes.forEach((a, i) => {
        if (!a) return;
        if (a.href) {
          acoes.appendChild(h('a', { class: 'ap-link' + (i === 0 ? ' ap-primario' : ''), href: a.href }, a.icone ? icone(a.icone) : null, a.texto));
        } else {
          acoes.appendChild(h('button', { type: 'button', class: i === 0 ? '' : 'ap-secundario', on: { click: a.acao } }, a.icone ? icone(a.icone) : null, a.texto));
        }
      });
      caixa.appendChild(acoes);
    }
    app.appendChild(caixa);
    const foco = caixa.querySelector('button, a');
    if (foco) foco.focus({ preventScroll: true });
  }

  function minutos(segundos) {
    const m = Math.round(segundos / 60);
    return m <= 1 ? 'cerca de 1 minuto' : 'cerca de ' + m + ' minutos';
  }

  function telaInicial(app, doc, titulo) {
    const cfg = window.APRES || {};
    const urls = cfg.urls || {};
    const visiveis = doc.slides.filter((s) => !s.oculto);
    const narrados = visiveis.filter((s) => s.narracao && R.resolverSrc(s.narracao.url));
    const fala = narrados.reduce((soma, s) => soma + num(s.narracao.duracao, 0), 0);
    limpar(app);
    const comecar = (narracao) => {
      app.hidden = true;
      abrir(doc, {
        titulo, narracao, rotuloSair: 'Sair',
        aoFechar: () => { telaInicial(app, doc, titulo); },
      });
    };
    const botaoComecar = h('button', { type: 'button', on: { click: () => comecar(false) } }, icone('fa-solid fa-play'), 'Começar');
    const acoes = h('div', { class: 'ap-acoes' }, botaoComecar,
      narrados.length ? h('button', { type: 'button', class: 'ap-secundario', on: { click: () => comecar(true) } }, icone('fa-solid fa-microphone'), 'Com narração') : null);
    const detalhe = visiveis.length + (visiveis.length === 1 ? ' slide' : ' slides')
      + (narrados.length ? ' · narração em ' + narrados.length + ' (' + minutos(fala) + ')' : '');
    const dica = h('small', {},
      h('kbd', { text: '→' }), ' avança  ', h('kbd', { text: '←' }), ' volta  ', h('kbd', { text: 'F' }), ' tela cheia  ',
      h('kbd', { text: 'N' }), ' notas  ', h('kbd', { text: 'A' }), ' automático  ', h('kbd', { text: 'Esc' }), ' sai');
    app.appendChild(h('div', { class: 'ap-inicio' },
      h('h1', { text: titulo }),
      h('p', { text: detalhe }),
      acoes,
      dica,
      urls.voltar ? h('a', { class: 'ap-link', href: urls.voltar }, icone('fa-solid fa-pen-to-square'), 'Abrir no editor') : null));
    botaoComecar.focus({ preventScroll: true });
  }

  async function iniciarPagina(app) {
    const cfg = window.APRES || {};
    const urls = cfg.urls || {};
    const alvo = app || document.getElementById('ap-app');
    if (!alvo) return;
    mostrarMensagem(alvo, { carregando: true, texto: 'Abrindo a apresentação…' });
    let resposta;
    try {
      resposta = await buscarJson(urls.documento, 30000);
    } catch (erro) {
      mostrarMensagem(alvo, {
        icone: 'fa-solid fa-triangle-exclamation', titulo: 'Não foi possível abrir a apresentação', texto: erro.message,
        acoes: [
          { texto: 'Tentar de novo', icone: 'fa-solid fa-rotate', acao: () => iniciarPagina(alvo) },
          urls.voltar ? { texto: 'Abrir o editor', icone: 'fa-solid fa-pen-to-square', href: urls.voltar } : null,
        ],
      });
      return;
    }
    const doc = normalizar(resposta.documento);
    const titulo = resposta.titulo || cfg.titulo || 'Apresentação';
    if (!doc.slides.some((s) => !s.oculto)) {
      mostrarMensagem(alvo, {
        icone: doc.slides.length ? 'fa-solid fa-eye-slash' : 'fa-solid fa-file-circle-plus',
        titulo: doc.slides.length ? 'Todos os slides estão ocultos' : 'Esta apresentação ainda não tem slides',
        texto: doc.slides.length ? 'Mostre ao menos um slide no editor para apresentar.' : 'Abra o editor para criar os slides ou pedir à IA.',
        acoes: [
          urls.voltar ? { texto: 'Abrir o editor', icone: 'fa-solid fa-pen-to-square', href: urls.voltar } : null,
          urls.inicio ? { texto: 'Voltar às apresentações', icone: 'fa-solid fa-arrow-left', href: urls.inicio } : null,
        ],
      });
      return;
    }
    preCarregarImagens(doc);
    mostrarMensagem(alvo, { carregando: true, texto: 'Carregando as fontes…' });
    try {
      await R.carregarFontes(doc, { limite: 8000 });
    } catch (e) {
      // Sem a fonte do Google, o slide sai na fonte reserva: melhor que não apresentar.
    }
    telaInicial(alvo, doc, titulo);
  }

  window.APRES_APRESENTAR = { abrir, iniciarPagina, normalizar, buscarJson };
})();
