/*
 * Assistente de Apresentações — editor de slides (window.APRES_EDITOR).
 *
 * Organização (um arquivo só, sem bundler):
 *   A. utilidades, API, estado, histórico, salvamento, popovers/modais/avisos
 *   B. interface, quadro, seleção e interação (arrastar/redimensionar/girar), texto no quadro
 *   C. painel de propriedades, seletores (cor, fonte, ícone, mídia) e inserção
 *   D. assistente de IA, geração inicial, exportação, template, celular e início
 *
 * O documento em memória (E.doc) é a verdade da tela: toda alteração passa por
 * `commit` (histórico de até 100 estados) e o salvamento automático manda o
 * documento inteiro com a revisão (409 = alterado em outra aba).
 * O desenho do slide é sempre do APRES_RENDER, o mesmo da apresentação e do PDF.
 */
(function () {
  'use strict';

  const APRES = window.APRES || {};
  const R = window.APRES_RENDER;
  const URLS = APRES.urls || {};
  const MODO_TEMPLATE = APRES.modo === 'template';
  const LARG = 1920;
  const ALT = 1080;
  const MAX_HISTORICO = 100;
  const TAM_MIN = 8;

  // =================================================================== A. utilidades

  function $(sel, raiz) { return (raiz || document).querySelector(sel); }
  function $$(sel, raiz) { return Array.prototype.slice.call((raiz || document).querySelectorAll(sel)); }
  function num(v, padrao) { const n = Number(v); return Number.isFinite(n) ? n : padrao; }
  function limitar(v, min, max) { return Math.min(max, Math.max(min, v)); }
  function arred(v, casas) { const f = Math.pow(10, casas == null ? 2 : casas); return Math.round(num(v, 0) * f) / f; }
  function esperar(ms) { return new Promise((r) => setTimeout(r, ms)); }
  function clonar(v) { return v == null ? v : JSON.parse(JSON.stringify(v)); }
  function trocarId(url, id) { return String(url || '').replace('/0/', '/' + encodeURIComponent(id) + '/'); }
  function hora(d) { return (d || new Date()).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' }); }
  function semAcento(t) { return String(t || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase(); }

  function novoId(prefixo) {
    const bytes = new Uint8Array(4);
    (window.crypto || window.msCrypto).getRandomValues(bytes);
    return prefixo + '-' + Array.prototype.map.call(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
  }

  function debounce(fn, ms) {
    let t = null;
    const f = function () {
      const args = arguments;
      clearTimeout(t);
      t = setTimeout(() => { t = null; fn.apply(null, args); }, ms);
    };
    f.cancelar = () => { clearTimeout(t); t = null; };
    f.pendente = () => t !== null;
    return f;
  }

  function ehEditavel(alvo) {
    if (!alvo || !alvo.closest) return false;
    if (alvo.isContentEditable) return true;
    const tag = alvo.tagName;
    if (tag === 'TEXTAREA' || tag === 'SELECT') return true;
    if (tag === 'INPUT') return !/^(button|checkbox|radio|range|color|file|submit|reset)$/i.test(alvo.type || '');
    return false;
  }

  /** Cria elemento. props: class, text, attrs, style, on, dataset, dica (tooltip + aria-label) e propriedades DOM comuns. */
  function h(tag, props) {
    const n = document.createElement(tag);
    const p = props || {};
    Object.keys(p).forEach((k) => {
      const v = p[k];
      if (v == null || v === false) return;
      if (k === 'class') n.className = v;
      else if (k === 'text') n.textContent = v;
      else if (k === 'attrs') Object.keys(v).forEach((a) => { if (v[a] != null && v[a] !== false) n.setAttribute(a, v[a] === true ? '' : v[a]); });
      else if (k === 'style') Object.assign(n.style, v);
      else if (k === 'on') Object.keys(v).forEach((ev) => n.addEventListener(ev, v[ev]));
      else if (k === 'dataset') Object.assign(n.dataset, v);
      else if (k === 'dica') { n.setAttribute('data-dica', v); if (!n.hasAttribute('aria-label')) n.setAttribute('aria-label', v); }
      else if (k in n) n[k] = v;
      else n.setAttribute(k, v);
    });
    for (let i = 2; i < arguments.length; i += 1) anexar(n, arguments[i]);
    return n;
  }
  function anexar(pai, filho) {
    if (filho == null || filho === false) return;
    if (Array.isArray(filho)) { filho.forEach((f) => anexar(pai, f)); return; }
    pai.appendChild(filho instanceof Node ? filho : document.createTextNode(String(filho)));
  }
  function icone(classe) { return h('i', { class: classe, attrs: { 'aria-hidden': 'true' } }); }
  function botaoIcone(classeIcone, dica, acao, extra) {
    return h('button', Object.assign({ class: 'ed-btn-icone', type: 'button', dica, on: { click: acao } }, extra || {}), icone(classeIcone));
  }
  function botao(texto, classeIcone, acao, classe, extra) {
    return h('button', Object.assign({ class: 'ed-btn ' + (classe || ''), type: 'button', on: { click: acao } }, extra || {}),
      classeIcone ? icone(classeIcone) : null, texto ? h('span', { text: texto }) : null);
  }

  /** Ícones desenhados (alinhar/distribuir não existem no Font Awesome gratuito). */
  function svgIcone(formas) {
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('viewBox', '0 0 16 16');
    svg.setAttribute('width', '16');
    svg.setAttribute('height', '16');
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('fill', 'currentColor');
    formas.forEach((f) => {
      const el = document.createElementNS(ns, 'rect');
      el.setAttribute('x', f[0]); el.setAttribute('y', f[1]); el.setAttribute('width', f[2]); el.setAttribute('height', f[3]);
      el.setAttribute('rx', f[4] == null ? 1 : f[4]);
      if (f[5]) el.setAttribute('opacity', f[5]);
      svg.appendChild(el);
    });
    return svg;
  }
  const ICONES_ALINHAR = {
    esquerda: [[1, 1, 1.5, 14, 0], [4, 3, 9, 3.5], [4, 9.5, 6, 3.5]],
    centro: [[7.25, 1, 1.5, 14, 0, 0.55], [2.5, 3, 11, 3.5], [4.5, 9.5, 7, 3.5]],
    direita: [[13.5, 1, 1.5, 14, 0], [3, 3, 9, 3.5], [6, 9.5, 6, 3.5]],
    topo: [[1, 1, 14, 1.5, 0], [3, 4, 3.5, 9], [9.5, 4, 3.5, 6]],
    meio: [[1, 7.25, 14, 1.5, 0, 0.55], [3, 2.5, 3.5, 11], [9.5, 4.5, 3.5, 7]],
    base: [[1, 13.5, 14, 1.5, 0], [3, 3, 3.5, 9], [9.5, 6, 3.5, 6]],
    distH: [[1, 1, 1.2, 14, 0], [13.8, 1, 1.2, 14, 0], [5.5, 4, 5, 8]],
    distV: [[1, 1, 14, 1.2, 0], [1, 13.8, 14, 1.2, 0], [4, 5.5, 8, 5]],
  };

  // ---------------------------------------------------------------- cores

  /** Cor do documento → {r,g,b,a} (tema:* resolvido). */
  function parseCor(valor) {
    const c = R.resolverCor(valor, E.doc && E.doc.tema);
    if (!c || c === 'transparent') return c === 'transparent' ? { r: 0, g: 0, b: 0, a: 0 } : null;
    if (c[0] === '#') {
      let hex = c.slice(1);
      if (hex.length === 3 || hex.length === 4) hex = hex.split('').map((x) => x + x).join('');
      const a = hex.length === 8 ? parseInt(hex.slice(6, 8), 16) / 255 : 1;
      return { r: parseInt(hex.slice(0, 2), 16), g: parseInt(hex.slice(2, 4), 16), b: parseInt(hex.slice(4, 6), 16), a };
    }
    const m = c.match(/rgba?\(([^)]+)\)/i);
    if (!m) return null;
    const p = m[1].split(',').map((x) => parseFloat(x));
    return { r: p[0] | 0, g: p[1] | 0, b: p[2] | 0, a: p.length > 3 ? limitar(p[3], 0, 1) : 1 };
  }
  function hex2(n) { return limitar(Math.round(n), 0, 255).toString(16).padStart(2, '0').toUpperCase(); }
  /** Formato aceito pelo saneador do servidor: #RRGGBB ou #RRGGBBAA. */
  function corHex(rgba) {
    if (!rgba) return '';
    const base = '#' + hex2(rgba.r) + hex2(rgba.g) + hex2(rgba.b);
    return rgba.a >= 0.999 ? base : base + hex2(rgba.a * 255);
  }
  function corParaCss(valor) { return R.resolverCor(valor, E.doc && E.doc.tema); }

  const ROTULOS_TEMA = {
    fundo: 'Fundo', superficie: 'Superfície', primaria: 'Primária', secundaria: 'Secundária',
    destaque: 'Destaque', texto: 'Texto', texto_suave: 'Texto suave',
  };

  // =================================================================== A. API

  class ErroApi extends Error {
    constructor(mensagem, status, dados) { super(mensagem); this.status = status; this.dados = dados; }
  }

  function mensagemStatus(status) {
    if (status === 403) return 'Sem permissão para esta ação.';
    if (status === 404) return 'Não encontrado.';
    if (status === 413) return 'Arquivo grande demais para o servidor.';
    if (status === 429) return 'Muitas tarefas ao mesmo tempo. Espere um pouco e tente de novo.';
    if (status >= 500) return 'O servidor teve um problema. Tente de novo em instantes.';
    return 'Não foi possível concluir (erro ' + status + ').';
  }

  async function api(url, opcoes) {
    const o = opcoes || {};
    const init = { method: o.metodo || 'GET', credentials: 'same-origin', headers: { Accept: 'application/json' } };
    if (o.json !== undefined) {
      init.method = o.metodo || 'POST';
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(o.json);
    }
    if (init.method !== 'GET') init.headers['X-CSRFToken'] = APRES.csrf || '';
    let resp;
    try {
      resp = await fetch(url, init);
    } catch (e) {
      throw new ErroApi('Sem conexão com o servidor. Confira a internet e tente de novo.', 0, null);
    }
    let dados = null;
    try { dados = await resp.json(); } catch (e) { dados = null; }
    if (!dados && resp.redirected && /login/i.test(resp.url)) {
      throw new ErroApi('Sua sessão expirou. Entre de novo em outra aba e tente outra vez.', 401, null);
    }
    if (!resp.ok || !dados || dados.ok === false) {
      throw new ErroApi((dados && dados.erro) || mensagemStatus(resp.status), resp.status, dados);
    }
    return dados;
  }

  /** Envio multipart com progresso (fetch não informa progresso de upload). */
  function enviarFormulario(url, form, aoProgresso) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open('POST', url);
      xhr.setRequestHeader('X-CSRFToken', APRES.csrf || '');
      xhr.setRequestHeader('Accept', 'application/json');
      xhr.upload.onprogress = (ev) => { if (ev.lengthComputable && aoProgresso) aoProgresso(ev.loaded / ev.total); };
      xhr.onload = () => {
        let dados = null;
        try { dados = JSON.parse(xhr.responseText); } catch (e) { dados = null; }
        if (xhr.status >= 200 && xhr.status < 300 && dados && dados.ok !== false) resolve(dados);
        else reject(new ErroApi((dados && dados.erro) || mensagemStatus(xhr.status), xhr.status, dados));
      };
      xhr.onerror = () => reject(new ErroApi('Sem conexão com o servidor.', 0, null));
      xhr.send(form);
    });
  }

  // =================================================================== A. estado

  const E = {
    doc: null,
    titulo: APRES.titulo || '',
    revisao: 0,
    slideId: null,
    selecao: [],
    zoom: null,
    escala: 0.4,
    historico: [],
    hIndice: -1,
    hChave: null,
    hTempo: 0,
    versaoLocal: 0,
    versaoSalva: 0,
    salvando: false,
    salvarDeNovo: false,
    erroSalvar: null,
    ultimoSalvo: null,
    conflito: false,
    editandoTexto: null,
    editandoCelula: null,
    interacao: null,
    pairar: null,
    guias: [],
    laco: null,
    medida: null,
    previa: false,
    painelIA: false,
    tarefaIA: null,
    tamanhoBase: {},
    movel: false,
    apresentando: false,
    colagens: 0,
  };

  function slideAtual() {
    if (!E.doc) return null;
    return E.doc.slides.find((s) => s.id === E.slideId) || E.doc.slides[0] || null;
  }
  function indiceDoSlide(id) { return E.doc.slides.findIndex((s) => s.id === id); }
  function elementoPorId(id, slide) {
    const s = slide || slideAtual();
    return s ? (s.elementos || []).find((el) => el.id === id) || null : null;
  }
  function selecionados() { return E.selecao.map((id) => elementoPorId(id)).filter(Boolean); }
  function nodoDoElemento(id) {
    return UI.escala ? UI.escala.querySelector('.apres-el[data-id="' + R.cssEscape(id) + '"]') : null;
  }

  // ---------------------------------------------------------------- padrões dos elementos

  const BORDA_VAZIA = () => ({ cor: '', largura: 0, estilo: 'solid' });
  const ANIM_VAZIA = () => ({ tipo: 'nenhuma', duracao: 600, atraso: 0, gatilho: 'auto' });

  function padraoEstiloTexto() {
    return {
      fonte: '', papel: 'texto', tamanho: 36, peso: 400, italico: false, sublinhado: false, tachado: false,
      maiusculas: false, cor: 'tema:texto', alinhamento: 'left', vertical: 'top', entrelinha: 1.2, espacamento: 0,
      fundo: '', borda: BORDA_VAZIA(), raio: 0, preenchimento: 0, sombra: '',
    };
  }

  /** Completa campos ausentes (documentos antigos ou vindos da IA) sem mudar o que já existe. */
  function normalizarDocumento(doc) {
    const d = doc && typeof doc === 'object' ? doc : {};
    d.formato = d.formato || 1;
    d.largura = LARG;
    d.altura = ALT;
    d.tema = d.tema || {};
    d.tema.fonte_titulo = d.tema.fonte_titulo || 'Montserrat';
    d.tema.fonte_texto = d.tema.fonte_texto || 'Montserrat';
    d.tema.cores = Object.assign({
      fundo: '#0B0612', superficie: '#1A0F24', primaria: '#E23FCF', secundaria: '#8B3DFF',
      destaque: '#FFD15C', texto: '#FFFFFF', texto_suave: '#D9C9E8',
    }, d.tema.cores || {});
    d.slides = Array.isArray(d.slides) ? d.slides.filter((s) => s && typeof s === 'object') : [];
    d.slides.forEach(normalizarSlide);
    return d;
  }
  function normalizarSlide(s) {
    s.id = s.id || novoId('s');
    s.nome = s.nome || '';
    s.layout = s.layout || 'livre';
    s.fundo = Object.assign({ cor: 'tema:fundo', gradiente: '', imagem: '', ajuste: 'cover' }, s.fundo || {});
    s.transicao = Object.assign({ tipo: 'fade', duracao: 700 }, s.transicao || {});
    s.notas = s.notas || '';
    s.narracao = s.narracao || null;
    s.oculto = !!s.oculto;
    s.elementos = Array.isArray(s.elementos) ? s.elementos.filter((el) => el && typeof el === 'object') : [];
    s.elementos.forEach(normalizarElemento);
    return s;
  }
  function normalizarElemento(el) {
    el.id = el.id || novoId('e');
    ['x', 'y'].forEach((k) => { el[k] = num(el[k], 0); });
    el.w = Math.max(1, num(el.w, 100));
    el.h = Math.max(1, num(el.h, 100));
    el.rotacao = num(el.rotacao, 0);
    el.opacidade = el.opacidade == null ? 1 : num(el.opacidade, 1);
    el.bloqueado = !!el.bloqueado;
    el.slot = el.slot || '';
    el.animacao = Object.assign(ANIM_VAZIA(), el.animacao || {});
    el.link = el.link || '';
    if (el.tipo === 'texto') {
      el.html = el.html || '';
      el.estilo = Object.assign(padraoEstiloTexto(), el.estilo || {});
      el.estilo.borda = Object.assign(BORDA_VAZIA(), el.estilo.borda || {});
      el.autoajuste = !!el.autoajuste;
    } else if (el.tipo === 'imagem') {
      Object.keys({ src: '', ajuste: 'cover', raio: 0, sombra: '', filtro: '', alt: '' }).forEach((k) => {
        if (el[k] == null) el[k] = { src: '', ajuste: 'cover', raio: 0, sombra: '', filtro: '', alt: '' }[k];
      });
      el.foco = Object.assign({ x: 0.5, y: 0.5 }, el.foco || {});
      el.borda = Object.assign(BORDA_VAZIA(), el.borda || {});
    } else if (el.tipo === 'forma') {
      el.forma = el.forma || 'retangulo';
      el.preenchimento = el.preenchimento == null ? '' : el.preenchimento;
      el.gradiente = el.gradiente || '';
      el.borda = Object.assign(BORDA_VAZIA(), el.borda || {});
      el.raio = num(el.raio, 0);
      el.sombra = el.sombra || '';
    } else if (el.tipo === 'icone') {
      el.icone = el.icone || 'fa-solid fa-star';
      el.cor = el.cor || 'tema:destaque';
      el.fundo = el.fundo || '';
      el.raio = num(el.raio, 0);
    } else if (el.tipo === 'video') {
      el.src = el.src || '';
      el.poster = el.poster || '';
      ['autoplay', 'mudo'].forEach((k) => { if (el[k] == null) el[k] = true; });
      ['loop', 'controles'].forEach((k) => { el[k] = !!el[k]; });
      el.raio = num(el.raio, 0);
      el.ajuste = el.ajuste || 'cover';
    } else if (el.tipo === 'tabela') {
      el.linhas = Array.isArray(el.linhas) && el.linhas.length ? el.linhas.map((l) => (Array.isArray(l) ? l.map((c) => (c == null ? '' : String(c))) : [''])) : [['']];
      const cols = Math.max.apply(null, el.linhas.map((l) => l.length));
      el.linhas.forEach((l) => { while (l.length < cols) l.push(''); });
      el.cabecalho = el.cabecalho == null ? true : !!el.cabecalho;
      el.estilo = Object.assign({
        fonte: '', tamanho: 28, cor: 'tema:texto', fundo_cabecalho: 'tema:primaria', cor_cabecalho: '#FFFFFF',
        fundo_linhas: '', fundo_alternado: '', borda: '', alinhamento: 'left', raio: 0,
      }, el.estilo || {});
    }
    return el;
  }

  /** Arredonda números como o servidor (2 casas): evita "diferenças" falsas depois de salvar. */
  function arredondarElemento(el) {
    ['x', 'y', 'w', 'h', 'rotacao'].forEach((k) => { el[k] = arred(el[k], 2); });
    el.opacidade = arred(limitar(el.opacidade, 0, 1), 2);
    if (el.estilo && el.estilo.tamanho != null) el.estilo.tamanho = arred(el.estilo.tamanho, 2);
  }

  // =================================================================== A. histórico

  function iniciarHistorico() {
    E.historico = [{ doc: JSON.stringify(E.doc), slideId: E.slideId, rotulo: 'Início' }];
    E.hIndice = 0;
    E.hChave = null;
    atualizarBotoesHistorico();
  }

  /**
   * Registra o estado atual. `chave` agrupa alterações seguidas do mesmo controle
   * (digitar um número, setas, arrastar um slider) num passo só de desfazer.
   */
  function commit(rotulo, chave, opcoes) {
    if (!E.doc) return false;
    (E.doc.slides || []).forEach((s) => s.elementos.forEach(arredondarElemento));
    const estado = JSON.stringify(E.doc);
    const atual = E.historico[E.hIndice];
    if (atual && atual.doc === estado) return false;
    const agora = Date.now();
    const entrada = { doc: estado, slideId: E.slideId, rotulo: rotulo || '' };
    E.historico.length = E.hIndice + 1;
    if (chave && E.hChave === chave && agora - E.hTempo < 1500 && E.hIndice > 0) {
      E.historico[E.hIndice] = entrada;
    } else {
      E.historico.push(entrada);
      E.hIndice += 1;
      while (E.historico.length > MAX_HISTORICO) { E.historico.shift(); E.hIndice -= 1; }
    }
    E.hChave = chave || null;
    E.hTempo = agora;
    if (!(opcoes && opcoes.semSalvar)) marcarAlterado();
    atualizarBotoesHistorico();
    return true;
  }

  function desfazer() {
    finalizarEdicoes({ semCommit: false });
    if (E.hIndice <= 0) return;
    const desfeita = E.historico[E.hIndice];
    E.hIndice -= 1;
    restaurarEstado(E.historico[E.hIndice], desfeita.slideId);
    avisar('Desfeito' + (desfeita.rotulo ? ': ' + desfeita.rotulo : ''), { duracao: 1600 });
  }
  function refazer() {
    finalizarEdicoes();
    if (E.hIndice >= E.historico.length - 1) return;
    E.hIndice += 1;
    const entrada = E.historico[E.hIndice];
    restaurarEstado(entrada, entrada.slideId);
    avisar('Refeito' + (entrada.rotulo ? ': ' + entrada.rotulo : ''), { duracao: 1600 });
  }
  function restaurarEstado(entrada, slidePreferido) {
    E.doc = JSON.parse(entrada.doc);
    const ids = E.doc.slides.map((s) => s.id);
    if (slidePreferido && ids.indexOf(slidePreferido) >= 0) E.slideId = slidePreferido;
    else if (ids.indexOf(E.slideId) < 0) E.slideId = ids[0] || null;
    E.selecao = E.selecao.filter((id) => elementoPorId(id));
    E.hChave = null;
    marcarAlterado();
    atualizarBotoesHistorico();
    renderizarTudo();
  }
  function atualizarBotoesHistorico() {
    if (UI.desfazer) UI.desfazer.disabled = E.hIndice <= 0;
    if (UI.refazer) UI.refazer.disabled = E.hIndice >= E.historico.length - 1;
  }

  // =================================================================== A. salvamento

  function marcarAlterado() {
    E.versaoLocal += 1;
    atualizarStatus();
    agendarSalvar();
  }
  const agendarSalvar = debounce(() => { salvar(); }, 1500);

  function canonico(v) {
    if (Array.isArray(v)) return v.map(canonico);
    if (v && typeof v === 'object') {
      const o = {};
      Object.keys(v).sort().forEach((k) => { o[k] = canonico(v[k]); });
      return o;
    }
    if (typeof v === 'number') return Math.round(v * 100) / 100;
    return v;
  }

  function salvar(opcoes) {
    const o = opcoes || {};
    if (E.conflito || !E.doc || !URLS.documento) return Promise.resolve(false);
    agendarSalvar.cancelar();
    if (E.salvando) { E.salvarDeNovo = true; return E.promessaSalvar; }
    if (E.versaoLocal === E.versaoSalva && !o.forcar) { atualizarStatus(); return Promise.resolve(true); }
    E.salvando = true;
    atualizarStatus();
    const versao = E.versaoLocal;
    const enviado = JSON.stringify(E.doc);
    E.promessaSalvar = api(URLS.documento, { json: { documento: JSON.parse(enviado), revisao: E.revisao, titulo: E.titulo } })
      .then((r) => {
        E.revisao = r.revisao;
        E.versaoSalva = Math.max(E.versaoSalva, versao);
        E.erroSalvar = null;
        E.ultimoSalvo = new Date();
        if (r.documento && versao === E.versaoLocal) adotarDocumentoDoServidor(r.documento, enviado);
        return true;
      })
      .catch((erro) => {
        if (erro.status === 409) {
          E.conflito = true;
          mostrarConflito(erro);
        } else {
          E.erroSalvar = erro;
          // Nova tentativa sozinha: queda de rede costuma ser passageira.
          clearTimeout(E.tentarSalvarTimer);
          E.tentarSalvarTimer = setTimeout(() => { if (E.erroSalvar) salvar(); }, 15000);
        }
        return false;
      })
      .finally(() => {
        E.salvando = false;
        atualizarStatus();
        if (E.salvarDeNovo) { E.salvarDeNovo = false; salvar(); }
      });
    return E.promessaSalvar;
  }

  /** O servidor devolve o documento saneado; se ele mudou algo e ninguém mexeu desde o envio, ficamos com a versão dele. */
  function adotarDocumentoDoServidor(docServidor, enviado) {
    if (E.interacao || E.editandoTexto || E.editandoCelula || E.previa) return;
    if (JSON.stringify(canonico(docServidor)) === JSON.stringify(canonico(JSON.parse(enviado)))) return;
    E.doc = normalizarDocumento(docServidor);
    if (!E.doc.slides.some((s) => s.id === E.slideId)) E.slideId = (E.doc.slides[0] || {}).id || null;
    E.selecao = E.selecao.filter((id) => elementoPorId(id));
    E.historico[E.hIndice] = { doc: JSON.stringify(E.doc), slideId: E.slideId, rotulo: E.historico[E.hIndice].rotulo };
    renderizarTudo();
  }

  function haAlteracoesNaoSalvas() {
    return !!E.doc && (E.versaoLocal !== E.versaoSalva || E.salvando || !!E.editandoTexto || !!E.editandoCelula);
  }

  function atualizarStatus() {
    const s = UI.status;
    if (!s) return;
    s.className = 'ed-status';
    s.textContent = '';
    s.onclick = null;
    s.removeAttribute('data-dica');
    if (E.conflito) {
      s.classList.add('erro');
      anexar(s, [icone('fa-solid fa-triangle-exclamation'), 'Alterada em outra aba']);
      s.onclick = () => mostrarConflito();
    } else if (E.salvando) {
      s.classList.add('salvando');
      anexar(s, [h('span', { class: 'ed-giro ed-giro-p', attrs: { 'aria-hidden': 'true' } }), 'Salvando…']);
    } else if (E.erroSalvar) {
      s.classList.add('erro');
      anexar(s, [icone('fa-solid fa-circle-exclamation'), 'Erro — tentar de novo']);
      s.setAttribute('data-dica', E.erroSalvar.message);
      s.onclick = () => salvar({ forcar: true });
    } else if (E.versaoLocal !== E.versaoSalva) {
      s.classList.add('pendente');
      anexar(s, [icone('fa-regular fa-circle'), 'Alterações não salvas']);
    } else if (E.ultimoSalvo) {
      anexar(s, [icone('fa-solid fa-check'), 'Salvo às ' + hora(E.ultimoSalvo)]);
    } else {
      anexar(s, [icone('fa-solid fa-cloud'), 'Tudo salvo']);
    }
    s.setAttribute('aria-live', 'polite');
  }

  function mostrarConflito() {
    if (E.modalConflito) return;
    E.modalConflito = modal({
      titulo: 'Alterada em outra aba', icone: 'fa-solid fa-code-branch', fechavel: false,
      corpo: h('p', { text: 'Esta apresentação foi alterada em outra aba ou por outra pessoa enquanto você editava. '
        + 'Para não apagar o trabalho de ninguém, recarregue a versão mais recente.' }),
      acoes: [{
        texto: 'Recarregar', primario: true, icone: 'fa-solid fa-rotate',
        acao: async (fechar) => {
          try {
            await recarregarDocumento();
            E.conflito = false;
            E.modalConflito = null;
            fechar();
            avisar('Versão mais recente carregada.', { tipo: 'ok' });
          } catch (erro) {
            avisar(erro.message, { tipo: 'erro' });
          }
          return false;
        },
      }],
    });
  }

  async function recarregarDocumento() {
    const r = await api(URLS.documento);
    finalizarEdicoes({ descartar: true });
    E.doc = normalizarDocumento(r.documento);
    E.revisao = r.revisao;
    if (typeof r.titulo === 'string') { E.titulo = r.titulo; if (UI.titulo) UI.titulo.value = r.titulo; }
    if (!E.doc.slides.some((s) => s.id === E.slideId)) E.slideId = (E.doc.slides[0] || {}).id || null;
    E.selecao = [];
    E.versaoLocal = 0;
    E.versaoSalva = 0;
    E.erroSalvar = null;
    E.ultimoSalvo = new Date();
    await R.carregarFontes(E.doc, { limite: 6000 });
    iniciarHistorico();
    renderizarTudo();
    atualizarStatus();
  }

  // =================================================================== A. avisos, modais, popovers, dicas

  function avisar(texto, opcoes) {
    const o = opcoes || {};
    let caixa = $('.ed-toasts');
    if (!caixa) { caixa = h('div', { class: 'ed-toasts', attrs: { role: 'status', 'aria-live': 'polite' } }); document.body.appendChild(caixa); }
    const ic = o.tipo === 'erro' ? 'fa-solid fa-circle-exclamation' : o.tipo === 'ok' ? 'fa-solid fa-circle-check' : 'fa-solid fa-circle-info';
    const t = h('div', { class: 'ed-toast ' + (o.tipo || '') }, icone(ic), h('span', { text: texto }));
    if (o.acao) t.appendChild(h('button', { type: 'button', text: o.acao.texto, on: { click: () => { t.remove(); o.acao.fn(); } } }));
    caixa.appendChild(t);
    // Um aviso por vez de cada texto (salvar repetido não empilha).
    $$('.ed-toast', caixa).forEach((x) => { if (x !== t && x.textContent === t.textContent) x.remove(); });
    while (caixa.children.length > 3) caixa.firstElementChild.remove();
    setTimeout(() => t.remove(), o.duracao || (o.acao ? 7000 : o.tipo === 'erro' ? 6000 : 3200));
    return t;
  }

  const modaisAbertos = [];

  /** Modal acessível. acoes: [{texto, icone, primario, perigo, acao(fechar) → false mantém aberto}] */
  function modal(opcoes) {
    const o = opcoes || {};
    const anterior = document.activeElement;
    const idTitulo = novoId('m');
    const fundo = h('div', { class: 'ed-modal-fundo' });
    const caixa = h('div', { class: 'ed-modal', attrs: { role: 'dialog', 'aria-modal': 'true', 'aria-labelledby': idTitulo } });
    if (o.largura) caixa.style.width = 'min(' + o.largura + 'px, 100%)';
    const titulo = h('h2', { id: idTitulo }, o.icone ? icone(o.icone) : null, h('span', { text: o.titulo || '' }));
    caixa.appendChild(titulo);
    const corpo = h('div', { class: 'ed-modal-corpo', style: { display: 'flex', flexDirection: 'column', gap: '12px' } });
    if (typeof o.corpo === 'string') corpo.appendChild(h('p', { text: o.corpo }));
    else anexar(corpo, o.corpo);
    caixa.appendChild(corpo);
    const rodape = h('div', { class: 'ed-modal-acoes' });
    caixa.appendChild(rodape);
    fundo.appendChild(caixa);
    let fechado = false;
    function fechar() {
      if (fechado) return;
      fechado = true;
      fundo.remove();
      const i = modaisAbertos.indexOf(controle);
      if (i >= 0) modaisAbertos.splice(i, 1);
      document.removeEventListener('keydown', teclas, true);
      if (o.aoFechar) o.aoFechar();
      if (anterior && anterior.focus && document.contains(anterior)) anterior.focus({ preventScroll: true });
    }
    function montarAcoes(acoes) {
      rodape.textContent = '';
      (acoes || []).forEach((a) => {
        const b = botao(a.texto, a.icone, async () => {
          if (a.acao) {
            b.disabled = true;
            try {
              const r = await a.acao(fechar, b);
              if (r !== false) fechar();
            } finally { b.disabled = false; }
          } else fechar();
        }, (a.primario ? 'ed-btn-primario' : '') + (a.perigo ? ' ed-btn-perigo' : ''));
        if (a.href) {
          const link = h('a', { class: b.className, href: a.href, target: a.novaAba ? '_blank' : null, rel: a.novaAba ? 'noopener' : null },
            a.icone ? icone(a.icone) : null, h('span', { text: a.texto }));
          link.addEventListener('click', () => { if (a.fecharAoAbrir !== false) setTimeout(fechar, 50); });
          rodape.appendChild(link);
        } else rodape.appendChild(b);
      });
    }
    montarAcoes(o.acoes || [{ texto: 'Fechar' }]);
    function teclas(ev) {
      if (modaisAbertos[modaisAbertos.length - 1] !== controle) return;
      if (ev.key === 'Escape' && o.fechavel !== false) { ev.stopPropagation(); ev.preventDefault(); fechar(); }
      if (ev.key === 'Tab') {
        const focaveis = $$('button:not([disabled]), a[href], input, select, textarea, [tabindex="0"]', caixa);
        if (!focaveis.length) return;
        const primeiro = focaveis[0];
        const ultimo = focaveis[focaveis.length - 1];
        if (ev.shiftKey && document.activeElement === primeiro) { ev.preventDefault(); ultimo.focus(); }
        else if (!ev.shiftKey && document.activeElement === ultimo) { ev.preventDefault(); primeiro.focus(); }
      }
    }
    document.addEventListener('keydown', teclas, true);
    if (o.fechavel !== false) fundo.addEventListener('pointerdown', (ev) => { if (ev.target === fundo) fechar(); });
    document.body.appendChild(fundo);
    fecharPopover();
    const focar = $('[autofocus]', caixa) || $('.ed-btn-primario', rodape) || $('button, a[href]', rodape);
    if (focar) setTimeout(() => focar.focus({ preventScroll: true }), 30);
    const controle = { fechar, caixa, corpo, rodape, montarAcoes, titulo: titulo.lastChild, get fechado() { return fechado; } };
    modaisAbertos.push(controle);
    return controle;
  }

  function confirmar(titulo, texto, rotuloOk, perigo) {
    return new Promise((resolve) => {
      let ok = false;
      modal({
        titulo, icone: perigo ? 'fa-solid fa-triangle-exclamation' : 'fa-solid fa-circle-question', corpo: texto,
        aoFechar: () => resolve(ok),
        acoes: [{ texto: 'Cancelar' }, { texto: rotuloOk || 'Confirmar', primario: !perigo, perigo: !!perigo, acao: () => { ok = true; } }],
      });
    });
  }

  let popAtual = null;

  /** Popover ancorado. construir(pop, fechar) devolve o conteúdo. Um aberto por vez. */
  function abrirPopover(ancora, construir, opcoes) {
    const o = opcoes || {};
    fecharPopover();
    const pop = h('div', { class: 'ed-pop', attrs: { role: o.role || 'dialog', 'aria-label': o.rotulo || null } });
    const controle = { pop, ancora, aoFechar: o.aoFechar, fechar: () => fecharPopover(controle) };
    popAtual = controle;
    anexar(pop, construir(pop, controle.fechar));
    pop.style.visibility = 'hidden';
    document.body.appendChild(pop);
    posicionarPopover(controle, o);
    pop.style.visibility = '';
    if (ancora && ancora.setAttribute) ancora.setAttribute('aria-expanded', 'true');
    pop.addEventListener('keydown', (ev) => {
      if (ev.key === 'Escape') {
        ev.stopPropagation();
        ev.preventDefault();
        controle.fechar();
        if (ancora && ancora.focus) ancora.focus({ preventScroll: true });
      }
    });
    controle.reposicionar = () => posicionarPopover(controle, o);
    setTimeout(() => {
      if (popAtual !== controle) return;
      const focar = o.focar === false ? null : ($('[autofocus]', pop) || (o.focarPrimeiro ? $('button, input', pop) : null));
      if (focar) focar.focus({ preventScroll: true });
    }, 20);
    return controle;
  }
  function posicionarPopover(controle, o) {
    const pop = controle.pop;
    const r = o.ponto ? { left: o.ponto.x, right: o.ponto.x, top: o.ponto.y, bottom: o.ponto.y, width: 0, height: 0 }
      : controle.ancora.getBoundingClientRect();
    const pw = pop.offsetWidth;
    const ph = pop.offsetHeight;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    let top;
    let left;
    if (o.lado === 'esquerda') {
      left = r.left - pw - 8;
      top = r.top;
      if (left < 8) left = r.right + 8;
    } else {
      top = o.lado === 'cima' ? r.top - ph - 6 : r.bottom + 6;
      if (top + ph > vh - 8) top = r.top - ph - 6;
      left = o.alinhar === 'fim' ? r.right - pw : o.alinhar === 'centro' ? r.left + r.width / 2 - pw / 2 : r.left;
    }
    top = limitar(top, 8, Math.max(8, vh - ph - 8));
    left = limitar(left, 8, Math.max(8, vw - pw - 8));
    pop.style.top = Math.round(top) + 'px';
    pop.style.left = Math.round(left) + 'px';
  }
  function fecharPopover(controle) {
    if (!popAtual || (controle && controle !== popAtual)) return;
    const c = popAtual;
    popAtual = null;
    c.pop.remove();
    if (c.ancora && c.ancora.setAttribute) c.ancora.setAttribute('aria-expanded', 'false');
    if (c.aoFechar) c.aoFechar();
  }
  document.addEventListener('pointerdown', (ev) => {
    if (!popAtual) return;
    if (popAtual.pop.contains(ev.target)) return;
    if (popAtual.ancora && popAtual.ancora.contains && popAtual.ancora.contains(ev.target)) return;
    if (ev.target.closest && ev.target.closest('.ed-modal-fundo')) return;
    fecharPopover();
  }, true);
  window.addEventListener('resize', () => { if (popAtual && popAtual.reposicionar) popAtual.reposicionar(); });

  /** Menu com navegação por setas. itens: {icone, texto, sub, atalho, acao, desativado} | '-' | {titulo} */
  function abrirMenu(ancora, itens, opcoes) {
    const controle = abrirPopover(ancora, (pop, fechar) => {
      const frag = [];
      itens.forEach((item) => {
        if (!item) return;
        if (item === '-') { frag.push(h('div', { class: 'ed-menu-sep', attrs: { role: 'separator' } })); return; }
        if (item.titulo) { frag.push(h('div', { class: 'ed-menu-titulo', text: item.titulo })); return; }
        const conteudo = h('span', { style: { flex: '1', minWidth: '0' } }, item.texto, item.sub ? h('small', { text: item.sub }) : null);
        const attrs = { role: 'menuitem' };
        let b;
        if (item.href) {
          b = h('a', { class: 'ed-menu-item', href: item.href, target: item.novaAba ? '_blank' : null, rel: item.novaAba ? 'noopener' : null, attrs, on: { click: () => fechar() } },
            icone(item.icone || 'fa-solid fa-angle-right'), conteudo);
        } else {
          b = h('button', { class: 'ed-menu-item', type: 'button', disabled: !!item.desativado, attrs, on: { click: () => { fechar(); item.acao(); } } },
            icone(item.icone || 'fa-solid fa-angle-right'), conteudo, item.atalho ? h('span', { class: 'ed-atalho', text: item.atalho }) : null);
        }
        if (item.ativo) b.setAttribute('aria-checked', 'true');
        frag.push(b);
      });
      pop.setAttribute('role', 'menu');
      pop.addEventListener('keydown', (ev) => {
        const itensFoco = $$('.ed-menu-item:not([disabled])', pop);
        const i = itensFoco.indexOf(document.activeElement);
        if (ev.key === 'ArrowDown') { ev.preventDefault(); (itensFoco[i + 1] || itensFoco[0]).focus(); }
        if (ev.key === 'ArrowUp') { ev.preventDefault(); (itensFoco[i - 1] || itensFoco[itensFoco.length - 1]).focus(); }
      });
      return frag;
    }, Object.assign({ role: 'menu' }, opcoes || {}));
    setTimeout(() => { const p = $('.ed-menu-item:not([disabled])', controle.pop); if (p) p.focus({ preventScroll: true }); }, 20);
    return controle;
  }

  // Dicas (tooltips) em português: um elemento flutuante só, para qualquer [data-dica].
  (function dicas() {
    let alvo = null;
    let timer = null;
    let caixa = null;
    function esconder() {
      clearTimeout(timer);
      alvo = null;
      if (caixa) caixa.remove();
      caixa = null;
    }
    function mostrar(el) {
      if (!document.contains(el)) return;
      const texto = el.getAttribute('data-dica');
      if (!texto) return;
      caixa = h('div', { class: 'ed-dica-flutuante', text: texto, attrs: { role: 'tooltip' } });
      document.body.appendChild(caixa);
      const r = el.getBoundingClientRect();
      const w = caixa.offsetWidth;
      const hh = caixa.offsetHeight;
      let top = r.bottom + 8;
      if (top + hh > window.innerHeight - 6) top = r.top - hh - 8;
      caixa.style.top = top + 'px';
      caixa.style.left = limitar(r.left + r.width / 2 - w / 2, 6, window.innerWidth - w - 6) + 'px';
    }
    document.addEventListener('mouseover', (ev) => {
      const el = ev.target.closest && ev.target.closest('[data-dica]');
      if (el === alvo) return;
      esconder();
      if (!el || E.interacao) return;
      alvo = el;
      timer = setTimeout(() => mostrar(el), 450);
    });
    document.addEventListener('focusin', (ev) => {
      const el = ev.target.closest && ev.target.closest('[data-dica]');
      esconder();
      if (el && el.matches(':focus-visible')) { alvo = el; timer = setTimeout(() => mostrar(el), 300); }
    });
    ['pointerdown', 'scroll', 'keydown', 'focusout'].forEach((t) => document.addEventListener(t, esconder, true));
  })();

  const UI = {};

  // =================================================================== B. interface

  function montarInterface() {
    const app = $('#ed-app');
    app.textContent = '';
    app.removeAttribute('aria-busy');
    app.className = 'ed-app';
    document.body.classList.remove('ed-body-movel');

    // ------------------------------------------------ topo
    UI.titulo = h('input', {
      class: 'ed-titulo', type: 'text', value: E.titulo, maxLength: MODO_TEMPLATE ? 120 : 200,
      attrs: { 'aria-label': MODO_TEMPLATE ? 'Nome do template' : 'Título da apresentação', spellcheck: 'false', autocomplete: 'off' },
      on: {
        input: () => { E.titulo = UI.titulo.value; document.title = (E.titulo || 'Sem título') + ' · Editor de apresentações'; marcarAlterado(); },
        keydown: (ev) => { if (ev.key === 'Enter' || ev.key === 'Escape') { ev.preventDefault(); UI.titulo.blur(); } },
        blur: () => { if (!UI.titulo.value.trim()) { UI.titulo.value = E.titulo = MODO_TEMPLATE ? 'Template' : 'Sem título'; marcarAlterado(); } },
      },
    });
    UI.status = h('button', { class: 'ed-status', type: 'button' });
    UI.desfazer = botaoIcone('fa-solid fa-rotate-left', 'Desfazer (Ctrl+Z)', desfazer);
    UI.refazer = botaoIcone('fa-solid fa-rotate-right', 'Refazer (Ctrl+Shift+Z)', refazer);
    UI.zoomValor = h('button', { class: 'ed-zoom-valor', type: 'button', dica: 'Zoom', attrs: { 'aria-haspopup': 'menu' }, on: { click: (ev) => abrirMenuZoom(ev.currentTarget) } });

    const esquerda = h('div', { class: 'ed-topo-esq' },
      h('a', { class: 'ed-btn-icone', href: URLS.voltar || '/', dica: MODO_TEMPLATE ? 'Voltar aos templates' : 'Voltar às apresentações' }, icone('fa-solid fa-arrow-left')),
      h('span', { class: 'ed-marca', attrs: { 'aria-hidden': 'true' } }, icone(MODO_TEMPLATE ? 'fa-solid fa-swatchbook' : 'fa-solid fa-person-chalkboard')),
      UI.titulo,
      APRES.dono ? h('span', { class: 'ed-chip', text: 'de ' + APRES.dono, dica: 'Você está editando a apresentação de outra pessoa' }) : null,
      UI.status);
    const meio = h('div', { class: 'ed-topo-meio' },
      UI.desfazer, UI.refazer, h('span', { class: 'ed-divisor' }),
      h('div', { class: 'ed-zoom' },
        botaoIcone('fa-solid fa-magnifying-glass-minus', 'Diminuir zoom (Ctrl −)', () => mudarZoom(-1)),
        UI.zoomValor,
        botaoIcone('fa-solid fa-magnifying-glass-plus', 'Aumentar zoom (Ctrl +)', () => mudarZoom(1))));
    const direita = h('div', { class: 'ed-topo-dir' });
    direita.appendChild(botaoIcone('fa-regular fa-keyboard', 'Atalhos de teclado', mostrarAtalhos));
    if (URLS.versoes && !MODO_TEMPLATE) {
      direita.appendChild(botao('Versões', 'fa-solid fa-clock-rotate-left', (ev) => abrirVersoes(ev.currentTarget), 'ed-btn-fantasma', { dica: 'Versões anteriores', attrs: { 'aria-haspopup': 'dialog' } }));
    }
    if (iaDisponivel()) {
      UI.botaoIA = botao('Assistente IA', 'fa-solid fa-wand-magic-sparkles', () => alternarPainelIA(), 'ed-btn-ia', { attrs: { 'aria-pressed': 'false' } });
      direita.appendChild(UI.botaoIA);
    }
    if (MODO_TEMPLATE) {
      direita.appendChild(botao('Visualizar', 'fa-solid fa-play', () => apresentar({ template: true }), 'ed-btn-fantasma', { dica: 'Ver os layouts em tela cheia' }));
    } else {
      direita.appendChild(botao('Apresentar', 'fa-solid fa-play', () => apresentar(), 'ed-btn-primario', { dica: 'Apresentar a partir deste slide (Ctrl+Enter)' }));
      UI.botaoExportar = botao('Exportar', 'fa-solid fa-download', (ev) => abrirMenuExportar(ev.currentTarget), '', { attrs: { 'aria-haspopup': 'menu' } });
      UI.botaoExportar.appendChild(icone('fa-solid fa-chevron-down'));
      direita.appendChild(UI.botaoExportar);
    }
    const topo = h('header', { class: 'ed-topo' }, esquerda, meio, direita);

    // ------------------------------------------------ esquerda: slides
    UI.contador = h('span', { class: 'ed-contador', text: '0' });
    UI.listaSlides = h('ol', { class: 'ed-slides-lista', attrs: { 'aria-label': MODO_TEMPLATE ? 'Layouts do template' : 'Slides da apresentação' } });
    const painelSlides = h('nav', { class: 'ed-slides', attrs: { 'aria-label': MODO_TEMPLATE ? 'Layouts' : 'Slides' } },
      h('div', { class: 'ed-slides-cabeca' },
        h('h2', { text: MODO_TEMPLATE ? 'Layouts' : 'Slides' }), UI.contador, h('span', { class: 'ed-espaco' }),
        botaoIcone('fa-solid fa-plus', MODO_TEMPLATE ? 'Novo layout' : 'Novo slide', () => novoSlide(E.slideId), { class: 'ed-btn-icone p' })),
      UI.listaSlides,
      h('div', { class: 'ed-slides-rodape' },
        botao(MODO_TEMPLATE ? 'Novo layout' : 'Novo slide', 'fa-solid fa-plus', () => novoSlide(E.slideId), 'ed-btn-bloco')));

    // ------------------------------------------------ centro: inserir + palco
    UI.inserir = h('div', { class: 'ed-inserir', attrs: { role: 'toolbar', 'aria-label': 'Inserir' } });
    montarBarraInserir(UI.inserir);
    UI.escala = h('div', { class: 'ed-quadro-escala' });
    UI.sobre = h('div', { class: 'ed-sobreposicao' });
    UI.caixa = h('div', { class: 'ed-quadro-caixa', attrs: { role: 'region', 'aria-label': 'Slide em edição' } }, UI.escala, UI.sobre);
    UI.palcoInterno = h('div', { class: 'ed-palco-interno' }, UI.caixa);
    UI.palco = h('div', { class: 'ed-palco', attrs: { tabindex: '-1' } }, UI.palcoInterno);
    const centro = h('main', { class: 'ed-centro' }, UI.inserir, UI.palco);

    // ------------------------------------------------ direita: propriedades / IA
    UI.props = h('aside', { class: 'ed-props', attrs: { 'aria-label': 'Propriedades' } });

    app.append(topo, h('div', { class: 'ed-corpo' }, painelSlides, centro, UI.props));
    ligarEventosDoPalco();
    ligarListaSlides();
    new ResizeObserver(() => { if (E.zoom == null) aplicarZoom(); atualizarEscalaMiniaturas(); }).observe(UI.palco);
    atualizarBotoesHistorico();
    atualizarStatus();
  }

  function iaDisponivel() {
    return !MODO_TEMPLATE && !!APRES.pode_ia && !!URLS.ia;
  }

  function renderizarTudo() {
    if (!E.doc || E.movel) { if (E.movel && E.doc) renderizarMovel(); return; }
    if (!E.doc.slides.some((s) => s.id === E.slideId)) E.slideId = (E.doc.slides[0] || {}).id || null;
    renderizarListaSlides();
    renderizarQuadro();
    renderizarPainel();
    atualizarBotoesHistorico();
    atualizarStatus();
  }

  // =================================================================== B. quadro

  let quadroAgendado = false;
  function agendarQuadro() {
    if (quadroAgendado) return;
    quadroAgendado = true;
    requestAnimationFrame(() => { quadroAgendado = false; renderizarQuadro(); });
  }

  function contextoRender(extra) {
    return Object.assign({ modo: MODO_TEMPLATE ? 'template' : 'editor' }, extra || {});
  }

  function renderizarQuadro() {
    if (!UI.escala || !E.doc) return;
    if (E.editandoTexto || E.editandoCelula) return;
    const slide = slideAtual();
    UI.escala.textContent = '';
    if (!slide) {
      UI.escala.appendChild(h('div', { class: 'apres-slide', style: { display: 'flex', alignItems: 'center', justifyContent: 'center' } },
        h('button', { class: 'ed-btn ed-btn-g ed-btn-primario', type: 'button', style: { transform: 'scale(2.4)' }, on: { click: () => novoSlide(null) } },
          icone('fa-solid fa-plus'), h('span', { text: MODO_TEMPLATE ? 'Criar o primeiro layout' : 'Criar o primeiro slide' }))));
      aplicarZoom();
      return;
    }
    const nodo = R.criarSlide(slide, E.doc, contextoRender());
    (slide.elementos || []).forEach((el) => {
      if (!el.bloqueado) return;
      const n = nodo.querySelector('.apres-el[data-id="' + R.cssEscape(el.id) + '"]');
      if (n) n.classList.add('ed-bloqueado');
    });
    UI.escala.appendChild(nodo);
    aplicarZoom();
  }

  /** Recria só o nó de um elemento (formas e ícones dependem do tamanho, não só da caixa). */
  function redesenharElemento(el) {
    const antigo = nodoDoElemento(el.id);
    if (!antigo) return;
    if (el.tipo === 'texto' || el.tipo === 'imagem' || el.tipo === 'video' || el.tipo === 'tabela' || el.tipo === 'apagar') {
      R.aplicarCaixa(antigo, el);
      return;
    }
    const novo = R.criarElemento(el, E.doc, contextoRender());
    if (!novo) return;
    if (el.bloqueado) novo.classList.add('ed-bloqueado');
    antigo.replaceWith(novo);
  }

  // =================================================================== B. zoom

  const PASSOS_ZOOM = [0.1, 0.15, 0.25, 0.33, 0.5, 0.67, 0.75, 1, 1.25, 1.5, 2, 3, 4];

  function escalaAjustada() {
    if (!UI.palco) return 0.4;
    const larg = UI.palco.clientWidth - 72;
    const alt = UI.palco.clientHeight - 72;
    return Math.max(0.05, Math.min(larg / LARG, alt / ALT));
  }

  function aplicarZoom(ancora) {
    if (!UI.caixa) return;
    const antiga = E.escala;
    const retAntes = ancora ? UI.caixa.getBoundingClientRect() : null;
    E.escala = E.zoom == null ? escalaAjustada() : E.zoom;
    UI.caixa.style.width = LARG * E.escala + 'px';
    UI.caixa.style.height = ALT * E.escala + 'px';
    UI.escala.style.transform = 'scale(' + E.escala + ')';
    if (UI.zoomValor) {
      UI.zoomValor.textContent = Math.round(E.escala * 100) + '%';
      UI.zoomValor.setAttribute('data-dica', E.zoom == null ? 'Zoom: ajustado à tela' : 'Zoom');
    }
    if (ancora && retAntes && antiga) {
      // Mantém o ponto sob o cursor parado ao aproximar com Ctrl + rolagem.
      const px = (ancora.x - retAntes.left) / antiga;
      const py = (ancora.y - retAntes.top) / antiga;
      const retDepois = UI.caixa.getBoundingClientRect();
      UI.palco.scrollLeft += retDepois.left + px * E.escala - ancora.x;
      UI.palco.scrollTop += retDepois.top + py * E.escala - ancora.y;
    }
    desenharSobreposicao();
  }

  function mudarZoom(direcao, ancora) {
    const atual = E.escala;
    let proximo;
    if (direcao > 0) proximo = PASSOS_ZOOM.find((p) => p > atual + 0.001) || PASSOS_ZOOM[PASSOS_ZOOM.length - 1];
    else proximo = PASSOS_ZOOM.slice().reverse().find((p) => p < atual - 0.001) || PASSOS_ZOOM[0];
    E.zoom = proximo;
    aplicarZoom(ancora);
  }

  function abrirMenuZoom(ancora) {
    const itens = [
      { icone: 'fa-solid fa-expand', texto: 'Ajustar à tela', atalho: 'Ctrl 0', acao: () => { E.zoom = null; aplicarZoom(); } },
      '-',
    ];
    [0.25, 0.5, 0.75, 1, 1.5, 2].forEach((z) => itens.push({
      icone: Math.abs(E.escala - z) < 0.005 ? 'fa-solid fa-check' : 'fa-solid fa-magnifying-glass',
      texto: Math.round(z * 100) + '%', acao: () => { E.zoom = z; aplicarZoom(); },
    }));
    abrirMenu(ancora, itens, { alinhar: 'centro' });
  }

  // =================================================================== B. lista de slides (miniaturas)

  function atualizarEscalaMiniaturas() {
    if (!UI.listaSlides) return;
    const quadro = $('.ed-mini-quadro', UI.listaSlides);
    if (!quadro || !quadro.clientWidth) return;
    const escala = quadro.clientWidth / LARG;
    if (Math.abs(escala - (E.escalaMini || 0)) < 0.0005) return;
    E.escalaMini = escala;
    $$('.ed-mini-escala', UI.listaSlides).forEach((n) => { n.style.transform = 'scale(' + escala + ')'; });
  }

  const agendarMiniaturas = debounce(() => renderizarListaSlides(), 180);

  function renderizarListaSlides() {
    if (!UI.listaSlides || !E.doc) return;
    agendarMiniaturas.cancelar();
    const lista = UI.listaSlides;
    const existentes = new Map($$(':scope > .ed-mini', lista).map((li) => [li.getAttribute('data-id'), li]));
    const temaJson = JSON.stringify(E.doc.tema);
    E.doc.slides.forEach((s, i) => {
      let li = existentes.get(s.id);
      if (li) existentes.delete(s.id);
      else li = criarItemMiniatura(s);
      atualizarItemMiniatura(li, s, i, temaJson);
      if (lista.children[i] !== li) lista.insertBefore(li, lista.children[i] || null);
    });
    existentes.forEach((li) => li.remove());
    UI.contador.textContent = String(E.doc.slides.length);
    atualizarEscalaMiniaturas();
  }

  function criarItemMiniatura(s) {
    const li = h('li', { class: 'ed-mini', dataset: { id: s.id } });
    const botaoQuadro = h('button', {
      class: 'ed-mini-quadro', type: 'button', style: { padding: '0', border: '0', display: 'block', cursor: 'pointer' },
      on: {
        click: () => selecionarSlide(li.getAttribute('data-id')),
        keydown: (ev) => navegarMiniaturas(ev, li),
        contextmenu: (ev) => { ev.preventDefault(); menuDoSlide(li.getAttribute('data-id'), { x: ev.clientX, y: ev.clientY }); },
      },
    }, h('div', { class: 'ed-mini-escala' }));
    const corpo = h('div', { class: 'ed-mini-corpo' }, botaoQuadro, h('div', { class: 'ed-mini-papel' }));
    const acoes = h('div', { class: 'ed-mini-acoes' },
      botaoIcone('fa-regular fa-copy', 'Duplicar', () => duplicarSlide(li.getAttribute('data-id')), { class: 'ed-btn-icone p' }),
      botaoIcone('fa-regular fa-eye-slash', 'Ocultar', () => alternarOculto(li.getAttribute('data-id')), { class: 'ed-btn-icone p ed-mini-ocultar' }),
      botaoIcone('fa-regular fa-trash-can', 'Excluir', () => excluirSlide(li.getAttribute('data-id')), { class: 'ed-btn-icone p' }),
      botaoIcone('fa-solid fa-ellipsis', 'Mais ações', (ev) => menuDoSlide(li.getAttribute('data-id'), null, ev.currentTarget), { class: 'ed-btn-icone p' }));
    li.append(h('span', { class: 'ed-mini-num', attrs: { 'aria-hidden': 'true' } }), corpo, acoes);
    return li;
  }

  function atualizarItemMiniatura(li, s, i, temaJson) {
    const numero = R.numeracao(E.doc, s.id);
    const chave = JSON.stringify(s) + temaJson + numero.n + '/' + numero.total + (MODO_TEMPLATE ? 't' : '');
    const ativo = s.id === E.slideId;
    li.classList.toggle('ativo', ativo);
    li.classList.toggle('oculto', !!s.oculto);
    $('.ed-mini-num', li).textContent = String(i + 1);
    const botaoQuadro = $('.ed-mini-quadro', li);
    const rotuloPapel = MODO_TEMPLATE ? ' · ' + nomeDoPapel(s.layout) : '';
    botaoQuadro.setAttribute('aria-label', (MODO_TEMPLATE ? 'Layout ' : 'Slide ') + (i + 1) + rotuloPapel + (s.oculto ? ' (oculto)' : ''));
    if (ativo) botaoQuadro.setAttribute('aria-current', 'true'); else botaoQuadro.removeAttribute('aria-current');
    const ocultar = $('.ed-mini-ocultar', li);
    ocultar.setAttribute('data-dica', s.oculto ? 'Mostrar na apresentação' : 'Ocultar na apresentação');
    ocultar.setAttribute('aria-label', ocultar.getAttribute('data-dica'));
    $('i', ocultar).className = s.oculto ? 'fa-regular fa-eye' : 'fa-regular fa-eye-slash';
    const papel = $('.ed-mini-papel', li);
    papel.textContent = MODO_TEMPLATE ? nomeDoPapel(s.layout) + (s.nome ? ' · ' + s.nome : '') : '';
    papel.style.display = MODO_TEMPLATE ? '' : 'none';
    if (li._chave === chave) return;
    li._chave = chave;
    const escala = $('.ed-mini-escala', li);
    escala.textContent = '';
    escala.appendChild(R.criarSlide(s, E.doc, { modo: 'miniatura', template: MODO_TEMPLATE, indice: numero.n, total: numero.total }));
    if (E.escalaMini) escala.style.transform = 'scale(' + E.escalaMini + ')';
    $$('.ed-mini-selo, .ed-mini-selos', botaoQuadro).forEach((n) => n.remove());
    if (s.oculto) botaoQuadro.appendChild(h('span', { class: 'ed-mini-selo' }, icone('fa-solid fa-eye-slash'), 'Oculto'));
    const selos = h('span', { class: 'ed-mini-selos' });
    if (s.notas && s.notas.trim()) selos.appendChild(h('span', { attrs: { title: 'Tem notas do apresentador' } }, icone('fa-solid fa-note-sticky')));
    if (s.narracao) selos.appendChild(h('span', { attrs: { title: 'Tem narração' } }, icone('fa-solid fa-microphone')));
    if (s.transicao && s.transicao.tipo && s.transicao.tipo !== 'nenhuma') selos.appendChild(h('span', { attrs: { title: 'Transição: ' + nomeDaTransicao(s.transicao.tipo) } }, icone('fa-solid fa-shuffle')));
    if (selos.children.length) botaoQuadro.appendChild(selos);
  }

  function nomeDoPapel(v) { const p = R.PAPEIS.find((x) => x[0] === v); return p ? p[1] : 'Livre'; }
  function nomeDaTransicao(v) { const p = R.TRANSICOES.find((x) => x[0] === v); return p ? p[1] : v; }

  function navegarMiniaturas(ev, li) {
    const itens = $$(':scope > .ed-mini', UI.listaSlides);
    const i = itens.indexOf(li);
    let alvo = null;
    if (ev.key === 'ArrowDown' || ev.key === 'ArrowRight') alvo = itens[i + 1];
    else if (ev.key === 'ArrowUp' || ev.key === 'ArrowLeft') alvo = itens[i - 1];
    else if (ev.key === 'Home') alvo = itens[0];
    else if (ev.key === 'End') alvo = itens[itens.length - 1];
    else if (ev.key === 'Delete' || ev.key === 'Backspace') { ev.preventDefault(); excluirSlide(li.getAttribute('data-id')); return; }
    if (!alvo) return;
    ev.preventDefault();
    ev.stopPropagation();
    if (ev.altKey && (ev.key === 'ArrowDown' || ev.key === 'ArrowUp')) {
      moverSlide(i, itens.indexOf(alvo));
    } else {
      selecionarSlide(alvo.getAttribute('data-id'));
    }
    const novo = UI.listaSlides.querySelector('.ed-mini[data-id="' + R.cssEscape(E.slideId) + '"] .ed-mini-quadro');
    if (novo) novo.focus();
  }

  function ligarListaSlides() {
    if (typeof window.Sortable !== 'function') return;
    // Arrastar para reordenar; o DOM que o Sortable mexeu é refeito a partir do documento.
    window.Sortable.create(UI.listaSlides, {
      animation: 160, draggable: '.ed-mini', filter: '.ed-mini-acoes', preventOnFilter: false,
      ghostClass: 'sortable-ghost', chosenClass: 'sortable-chosen', delay: 60, delayOnTouchOnly: true,
      onEnd: (ev) => {
        if (ev.oldIndex === ev.newIndex) return;
        const [s] = E.doc.slides.splice(ev.oldIndex, 1);
        E.doc.slides.splice(ev.newIndex, 0, s);
        commit('Reordenar slides');
        $$(':scope > .ed-mini', UI.listaSlides).forEach((li) => { li._chave = null; });
        renderizarListaSlides();
        renderizarQuadro();
      },
    });
  }

  function rolarMiniaturaAtiva() {
    const li = UI.listaSlides && UI.listaSlides.querySelector('.ed-mini[data-id="' + R.cssEscape(E.slideId || '') + '"]');
    if (li) li.scrollIntoView({ block: 'nearest' });
  }

  // =================================================================== B. ações de slide

  function selecionarSlide(id) {
    if (!id || !E.doc.slides.some((s) => s.id === id)) return;
    finalizarEdicoes();
    if (E.slideId === id) return;
    E.slideId = id;
    E.selecao = [];
    E.pairar = null;
    $$(':scope > .ed-mini', UI.listaSlides).forEach((li) => {
      const ativo = li.getAttribute('data-id') === id;
      li.classList.toggle('ativo', ativo);
      const b = $('.ed-mini-quadro', li);
      if (ativo) b.setAttribute('aria-current', 'true'); else b.removeAttribute('aria-current');
    });
    renderizarQuadro();
    renderizarPainel();
    rolarMiniaturaAtiva();
  }

  function slideEmBranco(referencia) {
    return normalizarSlide({
      id: novoId('s'), nome: '', layout: MODO_TEMPLATE ? 'conteudo' : 'livre',
      fundo: referencia ? clonar(referencia.fundo) : { cor: 'tema:fundo', gradiente: '', imagem: '', ajuste: 'cover' },
      transicao: referencia ? clonar(referencia.transicao) : { tipo: 'fade', duracao: 700 },
      notas: '', narracao: null, oculto: false, elementos: [],
    });
  }

  function novoSlide(depoisDe) {
    finalizarEdicoes();
    const i = depoisDe ? indiceDoSlide(depoisDe) : E.doc.slides.length - 1;
    const ref = E.doc.slides[i] || null;
    const s = slideEmBranco(null);
    if (ref) s.transicao = clonar(ref.transicao);
    E.doc.slides.splice(i + 1, 0, s);
    E.slideId = s.id;
    E.selecao = [];
    commit(MODO_TEMPLATE ? 'Novo layout' : 'Novo slide');
    renderizarTudo();
    rolarMiniaturaAtiva();
  }

  function trocarIds(slide) {
    slide.id = novoId('s');
    slide.elementos.forEach((el) => { el.id = novoId('e'); });
    return slide;
  }

  function duplicarSlide(id) {
    finalizarEdicoes();
    const i = indiceDoSlide(id || E.slideId);
    if (i < 0) return;
    const copia = trocarIds(clonar(E.doc.slides[i]));
    E.doc.slides.splice(i + 1, 0, copia);
    E.slideId = copia.id;
    E.selecao = [];
    commit('Duplicar slide');
    renderizarTudo();
    rolarMiniaturaAtiva();
  }

  function excluirSlide(id) {
    finalizarEdicoes();
    const i = indiceDoSlide(id);
    if (i < 0) return;
    E.doc.slides.splice(i, 1);
    if (E.slideId === id) {
      const vizinho = E.doc.slides[Math.min(i, E.doc.slides.length - 1)];
      E.slideId = vizinho ? vizinho.id : null;
      E.selecao = [];
    }
    commit(MODO_TEMPLATE ? 'Excluir layout' : 'Excluir slide');
    renderizarTudo();
    avisar(MODO_TEMPLATE ? 'Layout excluído.' : 'Slide excluído.', { acao: { texto: 'Desfazer', fn: desfazer } });
  }

  function alternarOculto(id) {
    const i = indiceDoSlide(id || E.slideId);
    if (i < 0) return;
    const s = E.doc.slides[i];
    s.oculto = !s.oculto;
    commit(s.oculto ? 'Ocultar slide' : 'Mostrar slide');
    renderizarListaSlides();
    if (s.id === E.slideId) { renderizarQuadro(); renderizarPainel(); }
  }

  function moverSlide(de, para) {
    if (de === para || de < 0 || para < 0 || para >= E.doc.slides.length) return;
    const [s] = E.doc.slides.splice(de, 1);
    E.doc.slides.splice(para, 0, s);
    commit('Mover slide');
    renderizarListaSlides();
    renderizarQuadro();
  }

  function menuDoSlide(id, ponto, ancora) {
    const i = indiceDoSlide(id);
    const s = E.doc.slides[i];
    if (!s) return;
    selecionarSlide(id);
    abrirMenu(ancora || document.body, [
      { icone: 'fa-solid fa-plus', texto: MODO_TEMPLATE ? 'Novo layout depois deste' : 'Novo slide depois deste', acao: () => novoSlide(id) },
      { icone: 'fa-regular fa-copy', texto: 'Duplicar', atalho: 'Ctrl D', acao: () => duplicarSlide(id) },
      { icone: s.oculto ? 'fa-regular fa-eye' : 'fa-regular fa-eye-slash', texto: s.oculto ? 'Mostrar na apresentação' : 'Ocultar na apresentação', acao: () => alternarOculto(id) },
      '-',
      { icone: 'fa-solid fa-arrow-up', texto: 'Mover para cima', desativado: i === 0, acao: () => moverSlide(i, i - 1) },
      { icone: 'fa-solid fa-arrow-down', texto: 'Mover para baixo', desativado: i === E.doc.slides.length - 1, acao: () => moverSlide(i, i + 1) },
      '-',
      { icone: 'fa-regular fa-trash-can', texto: 'Excluir', acao: () => excluirSlide(id) },
    ], ponto ? { ponto } : { alinhar: 'fim' });
  }

  // =================================================================== B. geometria

  function paraRad(g) { return (num(g, 0) * Math.PI) / 180; }
  function girar(p, graus) {
    const a = paraRad(graus);
    const c = Math.cos(a);
    const s = Math.sin(a);
    return { x: p.x * c - p.y * s, y: p.x * s + p.y * c };
  }
  function cantos(el) {
    const cx = el.x + el.w / 2;
    const cy = el.y + el.h / 2;
    return [[-1, -1], [1, -1], [1, 1], [-1, 1]].map((k) => {
      const v = girar({ x: (k[0] * el.w) / 2, y: (k[1] * el.h) / 2 }, el.rotacao || 0);
      return { x: cx + v.x, y: cy + v.y };
    });
  }
  /** Caixa alinhada aos eixos (considera a rotação). */
  function caixaAlinhada(el) {
    if (!num(el.rotacao, 0)) return { x: el.x, y: el.y, w: el.w, h: el.h };
    const pts = cantos(el);
    const xs = pts.map((p) => p.x);
    const ys = pts.map((p) => p.y);
    const x = Math.min.apply(null, xs);
    const y = Math.min.apply(null, ys);
    return { x, y, w: Math.max.apply(null, xs) - x, h: Math.max.apply(null, ys) - y };
  }
  function caixaDoGrupo(els) {
    if (!els.length) return null;
    let x1 = Infinity; let y1 = Infinity; let x2 = -Infinity; let y2 = -Infinity;
    els.forEach((el) => {
      const c = caixaAlinhada(el);
      x1 = Math.min(x1, c.x); y1 = Math.min(y1, c.y); x2 = Math.max(x2, c.x + c.w); y2 = Math.max(y2, c.y + c.h);
    });
    return { x: x1, y: y1, w: x2 - x1, h: y2 - y1 };
  }
  function intersecta(a, b) { return a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y; }
  function pontoNoSlide(ev) {
    const r = UI.caixa.getBoundingClientRect();
    return { x: (ev.clientX - r.left) / E.escala, y: (ev.clientY - r.top) / E.escala };
  }
  function elementoVisivelNoEditor(el) {
    if (el.tipo === 'apagar' || el.slot === 'area_conteudo') return MODO_TEMPLATE;
    return true;
  }

  /** Guias: centro/bordas do slide e bordas/centros dos outros elementos. */
  function calcularSnap(caixa, ignorar, opcoes) {
    const o = opcoes || {};
    const limiar = 7 / E.escala;
    const alvosX = [0, LARG / 2, LARG];
    const alvosY = [0, ALT / 2, ALT];
    const slide = slideAtual();
    (slide ? slide.elementos : []).forEach((el) => {
      if (ignorar.has(el.id) || !elementoVisivelNoEditor(el)) return;
      const c = caixaAlinhada(el);
      alvosX.push(c.x, c.x + c.w / 2, c.x + c.w);
      alvosY.push(c.y, c.y + c.h / 2, c.y + c.h);
    });
    function melhor(valores, alvos) {
      let ajuste = null;
      valores.forEach((v) => {
        if (v == null) return;
        alvos.forEach((a) => {
          const d = a - v;
          if (Math.abs(d) <= limiar && (ajuste === null || Math.abs(d) < Math.abs(ajuste))) ajuste = d;
        });
      });
      return ajuste || 0;
    }
    const vx = o.bordasX || [caixa.x, caixa.x + caixa.w / 2, caixa.x + caixa.w];
    const vy = o.bordasY || [caixa.y, caixa.y + caixa.h / 2, caixa.y + caixa.h];
    const dx = o.semX ? 0 : melhor(vx, alvosX);
    const dy = o.semY ? 0 : melhor(vy, alvosY);
    const guias = [];
    const marcadas = new Set();
    vx.forEach((v) => { if (v == null) return; alvosX.forEach((a) => { if (Math.abs(a - (v + dx)) < 0.5 && !marcadas.has('v' + Math.round(a))) { marcadas.add('v' + Math.round(a)); guias.push({ tipo: 'v', pos: a }); } }); });
    vy.forEach((v) => { if (v == null) return; alvosY.forEach((a) => { if (Math.abs(a - (v + dy)) < 0.5 && !marcadas.has('h' + Math.round(a))) { marcadas.add('h' + Math.round(a)); guias.push({ tipo: 'h', pos: a }); } }); });
    return { dx, dy, guias };
  }

  // =================================================================== B. sobreposição (seleção, alças, guias)

  const ALCAS = [['nw', 0, 0], ['n', 50, 0], ['ne', 100, 0], ['e', 100, 50], ['se', 100, 100], ['s', 50, 100], ['sw', 0, 100], ['w', 0, 50]];
  const ANGULO_ALCA = { n: 0, ne: 45, e: 90, se: 135, s: 180, sw: 225, w: 270, nw: 315 };

  function cursorDaAlca(alca, rotacao) {
    const a = (((ANGULO_ALCA[alca] + num(rotacao, 0)) % 180) + 180) % 180;
    const passo = Math.round(a / 45) % 4;
    return ['ns-resize', 'nesw-resize', 'ew-resize', 'nwse-resize'][passo];
  }

  function caixaNaTela(el, classe) {
    const s = E.escala;
    return h('div', {
      class: classe,
      style: {
        left: el.x * s + 'px', top: el.y * s + 'px', width: Math.max(1, el.w * s) + 'px', height: Math.max(1, el.h * s) + 'px',
        transform: num(el.rotacao, 0) ? 'rotate(' + el.rotacao + 'deg)' : '',
      },
    });
  }

  function adicionarAlcas(caixa, largTela, altTela, rotacao, grupo) {
    const pequeno = largTela < 36 || altTela < 36;
    ALCAS.forEach((a) => {
      const lado = a[0].length === 1;
      if (pequeno && lado) return;
      const classe = 'ed-alca' + (lado ? (a[0] === 'n' || a[0] === 's' ? ' lado-h' : ' lado-v') : '');
      caixa.appendChild(h('div', {
        class: classe, dataset: { alca: a[0], grupo: grupo ? '1' : '' },
        style: { left: a[1] + '%', top: a[2] + '%', cursor: cursorDaAlca(a[0], rotacao) },
        attrs: { 'aria-hidden': 'true' },
      }));
    });
  }

  function desenharSobreposicao() {
    const sobre = UI.sobre;
    if (!sobre) return;
    sobre.textContent = '';
    if (!E.doc || E.previa) return;
    const s = E.escala;
    const sel = selecionados();
    const interagindo = !!(E.interacao && E.interacao.movido);
    if (E.pairar && E.selecao.indexOf(E.pairar) < 0 && !E.interacao) {
      const el = elementoPorId(E.pairar);
      if (el) sobre.appendChild(caixaNaTela(el, 'ed-pairar'));
    }
    if (sel.length === 1) {
      const el = sel[0];
      const caixa = caixaNaTela(el, 'ed-sel' + (el.bloqueado ? ' bloqueado' : ''));
      const editando = E.editandoTexto || E.editandoCelula;
      if (!el.bloqueado && !editando && !interagindo) {
        adicionarAlcas(caixa, el.w * s, el.h * s, el.rotacao, false);
        caixa.appendChild(h('div', { class: 'ed-alca-giro', attrs: { 'aria-hidden': 'true' } }, icone('fa-solid fa-rotate')));
      }
      if (el.bloqueado) caixa.appendChild(h('span', { class: 'ed-cadeado', attrs: { title: 'Bloqueado' } }, icone('fa-solid fa-lock')));
      sobre.appendChild(caixa);
    } else if (sel.length > 1) {
      sel.forEach((el) => sobre.appendChild(caixaNaTela(el, 'ed-sel multi' + (el.bloqueado ? ' bloqueado' : ''))));
      const g = caixaDoGrupo(sel);
      const grupo = h('div', { class: 'ed-sel-grupo', style: { left: g.x * s + 'px', top: g.y * s + 'px', width: g.w * s + 'px', height: g.h * s + 'px' } });
      if (!interagindo && !sel.some((el) => el.bloqueado)) adicionarAlcas(grupo, g.w * s, g.h * s, 0, true);
      sobre.appendChild(grupo);
    }
    if (E.mostrarOrdemAnim) desenharOrdemAnimacoes(sobre);
    E.guias.forEach((g) => {
      sobre.appendChild(h('div', {
        class: 'ed-guia ' + g.tipo,
        style: g.tipo === 'v' ? { left: g.pos * s + 'px', top: '0', height: ALT * s + 'px' } : { top: g.pos * s + 'px', left: '0', width: LARG * s + 'px' },
      }));
    });
    if (E.laco) {
      sobre.appendChild(h('div', { class: 'ed-laco', style: { left: E.laco.x * s + 'px', top: E.laco.y * s + 'px', width: E.laco.w * s + 'px', height: E.laco.h * s + 'px' } }));
    }
    if (E.medida) {
      sobre.appendChild(h('div', { class: 'ed-medida', text: E.medida.texto, style: { left: E.medida.x * s + 'px', top: E.medida.y * s + 12 + 'px' } }));
    }
    if (E.editandoTexto && UI.minibarra) {
      sobre.appendChild(UI.minibarra);
      posicionarMinibarra();
    }
  }

  function desenharOrdemAnimacoes(sobre) {
    const slide = slideAtual();
    if (!slide) return;
    const plano = R.planoAnimacoes(slide, { template: MODO_TEMPLATE });
    let n = 0;
    plano.forEach((passo, iPasso) => {
      passo.forEach((lote) => {
        n += 1;
        lote.forEach((el) => {
          const s = E.escala;
          sobre.appendChild(h('span', {
            class: 'ed-ordem-anim', text: (iPasso > 0 ? '▸' : '') + n,
            attrs: { title: iPasso > 0 ? 'Entra no clique ' + iPasso : 'Entra ao abrir o slide' },
            style: { left: el.x * s + 'px', top: el.y * s + 'px' },
          }));
        });
      });
    });
  }

  // =================================================================== B. eventos do palco

  function ligarEventosDoPalco() {
    UI.palco.addEventListener('pointerdown', aoPressionarPalco);
    UI.palco.addEventListener('dblclick', aoDuploClique);
    UI.palco.addEventListener('contextmenu', aoMenuContexto);
    UI.palco.addEventListener('wheel', (ev) => {
      if (!(ev.ctrlKey || ev.metaKey)) return;
      ev.preventDefault();
      // Pinça do trackpad chega como Ctrl + rolagem com passos pequenos.
      const fator = Math.exp(-ev.deltaY * (Math.abs(ev.deltaY) < 20 ? 0.01 : 0.002));
      E.zoom = limitar(E.escala * fator, PASSOS_ZOOM[0], PASSOS_ZOOM[PASSOS_ZOOM.length - 1]);
      aplicarZoom({ x: ev.clientX, y: ev.clientY });
    }, { passive: false });
    UI.escala.addEventListener('click', (ev) => {
      // Links dentro dos textos não navegam no editor.
      if (ev.target.closest && ev.target.closest('a')) ev.preventDefault();
    });
    UI.escala.addEventListener('mouseover', (ev) => {
      const n = ev.target.closest && ev.target.closest('.apres-el');
      const id = n ? n.getAttribute('data-id') : null;
      if (id === E.pairar) return;
      E.pairar = id;
      if (!E.interacao) desenharSobreposicao();
    });
    UI.escala.addEventListener('mouseleave', () => { if (E.pairar) { E.pairar = null; if (!E.interacao) desenharSobreposicao(); } });
    ['dragenter', 'dragover'].forEach((tipo) => UI.palco.addEventListener(tipo, (ev) => {
      if (!ev.dataTransfer || Array.prototype.indexOf.call(ev.dataTransfer.types || [], 'Files') < 0) return;
      ev.preventDefault();
      ev.dataTransfer.dropEffect = 'copy';
      UI.palco.classList.add('ed-arrastando-arquivo');
    }));
    UI.palco.addEventListener('dragleave', (ev) => {
      if (!UI.palco.contains(ev.relatedTarget)) UI.palco.classList.remove('ed-arrastando-arquivo');
    });
    UI.palco.addEventListener('drop', (ev) => {
      UI.palco.classList.remove('ed-arrastando-arquivo');
      const arquivos = ev.dataTransfer && ev.dataTransfer.files ? Array.prototype.slice.call(ev.dataTransfer.files) : [];
      if (!arquivos.length) return;
      ev.preventDefault();
      const p = pontoNoSlide(ev);
      arquivos.forEach((f, i) => inserirArquivo(f, { centro: { x: p.x + i * 40, y: p.y + i * 40 } }));
    });
  }

  function capturar(ev, aoMover, aoSoltar, aoCancelar) {
    const id = ev.pointerId;
    function mover(e) { if (e.pointerId === id) aoMover(e); }
    function soltar(e) {
      if (e.pointerId !== id) return;
      limpar();
      aoSoltar(e);
    }
    function tecla(e) {
      if (e.key !== 'Escape') return;
      e.preventDefault();
      e.stopPropagation();
      limpar();
      if (aoCancelar) aoCancelar(); else aoSoltar(null);
    }
    function limpar() {
      window.removeEventListener('pointermove', mover);
      window.removeEventListener('pointerup', soltar);
      window.removeEventListener('pointercancel', soltar);
      window.removeEventListener('keydown', tecla, true);
    }
    window.addEventListener('pointermove', mover);
    window.addEventListener('pointerup', soltar);
    window.addEventListener('pointercancel', soltar);
    window.addEventListener('keydown', tecla, true);
  }

  function avisarBloqueado() {
    avisar('Elemento bloqueado. Desbloqueie no painel para mover ou apagar.', { duracao: 2600 });
  }

  function aoPressionarPalco(ev) {
    if (ev.pointerType === 'mouse' && ev.button !== 0) return;
    if (!E.doc || !slideAtual()) return;
    const alvo = ev.target;
    if (alvo.closest('.ed-minibarra')) return;
    const alca = alvo.closest('.ed-alca');
    if (alca) { comecarRedimensionar(ev, alca); return; }
    if (alvo.closest('.ed-alca-giro')) { comecarGirar(ev); return; }
    if (E.editandoTexto && E.editandoTexto.conteudo.contains(alvo)) return;
    if (E.editandoCelula && E.editandoCelula.celula.contains(alvo)) return;
    finalizarEdicoes();
    fecharPopover();
    const nodoEl = alvo.closest('.apres-el');
    const p = pontoNoSlide(ev);
    const aditivo = ev.shiftKey || ev.metaKey || ev.ctrlKey;
    if (nodoEl && UI.escala.contains(nodoEl)) {
      ev.preventDefault();
      let id = nodoEl.getAttribute('data-id');
      if (ev.altKey) id = elementoAbaixo(ev) || id;
      if (aditivo) {
        if (E.selecao.indexOf(id) >= 0) {
          E.selecao = E.selecao.filter((x) => x !== id);
          selecaoMudou();
          return;
        }
        E.selecao = E.selecao.concat([id]);
      } else if (E.selecao.indexOf(id) < 0 || ev.altKey) {
        E.selecao = [id];
      }
      selecaoMudou();
      comecarMover(ev, p);
      return;
    }
    if (!aditivo && E.selecao.length > 1) {
      const g = caixaDoGrupo(selecionados());
      if (g && p.x >= g.x && p.x <= g.x + g.w && p.y >= g.y && p.y <= g.y + g.h) { ev.preventDefault(); comecarMover(ev, p); return; }
    }
    if (!aditivo && E.selecao.length) { E.selecao = []; selecaoMudou(); }
    ev.preventDefault();
    UI.palco.focus({ preventScroll: true });
    comecarLaco(ev, p, aditivo);
  }

  /** Alt + clique: escolhe o elemento de baixo (útil para texto sobre forma). */
  function elementoAbaixo(ev) {
    const ids = [];
    document.elementsFromPoint(ev.clientX, ev.clientY).forEach((n) => {
      const el = n.closest && n.closest('.apres-el');
      if (el && UI.escala.contains(el)) {
        const id = el.getAttribute('data-id');
        if (ids.indexOf(id) < 0) ids.push(id);
      }
    });
    if (ids.length < 2) return null;
    const atual = E.selecao.length === 1 ? ids.indexOf(E.selecao[0]) : -1;
    return ids[(atual + 1) % ids.length];
  }

  function comecarMover(ev, p0) {
    const els = selecionados();
    const moveis = els.filter((el) => !el.bloqueado);
    const inicio = moveis.map((el) => ({ el, x: el.x, y: el.y }));
    const caixa0 = caixaDoGrupo(moveis);
    const it = { tipo: 'mover', movido: false };
    E.interacao = it;
    capturar(ev, (e2) => {
      const p = pontoNoSlide(e2);
      let dx = p.x - p0.x;
      let dy = p.y - p0.y;
      if (!it.movido) {
        if (Math.hypot(dx * E.escala, dy * E.escala) < 4) return;
        if (!moveis.length) {
          if (!it.avisou && els.some((el) => el.bloqueado)) { it.avisou = true; avisarBloqueado(); }
          return;
        }
        it.movido = true;
        E.pairar = null;
      }
      if (e2.shiftKey) { if (Math.abs(dx) > Math.abs(dy)) dy = 0; else dx = 0; }
      let guias = [];
      if (!e2.altKey && caixa0) {
        const snap = calcularSnap({ x: caixa0.x + dx, y: caixa0.y + dy, w: caixa0.w, h: caixa0.h }, new Set(E.selecao),
          { semX: e2.shiftKey && dx === 0, semY: e2.shiftKey && dy === 0 });
        dx += snap.dx;
        dy += snap.dy;
        guias = snap.guias;
      }
      inicio.forEach((i) => {
        i.el.x = i.x + dx;
        i.el.y = i.y + dy;
        const n = nodoDoElemento(i.el.id);
        if (n) R.aplicarCaixa(n, i.el);
      });
      E.guias = guias;
      E.medida = { texto: 'X ' + Math.round(caixa0.x + dx) + '   Y ' + Math.round(caixa0.y + dy), x: caixa0.x + dx + caixa0.w / 2, y: caixa0.y + dy + caixa0.h };
      desenharSobreposicao();
      atualizarCamposGeometria();
    }, () => {
      E.interacao = null;
      E.guias = [];
      E.medida = null;
      if (it.movido) {
        commit(moveis.length > 1 ? 'Mover elementos' : 'Mover');
        agendarMiniaturas();
        atualizarCamposGeometria();
      }
      desenharSobreposicao();
    }, () => {
      inicio.forEach((i) => { i.el.x = i.x; i.el.y = i.y; const n = nodoDoElemento(i.el.id); if (n) R.aplicarCaixa(n, i.el); });
      E.interacao = null; E.guias = []; E.medida = null;
      desenharSobreposicao();
      atualizarCamposGeometria();
    });
  }

  function comecarRedimensionar(ev, alca) {
    const qual = alca.getAttribute('data-alca');
    const emGrupo = alca.getAttribute('data-grupo') === '1';
    const els = selecionados();
    if (!els.length || els.some((el) => el.bloqueado)) { avisarBloqueado(); return; }
    ev.preventDefault();
    ev.stopPropagation();
    const sx = qual.indexOf('e') >= 0 ? 1 : qual.indexOf('w') >= 0 ? -1 : 0;
    const sy = qual.indexOf('s') >= 0 ? 1 : qual.indexOf('n') >= 0 ? -1 : 0;
    const p0 = pontoNoSlide(ev);
    const MIN = 8;
    const it = { tipo: 'redimensionar', movido: false };
    E.interacao = it;
    const originais = els.map((el) => ({ el, x: el.x, y: el.y, w: el.w, h: el.h }));
    const g0 = emGrupo || els.length > 1 ? caixaDoGrupo(els) : { x: els[0].x, y: els[0].y, w: els[0].w, h: els[0].h };
    const rot = emGrupo || els.length > 1 ? 0 : num(els[0].rotacao, 0);
    const c0 = { x: g0.x + g0.w / 2, y: g0.y + g0.h / 2 };
    const ancoraLocal = { x: (-sx * g0.w) / 2, y: (-sy * g0.h) / 2 };
    const vAncora = girar(ancoraLocal, rot);
    const ancora = { x: c0.x + vAncora.x, y: c0.y + vAncora.y };

    capturar(ev, (e2) => {
      it.movido = true;
      const p = pontoNoSlide(e2);
      const d = girar({ x: p.x - p0.x, y: p.y - p0.y }, -rot);
      const fator = e2.altKey ? 2 : 1;
      let w = sx ? g0.w + sx * d.x * fator : g0.w;
      let hh = sy ? g0.h + sy * d.y * fator : g0.h;
      w = Math.max(MIN, w);
      hh = Math.max(MIN, hh);
      if (e2.shiftKey) {
        const razao = g0.w / g0.h;
        if (sx && sy) { if (w / g0.w > hh / g0.h) hh = w / razao; else w = hh * razao; }
        else if (sx) hh = w / razao;
        else w = hh * razao;
      }
      let centro;
      if (e2.altKey) centro = c0;
      else {
        const v = girar({ x: (-sx * w) / 2, y: (-sy * hh) / 2 }, rot);
        centro = { x: ancora.x - v.x, y: ancora.y - v.y };
      }
      let box = { x: centro.x - w / 2, y: centro.y - hh / 2, w, h: hh };
      E.guias = [];
      if (!rot && !e2.shiftKey && !e2.altKey) {
        const snap = calcularSnap(box, new Set(E.selecao), {
          bordasX: [sx === 1 ? box.x + box.w : sx === -1 ? box.x : null], bordasY: [sy === 1 ? box.y + box.h : sy === -1 ? box.y : null],
          semX: !sx, semY: !sy,
        });
        if (sx === 1) box.w += snap.dx;
        if (sx === -1) { box.x += snap.dx; box.w -= snap.dx; }
        if (sy === 1) box.h += snap.dy;
        if (sy === -1) { box.y += snap.dy; box.h -= snap.dy; }
        E.guias = snap.guias;
      }
      if (els.length === 1 && !emGrupo) {
        const el = els[0];
        el.x = box.x; el.y = box.y; el.w = Math.max(MIN, box.w); el.h = Math.max(MIN, box.h);
        redesenharElemento(el);
      } else {
        const fx = box.w / g0.w;
        const fy = box.h / g0.h;
        originais.forEach((o) => {
          const cx = box.x + (o.x + o.w / 2 - g0.x) * fx;
          const cy = box.y + (o.y + o.h / 2 - g0.y) * fy;
          o.el.w = Math.max(MIN, o.w * fx);
          o.el.h = Math.max(MIN, o.h * fy);
          o.el.x = cx - o.el.w / 2;
          o.el.y = cy - o.el.h / 2;
          redesenharElemento(o.el);
        });
      }
      E.medida = { texto: Math.round(box.w) + ' × ' + Math.round(box.h), x: box.x + box.w / 2, y: box.y + box.h };
      desenharSobreposicao();
      atualizarCamposGeometria();
    }, () => {
      E.interacao = null; E.guias = []; E.medida = null;
      if (it.movido) {
        els.forEach((el) => { if (el.tipo === 'texto' && el.autoajuste) ajustarTextoNoQuadro(el); });
        commit('Redimensionar');
        agendarQuadro();
        agendarMiniaturas();
      }
      desenharSobreposicao();
      atualizarCamposGeometria();
    }, () => {
      originais.forEach((o) => { Object.assign(o.el, { x: o.x, y: o.y, w: o.w, h: o.h }); redesenharElemento(o.el); });
      E.interacao = null; E.guias = []; E.medida = null;
      desenharSobreposicao();
      atualizarCamposGeometria();
    });
  }

  function comecarGirar(ev) {
    const el = selecionados()[0];
    if (!el || el.bloqueado) return;
    ev.preventDefault();
    ev.stopPropagation();
    const centro = { x: el.x + el.w / 2, y: el.y + el.h / 2 };
    const p0 = pontoNoSlide(ev);
    const a0 = Math.atan2(p0.y - centro.y, p0.x - centro.x);
    const rot0 = num(el.rotacao, 0);
    const it = { tipo: 'girar', movido: false };
    E.interacao = it;
    capturar(ev, (e2) => {
      it.movido = true;
      const p = pontoNoSlide(e2);
      let r = rot0 + ((Math.atan2(p.y - centro.y, p.x - centro.x) - a0) * 180) / Math.PI;
      r = ((((r + 180) % 360) + 360) % 360) - 180;
      if (e2.shiftKey) r = Math.round(r / 15) * 15;
      else {
        [-180, -90, 0, 90, 180].forEach((t) => { if (Math.abs(r - t) < 4) r = t; });
      }
      if (r <= -180) r += 360;
      el.rotacao = Math.round(r * 10) / 10;
      const n = nodoDoElemento(el.id);
      if (n) R.aplicarCaixa(n, el);
      E.medida = { texto: Math.round(el.rotacao) + '°', x: el.x + el.w / 2, y: el.y + el.h };
      desenharSobreposicao();
      atualizarCamposGeometria();
    }, () => {
      E.interacao = null; E.medida = null;
      if (it.movido) { commit('Girar'); agendarMiniaturas(); }
      desenharSobreposicao();
    }, () => {
      el.rotacao = rot0;
      const n = nodoDoElemento(el.id);
      if (n) R.aplicarCaixa(n, el);
      E.interacao = null; E.medida = null;
      desenharSobreposicao();
      atualizarCamposGeometria();
    });
  }

  function comecarLaco(ev, p0, aditivo) {
    const base = aditivo ? E.selecao.slice() : [];
    const it = { tipo: 'laco', movido: false };
    E.interacao = it;
    capturar(ev, (e2) => {
      const p = pontoNoSlide(e2);
      if (!it.movido && Math.hypot((p.x - p0.x) * E.escala, (p.y - p0.y) * E.escala) < 4) return;
      it.movido = true;
      E.laco = { x: Math.min(p0.x, p.x), y: Math.min(p0.y, p.y), w: Math.abs(p.x - p0.x), h: Math.abs(p.y - p0.y) };
      const slide = slideAtual();
      const atingidos = slide.elementos.filter((el) => !el.bloqueado && elementoVisivelNoEditor(el) && intersecta(caixaAlinhada(el), E.laco)).map((el) => el.id);
      E.selecao = base.concat(atingidos.filter((id) => base.indexOf(id) < 0));
      desenharSobreposicao();
    }, () => {
      E.interacao = null;
      E.laco = null;
      selecaoMudou();
    });
  }

  function aoDuploClique(ev) {
    const nodoEl = ev.target.closest && ev.target.closest('.apres-el');
    if (!nodoEl || !UI.escala.contains(nodoEl)) return;
    const id = nodoEl.getAttribute('data-id');
    const el = elementoPorId(id);
    if (!el) return;
    if (el.bloqueado) { avisarBloqueado(); return; }
    if (el.tipo === 'texto') editarTexto(id, { ponto: { x: ev.clientX, y: ev.clientY } });
    else if (el.tipo === 'tabela') {
      const cel = ev.target.closest('[data-linha]');
      editarCelula(id, cel ? Number(cel.getAttribute('data-linha')) : 0, cel ? Number(cel.getAttribute('data-coluna')) : 0);
    } else if (el.tipo === 'imagem') trocarMidiaDoElemento(el, nodoEl);
    else if (el.tipo === 'icone') abrirSeletorIcone(nodoEl, (classe) => mudarElemento(el, (x) => { x.icone = classe; }, 'Trocar ícone', { painel: true }));
    else if (el.tipo === 'video') trocarMidiaDoElemento(el, nodoEl);
  }

  function aoMenuContexto(ev) {
    if (E.editandoTexto || E.editandoCelula) return;
    ev.preventDefault();
    const nodoEl = ev.target.closest && ev.target.closest('.apres-el');
    const ponto = { x: ev.clientX, y: ev.clientY };
    if (nodoEl && UI.escala.contains(nodoEl)) {
      const id = nodoEl.getAttribute('data-id');
      if (E.selecao.indexOf(id) < 0) { E.selecao = [id]; selecaoMudou(); }
      const sel = selecionados();
      const um = sel.length === 1 ? sel[0] : null;
      const bloqueado = sel.some((el) => el.bloqueado);
      abrirMenu(document.body, [
        um && um.tipo === 'texto' ? { icone: 'fa-solid fa-i-cursor', texto: 'Editar texto', atalho: 'Enter', acao: () => editarTexto(um.id) } : null,
        um && (um.tipo === 'imagem' || um.tipo === 'video') ? { icone: 'fa-solid fa-arrow-right-arrow-left', texto: um.tipo === 'imagem' ? 'Trocar imagem' : 'Trocar vídeo', acao: () => trocarMidiaDoElemento(um, nodoEl) } : null,
        { icone: 'fa-regular fa-copy', texto: 'Copiar', atalho: 'Ctrl C', acao: () => copiarSelecao(false) },
        { icone: 'fa-solid fa-scissors', texto: 'Recortar', atalho: 'Ctrl X', desativado: bloqueado, acao: () => copiarSelecao(true) },
        { icone: 'fa-regular fa-paste', texto: 'Colar', atalho: 'Ctrl V', desativado: !temAreaTransferencia(), acao: () => colarElementos() },
        { icone: 'fa-solid fa-clone', texto: 'Duplicar', atalho: 'Ctrl D', acao: duplicarSelecao },
        '-',
        { icone: 'fa-solid fa-arrow-up', texto: 'Trazer para frente', atalho: 'Ctrl ]', acao: () => ordenar('frente') },
        { icone: 'fa-solid fa-angles-up', texto: 'Trazer para o topo', atalho: 'Ctrl ⇧ ]', acao: () => ordenar('topo') },
        { icone: 'fa-solid fa-arrow-down', texto: 'Enviar para trás', atalho: 'Ctrl [', acao: () => ordenar('tras') },
        { icone: 'fa-solid fa-angles-down', texto: 'Enviar para o fundo', atalho: 'Ctrl ⇧ [', acao: () => ordenar('fundo') },
        '-',
        um && um.animacao && um.animacao.tipo !== 'nenhuma' ? { icone: 'fa-solid fa-play', texto: 'Visualizar animação', acao: () => visualizarAnimacao(um) } : null,
        { icone: bloqueado ? 'fa-solid fa-lock-open' : 'fa-solid fa-lock', texto: bloqueado ? 'Desbloquear' : 'Bloquear', acao: alternarBloqueio },
        { icone: 'fa-regular fa-trash-can', texto: 'Excluir', atalho: 'Del', desativado: bloqueado, acao: excluirSelecao },
      ].filter(Boolean), { ponto });
    } else {
      abrirMenu(document.body, [
        { icone: 'fa-regular fa-paste', texto: 'Colar', atalho: 'Ctrl V', desativado: !temAreaTransferencia(), acao: () => colarElementos(pontoNoSlide(ev)) },
        { icone: 'fa-solid fa-object-group', texto: 'Selecionar tudo', atalho: 'Ctrl A', acao: selecionarTudo },
        '-',
        { icone: 'fa-solid fa-plus', texto: MODO_TEMPLATE ? 'Novo layout' : 'Novo slide', acao: () => novoSlide(E.slideId) },
        { icone: 'fa-regular fa-copy', texto: MODO_TEMPLATE ? 'Duplicar layout' : 'Duplicar slide', acao: () => duplicarSlide(E.slideId) },
      ], { ponto });
    }
  }

  // =================================================================== B. seleção e operações

  let painelAgendado = false;
  function selecaoMudou() {
    const vistos = new Set();
    E.selecao = E.selecao.filter((id) => { if (vistos.has(id) || !elementoPorId(id)) return false; vistos.add(id); return true; });
    desenharSobreposicao();
    if (painelAgendado) return;
    painelAgendado = true;
    requestAnimationFrame(() => { painelAgendado = false; renderizarPainel(); });
  }

  function selecionarTudo() {
    const s = slideAtual();
    if (!s) return;
    finalizarEdicoes();
    E.selecao = s.elementos.filter((el) => elementoVisivelNoEditor(el) && !el.bloqueado).map((el) => el.id);
    selecaoMudou();
  }

  /** Alteração num elemento vinda do painel ou de atalhos: aplica, registra e redesenha. */
  function mudarElemento(el, fn, rotulo, opcoes) {
    const o = opcoes || {};
    if (E.editandoTexto || E.editandoCelula) finalizarEdicoes();
    const alvos = Array.isArray(el) ? el : [el];
    alvos.forEach((x) => fn(x));
    alvos.forEach((x) => { if (x.tipo === 'texto' && x.autoajuste && o.ajustar !== false) E.precisaAjustar = true; });
    commit(rotulo, o.chave);
    renderizarQuadro();
    if (E.precisaAjustar) {
      E.precisaAjustar = false;
      alvos.forEach((x) => { if (x.tipo === 'texto' && x.autoajuste) ajustarTextoNoQuadro(x); });
      commit(rotulo, o.chave || 'autoajuste-' + rotulo);
    }
    desenharSobreposicao();
    agendarMiniaturas();
    if (o.painel) renderizarPainel();
  }

  function mudarSlide(fn, rotulo, opcoes) {
    const o = opcoes || {};
    const s = slideAtual();
    if (!s) return;
    finalizarEdicoes();
    fn(s);
    commit(rotulo, o.chave);
    renderizarQuadro();
    agendarMiniaturas();
    if (o.painel) renderizarPainel();
  }

  function ordenar(modo) {
    const s = slideAtual();
    const ids = new Set(E.selecao);
    if (!s || !ids.size) return;
    finalizarEdicoes();
    const lista = s.elementos;
    if (modo === 'topo' || modo === 'fundo') {
      const escolhidos = lista.filter((el) => ids.has(el.id));
      const resto = lista.filter((el) => !ids.has(el.id));
      s.elementos = modo === 'topo' ? resto.concat(escolhidos) : escolhidos.concat(resto);
    } else if (modo === 'frente') {
      for (let i = lista.length - 2; i >= 0; i -= 1) {
        if (ids.has(lista[i].id) && !ids.has(lista[i + 1].id)) { const t = lista[i]; lista[i] = lista[i + 1]; lista[i + 1] = t; }
      }
    } else {
      for (let i = 1; i < lista.length; i += 1) {
        if (ids.has(lista[i].id) && !ids.has(lista[i - 1].id)) { const t = lista[i]; lista[i] = lista[i - 1]; lista[i - 1] = t; }
      }
    }
    const rotulos = { topo: 'Trazer para o topo', fundo: 'Enviar para o fundo', frente: 'Trazer para frente', tras: 'Enviar para trás' };
    if (commit(rotulos[modo])) { renderizarQuadro(); agendarMiniaturas(); desenharSobreposicao(); }
  }

  function alinhar(modo) {
    const sel = selecionados().filter((el) => !el.bloqueado);
    if (!sel.length) return;
    finalizarEdicoes();
    const ref = sel.length === 1 ? { x: 0, y: 0, w: LARG, h: ALT } : caixaDoGrupo(sel);
    sel.forEach((el) => {
      const c = caixaAlinhada(el);
      if (modo === 'esquerda') el.x += ref.x - c.x;
      if (modo === 'centro') el.x += ref.x + ref.w / 2 - (c.x + c.w / 2);
      if (modo === 'direita') el.x += ref.x + ref.w - (c.x + c.w);
      if (modo === 'topo') el.y += ref.y - c.y;
      if (modo === 'meio') el.y += ref.y + ref.h / 2 - (c.y + c.h / 2);
      if (modo === 'base') el.y += ref.y + ref.h - (c.y + c.h);
    });
    if (commit('Alinhar')) { renderizarQuadro(); agendarMiniaturas(); desenharSobreposicao(); atualizarCamposGeometria(); }
  }

  function distribuir(eixo) {
    const sel = selecionados().filter((el) => !el.bloqueado);
    if (sel.length < 3) { avisar('Selecione ao menos 3 elementos para distribuir.'); return; }
    finalizarEdicoes();
    const g = caixaDoGrupo(sel);
    const itens = sel.map((el) => ({ el, c: caixaAlinhada(el) }));
    if (eixo === 'h') {
      itens.sort((a, b) => (a.c.x + a.c.w / 2) - (b.c.x + b.c.w / 2));
      const total = itens.reduce((s, i) => s + i.c.w, 0);
      const vao = (g.w - total) / (itens.length - 1);
      let x = g.x;
      itens.forEach((i) => { i.el.x += x - i.c.x; x += i.c.w + vao; });
    } else {
      itens.sort((a, b) => (a.c.y + a.c.h / 2) - (b.c.y + b.c.h / 2));
      const total = itens.reduce((s, i) => s + i.c.h, 0);
      const vao = (g.h - total) / (itens.length - 1);
      let y = g.y;
      itens.forEach((i) => { i.el.y += y - i.c.y; y += i.c.h + vao; });
    }
    if (commit('Distribuir')) { renderizarQuadro(); agendarMiniaturas(); desenharSobreposicao(); }
  }

  function moverComTeclado(dx, dy) {
    const sel = selecionados();
    const moveis = sel.filter((el) => !el.bloqueado);
    if (!moveis.length) { if (sel.length) avisarBloqueado(); return; }
    moveis.forEach((el) => {
      el.x += dx; el.y += dy;
      const n = nodoDoElemento(el.id);
      if (n) R.aplicarCaixa(n, el);
    });
    commit('Mover', 'setas');
    desenharSobreposicao();
    atualizarCamposGeometria();
    agendarMiniaturas();
  }

  function duplicarSelecao() {
    const s = slideAtual();
    const sel = selecionados();
    if (!s || !sel.length) return;
    finalizarEdicoes();
    const ids = new Set(sel.map((el) => el.id));
    const copias = s.elementos.filter((el) => ids.has(el.id)).map((el) => {
      const c = clonar(el);
      c.id = novoId('e');
      c.x += 30; c.y += 30;
      c.bloqueado = false;
      return c;
    });
    s.elementos.push.apply(s.elementos, copias);
    E.selecao = copias.map((c) => c.id);
    commit('Duplicar');
    renderizarQuadro();
    selecaoMudou();
    agendarMiniaturas();
  }

  function excluirSelecao() {
    const s = slideAtual();
    const sel = selecionados();
    if (!s || !sel.length) return;
    finalizarEdicoes();
    const apagar = sel.filter((el) => !el.bloqueado);
    if (!apagar.length) { avisarBloqueado(); return; }
    const ids = new Set(apagar.map((el) => el.id));
    s.elementos = s.elementos.filter((el) => !ids.has(el.id));
    E.selecao = E.selecao.filter((id) => !ids.has(id));
    commit(apagar.length > 1 ? 'Excluir elementos' : 'Excluir elemento');
    renderizarQuadro();
    selecaoMudou();
    agendarMiniaturas();
    if (apagar.length < sel.length) avisar('Os elementos bloqueados foram mantidos.');
  }

  function alternarBloqueio() {
    const sel = selecionados();
    if (!sel.length) return;
    const bloquear = !sel.every((el) => el.bloqueado);
    mudarElemento(sel, (el) => { el.bloqueado = bloquear; }, bloquear ? 'Bloquear' : 'Desbloquear', { painel: true, ajustar: false });
  }

  // =================================================================== B. autoajuste de texto

  /** Maior tamanho inteiro (até o tamanho-base) em que o texto cabe na caixa. */
  function medirTamanhoQueCabe(caixa, conteudo, base) {
    const estilo = getComputedStyle(caixa);
    const disponivel = caixa.clientHeight - parseFloat(estilo.paddingTop || 0) - parseFloat(estilo.paddingBottom || 0);
    const cabe = (t) => {
      caixa.style.fontSize = t + 'px';
      return conteudo.offsetHeight <= disponivel + 1;
    };
    let alto = Math.max(TAM_MIN, Math.floor(base));
    if (cabe(alto)) return alto;
    let baixo = TAM_MIN;
    while (baixo < alto) {
      const meio = Math.ceil((baixo + alto) / 2);
      if (cabe(meio)) baixo = meio; else alto = meio - 1;
    }
    caixa.style.fontSize = baixo + 'px';
    return baixo;
  }

  function tamanhoBase(el) {
    const atual = num(el.estilo && el.estilo.tamanho, 36);
    if (E.tamanhoBase[el.id] == null) E.tamanhoBase[el.id] = atual;
    return Math.max(E.tamanhoBase[el.id], TAM_MIN);
  }

  /** Mede no nó do quadro e grava o tamanho que coube (não registra histórico). */
  function ajustarTextoNoQuadro(el) {
    if (!el || el.tipo !== 'texto' || !el.autoajuste) return false;
    const nodo = nodoDoElemento(el.id);
    const caixa = nodo && nodo.querySelector('.apres-texto');
    const conteudo = nodo && nodo.querySelector('.apres-texto-conteudo');
    if (!caixa || !conteudo) return false;
    const t = medirTamanhoQueCabe(caixa, conteudo, tamanhoBase(el));
    if (t === el.estilo.tamanho) return false;
    el.estilo.tamanho = t;
    return true;
  }

  /** Ao abrir: mede todos os slides com autoajuste num palco invisível e grava os tamanhos. */
  function ajustarDocumentoInteiro() {
    if (!E.doc) return 0;
    const palco = h('div', { attrs: { 'aria-hidden': 'true' }, style: { position: 'fixed', left: '-30000px', top: '0', width: LARG + 'px', height: ALT + 'px', visibility: 'hidden', pointerEvents: 'none', contain: 'layout style' } });
    document.body.appendChild(palco);
    let mudou = 0;
    try {
      E.doc.slides.forEach((s) => {
        const textos = s.elementos.filter((el) => el.tipo === 'texto' && el.autoajuste);
        if (!textos.length) return;
        palco.textContent = '';
        const nodo = R.criarSlide(s, E.doc, contextoRender());
        palco.appendChild(nodo);
        textos.forEach((el) => {
          const n = nodo.querySelector('.apres-el[data-id="' + R.cssEscape(el.id) + '"]');
          const caixa = n && n.querySelector('.apres-texto');
          const conteudo = n && n.querySelector('.apres-texto-conteudo');
          if (!caixa || !conteudo) return;
          const t = medirTamanhoQueCabe(caixa, conteudo, tamanhoBase(el));
          if (t !== el.estilo.tamanho) { el.estilo.tamanho = t; mudou += 1; }
        });
      });
    } finally {
      palco.remove();
    }
    return mudou;
  }

  // =================================================================== B. edição de texto no quadro

  function editarTexto(id, opcoes) {
    const o = opcoes || {};
    const el = elementoPorId(id);
    if (!el || el.tipo !== 'texto') return;
    if (el.bloqueado) { avisarBloqueado(); return; }
    finalizarEdicoes();
    fecharPopover();
    E.selecao = [id];
    const nodo = nodoDoElemento(id);
    if (!nodo) return;
    const conteudo = nodo.querySelector('.apres-texto-conteudo');
    const caixa = nodo.querySelector('.apres-texto');
    if (el.slot === 'paginacao') R.preencherHtml(conteudo, el.html);
    conteudo.setAttribute('contenteditable', 'true');
    conteudo.setAttribute('role', 'textbox');
    conteudo.setAttribute('aria-multiline', 'true');
    conteudo.setAttribute('aria-label', 'Texto do slide');
    conteudo.spellcheck = true;
    nodo.classList.add('ed-editando');
    const ed = { id, nodo, conteudo, caixa, original: el.html, tamanho: el.estilo.tamanho, faixa: null };
    E.editandoTexto = ed;
    try {
      document.execCommand('styleWithCSS', false, true);
      document.execCommand('defaultParagraphSeparator', false, 'div');
    } catch (e) { /* navegador sem suporte: segue com o padrão */ }
    ed.aoDigitar = debounce(() => {
      if (E.editandoTexto !== ed) return;
      const atual = elementoPorId(id);
      if (atual && atual.autoajuste) ed.tamanhoAjustado = medirTamanhoQueCabe(caixa, conteudo, tamanhoBase(atual));
    }, 90);
    ed.aoTeclar = (ev) => {
      if (ev.key === 'Escape' || (ev.key === 'Enter' && (ev.metaKey || ev.ctrlKey))) {
        ev.preventDefault();
        ev.stopPropagation();
        finalizarEdicaoTexto();
        UI.palco.focus({ preventScroll: true });
      } else if (ev.key === 'Tab') {
        ev.preventDefault();
      }
    };
    ed.aoColar = (ev) => {
      ev.preventDefault();
      const texto = (ev.clipboardData && ev.clipboardData.getData('text/plain')) || '';
      document.execCommand('insertText', false, texto);
    };
    ed.aoSair = (ev) => {
      const destino = ev.relatedTarget;
      if (destino && (conteudo.contains(destino) || (UI.minibarra && UI.minibarra.contains(destino)) || (destino.closest && destino.closest('.ed-pop')))) return;
      setTimeout(() => {
        if (E.editandoTexto !== ed) return;
        const ativo = document.activeElement;
        if (ativo && (conteudo.contains(ativo) || (UI.minibarra && UI.minibarra.contains(ativo)) || (ativo.closest && ativo.closest('.ed-pop')))) return;
        if (popAtual && popAtual.dono === 'minibarra') return;
        finalizarEdicaoTexto();
      }, 0);
    };
    ed.guardarFaixa = () => {
      const sel = window.getSelection();
      if (sel && sel.rangeCount && conteudo.contains(sel.anchorNode)) ed.faixa = sel.getRangeAt(0).cloneRange();
    };
    conteudo.addEventListener('input', ed.aoDigitar);
    conteudo.addEventListener('keydown', ed.aoTeclar);
    conteudo.addEventListener('paste', ed.aoColar);
    conteudo.addEventListener('focusout', ed.aoSair);
    document.addEventListener('selectionchange', ed.guardarFaixa);
    conteudo.focus({ preventScroll: true });
    const sel = window.getSelection();
    let faixa = null;
    if (o.ponto && document.caretRangeFromPoint) {
      faixa = document.caretRangeFromPoint(o.ponto.x, o.ponto.y);
      if (faixa && !conteudo.contains(faixa.startContainer)) faixa = null;
    }
    if (!faixa) {
      faixa = document.createRange();
      faixa.selectNodeContents(conteudo);
      if (o.noFim) faixa.collapse(false);
    }
    sel.removeAllRanges();
    sel.addRange(faixa);
    montarMinibarra();
    desenharSobreposicao();
    renderizarPainel();
  }

  function executarNoTexto(comando, valor) {
    const ed = E.editandoTexto;
    if (!ed) return;
    ed.conteudo.focus({ preventScroll: true });
    if (ed.faixa) {
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(ed.faixa);
    }
    try { document.execCommand('styleWithCSS', false, true); } catch (e) { /* ok */ }
    document.execCommand(comando, false, valor == null ? null : valor);
    ed.aoDigitar();
    atualizarMinibarra();
  }

  function montarMinibarra() {
    const b = (ic, dica, comando, valor) => h('button', {
      class: 'ed-btn-icone', type: 'button', dica, dataset: { comando: comando || '' },
      on: { mousedown: (ev) => ev.preventDefault(), click: () => executarNoTexto(comando, valor) },
    }, icone(ic));
    const botaoCor = h('button', {
      class: 'ed-btn-icone', type: 'button', dica: 'Cor do trecho selecionado', style: { position: 'relative' },
      on: {
        mousedown: (ev) => ev.preventDefault(),
        click: (ev) => {
          const ed = E.editandoTexto;
          if (!ed) return;
          const pop = abrirSeletorCor(ev.currentTarget, '', (cor) => {
            const css = corParaCss(cor);
            if (css) executarNoTexto('foreColor', css);
          }, { permitirVazio: false, semTema: true, titulo: 'Cor do trecho' });
          pop.dono = 'minibarra';
        },
      },
    }, icone('fa-solid fa-font'), h('span', { class: 'ed-traco-cor', style: { background: '#E23FCF' } }));
    const botaoLink = h('button', {
      class: 'ed-btn-icone', type: 'button', dica: 'Link no trecho (site ou #slide-N)',
      on: { mousedown: (ev) => ev.preventDefault(), click: (ev) => pedirLink(ev.currentTarget) },
    }, icone('fa-solid fa-link'));
    UI.minibarra = h('div', { class: 'ed-minibarra', attrs: { role: 'toolbar', 'aria-label': 'Formatação do texto' } },
      b('fa-solid fa-bold', 'Negrito (Ctrl+B)', 'bold'),
      b('fa-solid fa-italic', 'Itálico (Ctrl+I)', 'italic'),
      b('fa-solid fa-underline', 'Sublinhado (Ctrl+U)', 'underline'),
      b('fa-solid fa-strikethrough', 'Tachado', 'strikeThrough'),
      h('span', { class: 'ed-divisor' }),
      botaoCor,
      b('fa-solid fa-list-ul', 'Lista com marcadores', 'insertUnorderedList'),
      b('fa-solid fa-list-ol', 'Lista numerada', 'insertOrderedList'),
      botaoLink,
      b('fa-solid fa-text-slash', 'Limpar formatação do trecho', 'removeFormat'),
      h('span', { class: 'ed-divisor' }),
      h('button', {
        class: 'ed-btn-icone', type: 'button', dica: 'Concluir edição (Esc)',
        on: { mousedown: (ev) => ev.preventDefault(), click: () => { finalizarEdicaoTexto(); UI.palco.focus({ preventScroll: true }); } },
      }, icone('fa-solid fa-check')));
    UI.minibarra.addEventListener('pointerdown', (ev) => ev.stopPropagation());
    atualizarMinibarra();
  }

  function atualizarMinibarra() {
    if (!UI.minibarra) return;
    $$('[data-comando]', UI.minibarra).forEach((b) => {
      const c = b.getAttribute('data-comando');
      if (!c || c === 'removeFormat') return;
      let ativo = false;
      try { ativo = document.queryCommandState(c); } catch (e) { ativo = false; }
      b.setAttribute('aria-pressed', ativo ? 'true' : 'false');
    });
  }

  function posicionarMinibarra() {
    const ed = E.editandoTexto;
    const barra = UI.minibarra;
    if (!ed || !barra) return;
    const el = elementoPorId(ed.id);
    if (!el) return;
    const c = caixaAlinhada(el);
    const s = E.escala;
    const larg = barra.offsetWidth || 380;
    let top = c.y * s - 52;
    if (top < 4) top = (c.y + c.h) * s + 10;
    const left = limitar(c.x * s, 4, Math.max(4, LARG * s - larg - 4));
    barra.style.left = left + 'px';
    barra.style.top = top + 'px';
  }

  function pedirLink(ancora) {
    const ed = E.editandoTexto;
    if (!ed) return;
    const pop = abrirPopover(ancora, (p, fechar) => {
      const entrada = h('input', { class: 'ed-entrada', type: 'text', placeholder: 'https://… ou #slide-3', attrs: { autofocus: '', 'aria-label': 'Endereço do link' } });
      const erro = h('p', { class: 'ed-dica', style: { color: 'var(--ed-perigo)', display: 'none' }, text: 'Use um endereço http(s):// ou #slide-N.' });
      const aplicar = () => {
        const v = entrada.value.trim();
        if (!v) { fechar(); executarNoTexto('unlink'); return; }
        if (!R.linkValido(v)) { erro.style.display = ''; entrada.focus(); return; }
        fechar();
        executarNoTexto('createLink', v);
      };
      entrada.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); aplicar(); } });
      return h('div', { style: { display: 'flex', flexDirection: 'column', gap: '8px', padding: '6px', width: '280px' } },
        h('label', { class: 'ed-rotulo', text: 'Link do trecho' }), entrada, erro,
        h('div', { class: 'ed-linha' }, botao('Remover link', null, () => { fechar(); executarNoTexto('unlink'); }, 'ed-btn-p ed-btn-fantasma'), h('span', { class: 'ed-espaco' }), botao('Aplicar', null, aplicar, 'ed-btn-p ed-btn-primario')));
    }, { rotulo: 'Link' });
    pop.dono = 'minibarra';
  }

  function finalizarEdicaoTexto(opcoes) {
    const o = opcoes || {};
    const ed = E.editandoTexto;
    if (!ed) return;
    E.editandoTexto = null;
    ed.aoDigitar.cancelar();
    ed.conteudo.removeEventListener('input', ed.aoDigitar);
    ed.conteudo.removeEventListener('keydown', ed.aoTeclar);
    ed.conteudo.removeEventListener('paste', ed.aoColar);
    ed.conteudo.removeEventListener('focusout', ed.aoSair);
    document.removeEventListener('selectionchange', ed.guardarFaixa);
    ed.conteudo.removeAttribute('contenteditable');
    ed.nodo.classList.remove('ed-editando');
    if (popAtual && popAtual.dono === 'minibarra') fecharPopover();
    const s = slideAtual();
    const el = elementoPorId(ed.id, s);
    if (el && !o.descartar) {
      let html = R.sanearHtml(ed.conteudo.innerHTML)
        .replace(/(<br>\s*)+$/i, '')
        .replace(/^(<div><br><\/div>)+|(<div><br><\/div>)+$/gi, '');
      const vazio = !R.textoPlano(html).trim();
      if (vazio && !MODO_TEMPLATE && !el.slot) {
        s.elementos = s.elementos.filter((x) => x.id !== el.id);
        E.selecao = E.selecao.filter((id) => id !== el.id);
        commit('Excluir texto vazio');
      } else {
        if (vazio) html = '';
        el.html = html;
        if (el.autoajuste) {
          if (ed.tamanhoAjustado) el.estilo.tamanho = ed.tamanhoAjustado;
        }
        commit('Editar texto');
      }
    }
    renderizarQuadro();
    if (el && el.autoajuste && ajustarTextoNoQuadro(el)) { commit('Editar texto', 'autoajuste-texto'); renderizarQuadro(); }
    desenharSobreposicao();
    agendarMiniaturas();
    selecaoMudou();
  }

  // =================================================================== B. edição de célula de tabela

  function editarCelula(id, linha, coluna) {
    const el = elementoPorId(id);
    if (!el || el.tipo !== 'tabela') return;
    if (el.bloqueado) { avisarBloqueado(); return; }
    finalizarEdicoes();
    fecharPopover();
    E.selecao = [id];
    const nodo = nodoDoElemento(id);
    const celula = nodo && nodo.querySelector('[data-linha="' + linha + '"][data-coluna="' + coluna + '"]');
    if (!celula) return;
    try { celula.contentEditable = 'plaintext-only'; } catch (e) { celula.contentEditable = 'true'; }
    if (celula.contentEditable !== 'plaintext-only') celula.contentEditable = 'true';
    celula.setAttribute('role', 'textbox');
    celula.setAttribute('aria-label', 'Célula linha ' + (linha + 1) + ', coluna ' + (coluna + 1));
    const ed = { id, linha, coluna, celula };
    E.editandoCelula = ed;
    ed.aoTeclar = (ev) => {
      const nLinhas = el.linhas.length;
      const nCols = el.linhas[0].length;
      if (ev.key === 'Escape') { ev.preventDefault(); ev.stopPropagation(); finalizarEdicaoCelula(); return; }
      if (ev.key === 'Tab' || (ev.key === 'Enter' && !ev.shiftKey)) {
        ev.preventDefault();
        ev.stopPropagation();
        let l = linha;
        let c = coluna;
        if (ev.key === 'Enter') l += 1;
        else if (ev.shiftKey) { c -= 1; if (c < 0) { c = nCols - 1; l -= 1; } }
        else { c += 1; if (c >= nCols) { c = 0; l += 1; } }
        if (l < 0) { finalizarEdicaoCelula(); return; }
        const novaLinha = l >= nLinhas;
        finalizarEdicaoCelula({ proxima: { linha: l, coluna: c }, novaLinha });
      }
    };
    ed.aoSair = () => setTimeout(() => { if (E.editandoCelula === ed && !celula.contains(document.activeElement)) finalizarEdicaoCelula(); }, 0);
    ed.aoColar = (ev) => { ev.preventDefault(); document.execCommand('insertText', false, (ev.clipboardData && ev.clipboardData.getData('text/plain')) || ''); };
    celula.addEventListener('keydown', ed.aoTeclar);
    celula.addEventListener('focusout', ed.aoSair);
    celula.addEventListener('paste', ed.aoColar);
    celula.focus({ preventScroll: true });
    const faixa = document.createRange();
    faixa.selectNodeContents(celula);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(faixa);
    desenharSobreposicao();
  }

  function finalizarEdicaoCelula(opcoes) {
    const o = opcoes || {};
    const ed = E.editandoCelula;
    if (!ed) return;
    E.editandoCelula = null;
    ed.celula.removeEventListener('keydown', ed.aoTeclar);
    ed.celula.removeEventListener('focusout', ed.aoSair);
    ed.celula.removeEventListener('paste', ed.aoColar);
    ed.celula.removeAttribute('contenteditable');
    const el = elementoPorId(ed.id);
    if (el && !o.descartar && el.linhas[ed.linha]) {
      const texto = (ed.celula.innerText || '').replace(/\n$/, '');
      if (el.linhas[ed.linha][ed.coluna] !== texto) el.linhas[ed.linha][ed.coluna] = texto;
      if (o.novaLinha) {
        el.linhas.push(el.linhas[0].map(() => ''));
        el.h += Math.round(el.h / Math.max(1, el.linhas.length - 1));
      }
      commit(o.novaLinha ? 'Nova linha na tabela' : 'Editar célula');
    }
    renderizarQuadro();
    agendarMiniaturas();
    desenharSobreposicao();
    if (o.proxima && el) {
      editarCelula(ed.id, o.proxima.linha, o.proxima.coluna);
    } else {
      renderizarPainel();
    }
  }

  function finalizarEdicoes(opcoes) {
    if (E.editandoTexto) finalizarEdicaoTexto(opcoes);
    if (E.editandoCelula) finalizarEdicaoCelula(opcoes);
  }

  // =================================================================== C. controles do painel

  let seqCampo = 0;
  function idCampo() { seqCampo += 1; return 'ed-campo-' + seqCampo; }

  function campo(rotulo, controle, opcoes) {
    const o = opcoes || {};
    const id = controle.id || (controle.querySelector && (controle.querySelector('input, select, textarea, button') || {}).id);
    return h('div', { class: 'ed-campo', style: o.estilo || null },
      rotulo ? h('label', { text: rotulo, htmlFor: id || null }) : null, controle, o.dica ? h('p', { class: 'ed-dica', text: o.dica }) : null);
  }

  function formatarNumero(v, casas) {
    const n = num(v, 0);
    const f = Math.pow(10, casas || 0);
    return String(Math.round(n * f) / f).replace('.', ',');
  }
  function lerNumero(texto) {
    const n = parseFloat(String(texto).replace(/\s/g, '').replace(',', '.'));
    return Number.isFinite(n) ? n : null;
  }

  /** Número com setas (Shift ×10), Enter confirma e arrastar o rótulo curto ajusta o valor. */
  function campoNumero(o) {
    const id = idCampo();
    let valor = num(o.valor, 0);
    const passo = o.passo || 1;
    const entrada = h('input', {
      class: 'ed-entrada', type: 'text', id, value: formatarNumero(valor, o.casas), attrs: { inputmode: 'decimal', 'aria-label': o.rotuloAcessivel || o.rotulo || o.curto, autocomplete: 'off' },
    });
    function aplicar(v, final) {
      if (v == null) return;
      v = limitar(v, o.min == null ? -Infinity : o.min, o.max == null ? Infinity : o.max);
      const f = Math.pow(10, o.casas || 0);
      v = Math.round(v * f) / f;
      valor = v;
      o.aoMudar(v, !!final);
    }
    entrada.addEventListener('input', () => { const v = lerNumero(entrada.value); if (v != null) aplicar(v, false); });
    entrada.addEventListener('change', () => { const v = lerNumero(entrada.value); if (v != null) aplicar(v, true); entrada.value = formatarNumero(valor, o.casas); });
    entrada.addEventListener('keydown', (ev) => {
      if (ev.key === 'ArrowUp' || ev.key === 'ArrowDown') {
        ev.preventDefault();
        aplicar(valor + (ev.key === 'ArrowUp' ? 1 : -1) * passo * (ev.shiftKey ? 10 : 1), false);
        entrada.value = formatarNumero(valor, o.casas);
      } else if (ev.key === 'Enter') {
        entrada.blur();
      }
    });
    entrada.addEventListener('focus', () => entrada.select());
    const partes = [];
    if (o.curto) {
      const rot = h('span', { class: 'ed-numero-rotulo', text: o.curto, attrs: { 'aria-hidden': 'true', title: 'Arraste para ajustar' } });
      rot.addEventListener('pointerdown', (ev) => {
        ev.preventDefault();
        const x0 = ev.clientX;
        const v0 = valor;
        capturar(ev, (e2) => {
          aplicar(v0 + Math.round((e2.clientX - x0) / 2) * passo * (e2.shiftKey ? 10 : 1), false);
          entrada.value = formatarNumero(valor, o.casas);
        }, () => aplicar(valor, true));
      });
      partes.push(rot);
    }
    partes.push(entrada);
    if (o.unidade) partes.push(h('span', { class: 'ed-unidade', text: o.unidade }));
    const caixa = h('div', { class: 'ed-numero' }, partes);
    caixa.definir = (v) => { valor = num(v, 0); if (document.activeElement !== entrada) entrada.value = formatarNumero(valor, o.casas); };
    return o.rotulo ? Object.assign(campo(o.rotulo, caixa), { definir: caixa.definir }) : caixa;
  }

  function campoFaixa(o) {
    const id = idCampo();
    const fator = o.fator || 1;
    const faixa = h('input', { type: 'range', id, min: o.min, max: o.max, step: o.passo || 1, value: num(o.valor, 0) * fator, attrs: { 'aria-label': o.rotulo } });
    const numero = campoNumero({
      valor: num(o.valor, 0) * fator, min: o.min, max: o.max, passo: o.passo || 1, casas: o.casas, rotuloAcessivel: o.rotulo,
      unidade: o.unidade, aoMudar: (v, final) => { faixa.value = v; o.aoMudar(v / fator, final); },
    });
    faixa.addEventListener('input', () => { numero.definir(Number(faixa.value)); o.aoMudar(Number(faixa.value) / fator, false); });
    faixa.addEventListener('change', () => o.aoMudar(Number(faixa.value) / fator, true));
    return campo(o.rotulo, h('div', { class: 'ed-faixa' }, faixa, numero));
  }

  function campoSelect(o) {
    const id = idCampo();
    const sel = h('select', { class: 'ed-select', id, attrs: { 'aria-label': o.rotulo } });
    o.opcoes.forEach((op) => {
      if (op[0] === '__grupo') { sel.appendChild(h('optgroup', { label: op[1] })); return; }
      const alvo = sel.lastElementChild && sel.lastElementChild.tagName === 'OPTGROUP' ? sel.lastElementChild : sel;
      alvo.appendChild(h('option', { value: op[0], text: op[1], selected: String(op[0]) === String(o.valor == null ? '' : o.valor) }));
    });
    sel.addEventListener('change', () => o.aoMudar(sel.value));
    return o.rotulo ? campo(o.rotulo, sel) : sel;
  }

  /** Botões lado a lado; `multi` alterna cada um (negrito/itálico…). opcoes: [valor, rótulo|null, ícone|null, dica] */
  function campoSegmentado(o) {
    const grupo = h('div', { class: 'ed-segmentado', attrs: { role: o.multi ? 'group' : 'radiogroup', 'aria-label': o.rotulo || '' } });
    o.opcoes.forEach((op) => {
      const ativo = o.multi ? !!(o.valor || {})[op[0]] : String(o.valor) === String(op[0]);
      const b = h('button', {
        type: 'button', attrs: { 'aria-pressed': ativo ? 'true' : 'false' }, dica: op[3] || op[1],
        on: {
          click: () => {
            if (o.multi) {
              const novo = b.getAttribute('aria-pressed') !== 'true';
              b.setAttribute('aria-pressed', novo ? 'true' : 'false');
              o.aoMudar(op[0], novo);
            } else {
              $$('button', grupo).forEach((x) => x.setAttribute('aria-pressed', 'false'));
              b.setAttribute('aria-pressed', 'true');
              o.aoMudar(op[0]);
            }
          },
        },
      }, op[2] ? (op[2] instanceof Node ? op[2] : icone(op[2])) : null, op[1] && !op[2] ? op[1] : null);
      grupo.appendChild(b);
    });
    return o.rotulo ? campo(o.rotulo, grupo) : grupo;
  }

  function campoAlternar(o) {
    const id = idCampo();
    const entrada = h('input', { type: 'checkbox', id, checked: !!o.valor });
    entrada.addEventListener('change', () => o.aoMudar(entrada.checked));
    return h('label', { class: 'ed-alternar', htmlFor: id, dica: o.dica || null }, h('span', { text: o.rotulo }), entrada, h('i', { class: 'ed-chave', attrs: { 'aria-hidden': 'true' } }));
  }

  function campoTexto(o) {
    const id = idCampo();
    const entrada = o.area
      ? h('textarea', { class: 'ed-area', id, value: o.valor || '', placeholder: o.placeholder || '', rows: o.linhas || 4, attrs: { 'aria-label': o.rotulo } })
      : h('input', { class: 'ed-entrada', type: 'text', id, value: o.valor || '', placeholder: o.placeholder || '', maxLength: o.max || 500, attrs: { 'aria-label': o.rotulo, autocomplete: 'off' } });
    if (o.area) entrada.value = o.valor || '';
    entrada.addEventListener('input', () => o.aoMudar(entrada.value, false));
    entrada.addEventListener('change', () => o.aoMudar(entrada.value, true));
    return o.rotulo ? campo(o.rotulo, entrada, { dica: o.dica }) : entrada;
  }

  function amostraDeCor(valor) {
    const css = corParaCss(valor);
    const a = h('span', { class: 'ed-cor-amostra' + (css ? '' : ' ed-cor-vazia') }, h('b'));
    if (css) a.firstChild.style.background = css;
    return a;
  }
  function descreverCor(valor) {
    if (!valor) return 'Nenhuma';
    if (String(valor).indexOf('tema:') === 0) return 'Tema · ' + (ROTULOS_TEMA[valor.slice(5)] || valor.slice(5));
    const rgba = parseCor(valor);
    if (!rgba) return valor;
    const base = corHex({ r: rgba.r, g: rgba.g, b: rgba.b, a: 1 });
    return rgba.a < 1 ? base + ' · ' + Math.round(rgba.a * 100) + '%' : base;
  }

  function campoCor(o) {
    const botaoCor = h('button', { class: 'ed-cor', type: 'button', id: idCampo(), attrs: { 'aria-haspopup': 'dialog', 'aria-label': (o.rotulo || 'Cor') + ': ' + descreverCor(o.valor) } });
    let valor = o.valor || '';
    function pintar() {
      botaoCor.textContent = '';
      botaoCor.append(amostraDeCor(valor), h('span', { class: 'ed-cor-texto', text: descreverCor(valor) }));
      botaoCor.setAttribute('aria-label', (o.rotulo || 'Cor') + ': ' + descreverCor(valor));
    }
    pintar();
    botaoCor.addEventListener('click', () => {
      abrirSeletorCor(botaoCor, valor, (v, final) => { valor = v; pintar(); o.aoMudar(v, final); }, {
        permitirVazio: o.permitirVazio, semTema: o.semTema, titulo: o.rotulo,
      });
    });
    return o.rotulo ? campo(o.rotulo, botaoCor) : botaoCor;
  }

  const PALETA = ['#FFFFFF', '#D9C9E8', '#9A8FA8', '#4B4458', '#1A0F24', '#0B0612', '#000000', '#FFD15C',
    '#E23FCF', '#8B3DFF', '#5B21B6', '#2563EB', '#0EA5E9', '#10B981', '#84CC16', '#F97316', '#EF4444', '#EC4899',
    '#F59AE6', '#C4B5FD', '#FDE68A', '#A7F3D0', '#BFDBFE', '#FECACA'];

  function coresDoDocumento() {
    const vistas = new Map();
    const texto = JSON.stringify((E.doc && E.doc.slides) || []);
    (texto.match(/#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?\b|rgba?\([\d\s.,%]+\)/g) || []).forEach((c) => {
      const rgba = parseCor(c);
      if (!rgba) return;
      const hex = corHex(rgba);
      if (!vistas.has(hex)) vistas.set(hex, 0);
      vistas.set(hex, vistas.get(hex) + 1);
    });
    return Array.from(vistas.entries()).sort((a, b) => b[1] - a[1]).slice(0, 16).map((x) => x[0]);
  }

  /** Popover de cor: tema, cores do documento, paleta, personalizada com opacidade, nenhuma. */
  function abrirSeletorCor(ancora, valor, aoEscolher, opcoes) {
    const o = opcoes || {};
    return abrirPopover(ancora, (pop) => {
      let atual = valor || '';
      const caixa = h('div', { class: 'ed-seletor-cor' });
      if (o.titulo) caixa.appendChild(h('div', { class: 'ed-menu-titulo', style: { padding: '0' }, text: o.titulo }));
      const nativo = h('input', { type: 'color', attrs: { 'aria-label': 'Escolher cor personalizada' }, style: { width: '38px', height: '32px', padding: '0', border: '0', background: 'none', cursor: 'pointer' } });
      const hexa = h('input', { class: 'ed-entrada', type: 'text', placeholder: '#RRGGBB', attrs: { 'aria-label': 'Código da cor', autocomplete: 'off' } });
      const opac = h('input', { type: 'range', min: 0, max: 100, step: 1, attrs: { 'aria-label': 'Opacidade da cor' } });
      const opacTexto = h('span', { class: 'ed-rotulo', style: { width: '36px', textAlign: 'right' } });
      function sincronizar() {
        const rgba = parseCor(atual) || { r: 255, g: 255, b: 255, a: 1 };
        nativo.value = corHex({ r: rgba.r, g: rgba.g, b: rgba.b, a: 1 }).toLowerCase();
        if (document.activeElement !== hexa) hexa.value = atual && atual.indexOf('tema:') !== 0 ? corHex(rgba) : (atual ? descreverCor(atual) : '');
        opac.value = Math.round((atual ? rgba.a : 1) * 100);
        opacTexto.textContent = opac.value + '%';
        $$('.ed-paleta button', caixa).forEach((b) => b.classList.toggle('ativo', b.getAttribute('data-valor') === atual));
      }
      function escolher(v, final) {
        atual = v;
        sincronizar();
        aoEscolher(v, final);
      }
      function grade(valores, classe, rotular) {
        const g = h('div', { class: 'ed-paleta ' + (classe || '') });
        valores.forEach((v) => {
          const b = h('button', { type: 'button', dataset: { valor: v }, dica: rotular ? rotular(v) : descreverCor(v), on: { click: () => escolher(v, true) } }, h('b'));
          b.firstChild.style.background = corParaCss(v) || 'transparent';
          b.style.boxShadow = 'inset 0 0 0 1px rgba(0,0,0,.15)';
          g.appendChild(b);
        });
        return g;
      }
      if (!o.semTema) {
        caixa.appendChild(h('div', { class: 'ed-rotulo', text: 'Cores do tema' }));
        caixa.appendChild(grade(Object.keys(ROTULOS_TEMA).map((k) => 'tema:' + k), 'ed-paleta-tema', (v) => 'Tema · ' + ROTULOS_TEMA[v.slice(5)]));
      }
      const doDoc = coresDoDocumento();
      if (doDoc.length) {
        caixa.appendChild(h('div', { class: 'ed-rotulo', text: 'Usadas no documento' }));
        caixa.appendChild(grade(doDoc));
      }
      caixa.appendChild(h('div', { class: 'ed-rotulo', text: 'Paleta' }));
      caixa.appendChild(grade(PALETA));
      nativo.addEventListener('input', () => {
        const rgba = parseCor(nativo.value);
        rgba.a = Number(opac.value) / 100;
        escolher(corHex(rgba), false);
      });
      nativo.addEventListener('change', () => aoEscolher(atual, true));
      hexa.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); hexa.dispatchEvent(new Event('change')); } });
      hexa.addEventListener('change', () => {
        let v = hexa.value.trim();
        if (/^[0-9a-f]{6}([0-9a-f]{2})?$/i.test(v)) v = '#' + v;
        const rgba = R.resolverCor(v, null) ? parseCor(v) : null;
        if (!rgba) { hexa.value = atual ? descreverCor(atual) : ''; return; }
        escolher(corHex(rgba), true);
      });
      opac.addEventListener('input', () => {
        const rgba = parseCor(atual) || { r: 255, g: 255, b: 255, a: 1 };
        rgba.a = Number(opac.value) / 100;
        escolher(corHex(rgba), false);
      });
      opac.addEventListener('change', () => aoEscolher(atual, true));
      caixa.appendChild(h('div', { class: 'ed-rotulo', text: 'Personalizada' }));
      caixa.appendChild(h('div', { class: 'ed-linha' }, nativo, hexa));
      caixa.appendChild(h('div', { class: 'ed-linha' }, h('span', { class: 'ed-rotulo', text: 'Opacidade' }), opac, opacTexto));
      if (o.permitirVazio) {
        caixa.appendChild(botao('Nenhuma cor', 'fa-solid fa-ban', () => escolher('', true), 'ed-btn-p ed-btn-fantasma'));
      }
      sincronizar();
      return caixa;
    }, { rotulo: o.titulo || 'Cor', lado: o.lado || 'esquerda' });
  }

  // ---------------------------------------------------------------- gradiente

  const RE_COR_GRAD = '(#[0-9a-fA-F]{3,8}|rgba?\\([^)]*\\)|transparent)(?:\\s+([\\d.]+)%)?';
  function lerGradiente(valor) {
    const v = String(valor || '').trim();
    if (!v) return null;
    let m = v.match(new RegExp('^linear-gradient\\(\\s*(-?[\\d.]+)deg\\s*,\\s*' + RE_COR_GRAD + '\\s*,\\s*' + RE_COR_GRAD + '\\s*\\)$', 'i'));
    if (m) return { tipo: 'linear', angulo: Number(m[1]), c1: m[2], p1: m[3], c2: m[4], p2: m[5] };
    m = v.match(new RegExp('^radial-gradient\\(\\s*circle(?:\\s+at\\s+([\\d.]+)%\\s+([\\d.]+)%)?\\s*,\\s*' + RE_COR_GRAD + '\\s*,\\s*' + RE_COR_GRAD + '\\s*\\)$', 'i'));
    if (m) return { tipo: 'radial', px: m[1], py: m[2], c1: m[3], p1: m[4], c2: m[5], p2: m[6] };
    return false;
  }
  /** O servidor só aceita cores literais dentro de gradientes: `tema:*` vira hex aqui. */
  function corLiteral(c) {
    if (!c) return 'transparent';
    if (c === 'transparent') return c;
    const rgba = parseCor(c);
    return rgba ? corHex(rgba) : 'transparent';
  }
  function montarGradiente(g) {
    const parada = (c, p) => corLiteral(c) + (p != null && p !== '' ? ' ' + p + '%' : '');
    if (g.tipo === 'radial') {
      const pos = g.px != null && g.px !== '' ? ' at ' + g.px + '% ' + g.py + '%' : '';
      return 'radial-gradient(circle' + pos + ', ' + parada(g.c1, g.p1) + ', ' + parada(g.c2, g.p2) + ')';
    }
    return 'linear-gradient(' + Math.round(num(g.angulo, 135)) + 'deg, ' + parada(g.c1, g.p1) + ', ' + parada(g.c2, g.p2) + ')';
  }
  const GRADIENTES_PRONTOS = [
    ['Magenta → roxo', 'linear-gradient(135deg, #E23FCF, #8B3DFF)'],
    ['Noite', 'linear-gradient(180deg, #1A0F24, #0B0612)'],
    ['Brilho magenta', 'radial-gradient(circle at 80% 20%, #E23FCF88, #0B061200 60%)'],
    ['Brilho roxo', 'radial-gradient(circle at 15% 85%, #8B3DFFAA, #0B061200 55%)'],
    ['Dourado', 'linear-gradient(90deg, #FFD15C, #F97316)'],
    ['Véu escuro', 'linear-gradient(90deg, #0B0612F2, #0B061200)'],
  ];

  function campoGradiente(o) {
    const caixa = h('div', { class: 'ed-campo' });
    function desenhar(valor) {
      caixa.textContent = '';
      caixa.appendChild(h('label', { text: o.rotulo || 'Gradiente' }));
      const prontos = h('div', { class: 'ed-paleta', style: { gridTemplateColumns: 'repeat(7, 1fr)' } });
      prontos.appendChild(h('button', { type: 'button', dica: 'Sem gradiente', class: valor ? '' : 'ativo', on: { click: () => mudar('') } }, h('b', { class: 'ed-cor-vazia', style: { background: 'linear-gradient(135deg, transparent 45%, #ef4444 45%, #ef4444 55%, transparent 55%)' } })));
      GRADIENTES_PRONTOS.forEach((p) => {
        const b = h('button', { type: 'button', dica: p[0], class: valor === p[1] ? 'ativo' : '', on: { click: () => mudar(p[1]) } }, h('b'));
        b.firstChild.style.background = p[1] + ', #0B0612';
        prontos.appendChild(b);
      });
      caixa.appendChild(prontos);
      const g = lerGradiente(valor);
      if (g) {
        caixa.appendChild(campoSegmentado({ valor: g.tipo, opcoes: [['linear', 'Linear'], ['radial', 'Radial']], aoMudar: (t) => { g.tipo = t; mudar(montarGradiente(g)); } }));
        if (g.tipo === 'linear') {
          caixa.appendChild(campoFaixa({ rotulo: 'Ângulo', valor: g.angulo, min: 0, max: 360, unidade: '°', aoMudar: (v, final) => { g.angulo = v; mudar(montarGradiente(g), final, true); } }));
        }
        caixa.appendChild(h('div', { class: 'ed-grade' },
          campoCor({ rotulo: 'Cor inicial', valor: g.c1, aoMudar: (v, final) => { g.c1 = corLiteral(v); mudar(montarGradiente(g), final, true); } }),
          campoCor({ rotulo: 'Cor final', valor: g.c2, aoMudar: (v, final) => { g.c2 = corLiteral(v); mudar(montarGradiente(g), final, true); } })));
      } else if (g === false) {
        caixa.appendChild(campoTexto({
          rotulo: 'CSS do gradiente', valor, dica: 'Gradiente avançado: edite o CSS ou escolha um pronto acima.',
          aoMudar: (v, final) => { if (!v || R.resolverGradiente(v, E.doc.tema)) mudar(v, final, true); },
        }));
      } else {
        caixa.appendChild(botao('Criar gradiente', 'fa-solid fa-plus', () => mudar('linear-gradient(135deg, #E23FCF, #8B3DFF)'), 'ed-btn-p'));
      }
    }
    function mudar(v, final, semRedesenhar) {
      o.aoMudar(v, final !== false);
      if (!semRedesenhar) desenhar(v);
    }
    desenhar(o.valor || '');
    return caixa;
  }

  function campoBorda(borda, aoMudar) {
    const b = Object.assign(BORDA_VAZIA(), borda || {});
    return h('div', { class: 'ed-campo' },
      h('label', { text: 'Borda' }),
      h('div', { class: 'ed-grade' },
        campoCor({ valor: b.cor, permitirVazio: true, rotulo: '', aoMudar: (v, final) => aoMudar({ cor: v, largura: v && !b.largura ? (b.largura = 4) : b.largura }, final) }),
        campoNumero({ valor: b.largura, min: 0, max: 200, curto: 'L', unidade: 'px', rotuloAcessivel: 'Largura da borda', aoMudar: (v, final) => { b.largura = v; aoMudar({ largura: v }, final); } })),
      campoSegmentado({
        valor: b.estilo, rotulo: '', opcoes: [['solid', 'Contínua'], ['dashed', 'Tracejada'], ['dotted', 'Pontilhada']],
        aoMudar: (v) => aoMudar({ estilo: v }, true),
      }));
  }

  function campoSombra(valor, aoMudar) {
    return campoSelect({
      rotulo: 'Sombra', valor: valor || '', opcoes: [['', 'Nenhuma'], ['suave', 'Suave'], ['forte', 'Forte'], ['brilho', 'Brilho (cor primária)']],
      aoMudar: (v) => aoMudar(v),
    });
  }

  // ---------------------------------------------------------------- fontes

  const previasFontes = new Map();
  let filaPrevias = Promise.resolve();
  let previasRodando = 0;

  /** Prévia da fonte com só as letras do nome (&text=), registrada com outro nome para não misturar com a fonte real. */
  function carregarPreviaFonte(info) {
    if (!info || info.origem !== 'google') return Promise.resolve(info ? info.familia : null);
    if (previasFontes.has(info.familia)) return previasFontes.get(info.familia);
    const promessa = new Promise((resolve) => {
      const rodar = () => {
        previasRodando += 1;
        fetch(R.urlGoogleFonts(info, info.familia))
          .then((r) => (r.ok ? r.text() : Promise.reject(new Error('css'))))
          .then((css) => {
            const m = css.match(/src:\s*url\(([^)]+)\)/);
            if (!m) throw new Error('sem src');
            const nome = 'ed-previa ' + info.familia.replace(/[^A-Za-z0-9 ]/g, '');
            const face = new FontFace(nome, 'url(' + m[1] + ')');
            return face.load().then((f) => { document.fonts.add(f); resolve(nome); });
          })
          .catch(() => resolve(null))
          .finally(() => { previasRodando -= 1; });
      };
      // Até 6 downloads de prévia ao mesmo tempo.
      const tentar = () => { if (previasRodando < 6) rodar(); else setTimeout(tentar, 60); };
      tentar();
    });
    previasFontes.set(info.familia, promessa);
    return promessa;
  }

  const CATEGORIAS_FONTE = [['', 'Todas'], ['sans-serif', 'Sem serifa'], ['serif', 'Serifa'], ['display', 'Display'], ['handwriting', 'Manuscrita'], ['monospace', 'Mono'], ['sistema', 'Do sistema']];

  function fontesDoDocumento() {
    const usadas = R.fontesUsadas(E.doc).familias;
    return Array.from(usadas.keys());
  }

  function abrirSeletorFonte(ancora, valorAtual, aoEscolher, opcoes) {
    const o = opcoes || {};
    let categoria = '';
    return abrirPopover(ancora, (pop, fechar) => {
      const busca = h('input', { class: 'ed-entrada', type: 'search', placeholder: 'Buscar fonte…', attrs: { autofocus: '', 'aria-label': 'Buscar fonte', autocomplete: 'off' } });
      const filtros = h('div', { class: 'ed-fontes-filtros' });
      const lista = h('div', { class: 'ed-fontes-lista', attrs: { role: 'listbox', 'aria-label': 'Fontes' } });
      const rodape = h('p', { class: 'ed-dica' });
      const observador = new IntersectionObserver((entradas) => {
        entradas.forEach((ent) => {
          if (!ent.isIntersecting) return;
          const item = ent.target;
          observador.unobserve(item);
          const info = R.infoFonte(item.getAttribute('data-familia'));
          const nomeSpan = $('.ed-fonte-nome', item);
          if (!info) return;
          if (info.origem === 'sistema') { nomeSpan.style.fontFamily = R.pilhaFonte(info.familia); return; }
          carregarPreviaFonte(info).then((nome) => { if (nome) nomeSpan.style.fontFamily = '"' + nome + '", ' + (info.categoria === 'serif' ? 'serif' : 'sans-serif'); });
        });
      }, { root: lista, rootMargin: '120px' });
      CATEGORIAS_FONTE.forEach((c) => {
        filtros.appendChild(h('button', {
          type: 'button', class: 'ed-chip', text: c[1], attrs: { 'aria-pressed': c[0] === categoria ? 'true' : 'false' },
          on: { click: (ev) => { categoria = c[0]; $$('.ed-chip', filtros).forEach((x) => x.setAttribute('aria-pressed', 'false')); ev.currentTarget.setAttribute('aria-pressed', 'true'); desenhar(); } },
        }));
      });
      function item(familia, rotulo, categoriaTexto, valor) {
        const b = h('button', {
          type: 'button', class: 'ed-fonte-item' + (valor === valorAtual ? ' ativo' : ''), dataset: { familia },
          attrs: { role: 'option', 'aria-selected': valor === valorAtual ? 'true' : 'false' },
          on: { click: () => { fechar(); aoEscolher(valor); } },
        }, h('span', { class: 'ed-fonte-nome', text: rotulo }), h('span', { class: 'ed-fonte-cat', text: categoriaTexto }));
        observador.observe(b);
        return b;
      }
      function desenhar() {
        lista.textContent = '';
        const cat = R.catalogo;
        if (!cat) { lista.appendChild(h('div', { class: 'ed-carregando', style: { position: 'static', height: '120px', background: 'transparent' } }, h('span', { class: 'ed-giro' }))); return; }
        const q = semAcento(busca.value.trim());
        if (!q && !categoria) {
          if (o.permitirTema) {
            lista.appendChild(h('div', { class: 'ed-menu-titulo', text: 'Tema' }));
            lista.appendChild(item(o.familiaTema || 'Montserrat', 'Fonte do tema (' + (o.familiaTema || 'Montserrat') + ')', 'tema', ''));
          }
          const doc = fontesDoDocumento();
          if (doc.length) {
            lista.appendChild(h('div', { class: 'ed-menu-titulo', text: 'No documento' }));
            doc.forEach((f) => { const info = R.infoFonte(f); lista.appendChild(item(f, f, info ? info.categoria : '', f)); });
          }
          lista.appendChild(h('div', { class: 'ed-menu-titulo', text: 'Todas as fontes' }));
        }
        let todas = cat.google.map((f) => Object.assign({ origem: 'google' }, f)).concat(cat.sistema.map((f) => Object.assign({ origem: 'sistema' }, f)));
        if (categoria === 'sistema') todas = todas.filter((f) => f.origem === 'sistema');
        else if (categoria) todas = todas.filter((f) => f.categoria === categoria);
        if (q) {
          todas = todas.filter((f) => semAcento(f.familia).indexOf(q) >= 0);
          todas.sort((a, b) => (semAcento(a.familia).indexOf(q) === 0 ? 0 : 1) - (semAcento(b.familia).indexOf(q) === 0 ? 0 : 1));
        }
        const limite = 150;
        todas.slice(0, limite).forEach((f) => lista.appendChild(item(f.familia, f.familia, f.origem === 'sistema' ? 'sistema' : f.categoria, f.familia)));
        if (!todas.length) lista.appendChild(h('p', { class: 'ed-dica', style: { padding: '12px' }, text: 'Nenhuma fonte com esse nome.' }));
        rodape.textContent = todas.length > limite ? 'Mostrando ' + limite + ' de ' + todas.length + '. Digite para encontrar outras.' : todas.length + ' fontes';
      }
      busca.addEventListener('input', debounce(desenhar, 120));
      busca.addEventListener('keydown', (ev) => {
        if (ev.key === 'ArrowDown') { ev.preventDefault(); const p = $('.ed-fonte-item', lista); if (p) p.focus(); }
        if (ev.key === 'Enter') { ev.preventDefault(); const p = $('.ed-fonte-item', lista); if (p) p.click(); }
      });
      lista.addEventListener('keydown', (ev) => {
        const itens = $$('.ed-fonte-item', lista);
        const i = itens.indexOf(document.activeElement);
        if (ev.key === 'ArrowDown') { ev.preventDefault(); (itens[i + 1] || itens[i]).focus(); }
        if (ev.key === 'ArrowUp') { ev.preventDefault(); if (i <= 0) busca.focus(); else itens[i - 1].focus(); }
      });
      R.carregarCatalogoFontes().then(() => { if (document.contains(lista)) desenhar(); });
      desenhar();
      return h('div', { class: 'ed-fontes' }, busca, filtros, lista, rodape);
    }, { rotulo: 'Escolher fonte', lado: 'esquerda', aoFechar: () => {} });
  }

  function campoFonte(o) {
    const familiaExibida = o.valor || o.familiaTema || 'Montserrat';
    const b = h('button', { class: 'ed-btn ed-btn-bloco ed-seletor-fonte', type: 'button', id: idCampo(), attrs: { 'aria-haspopup': 'dialog' } },
      h('span', { text: o.valor ? o.valor : 'Do tema · ' + familiaExibida, style: { fontFamily: R.pilhaFonte(familiaExibida) } }),
      icone('fa-solid fa-chevron-down'));
    b.setAttribute('aria-label', (o.rotulo || 'Fonte') + ': ' + (o.valor || 'do tema'));
    b.addEventListener('click', () => abrirSeletorFonte(b, o.valor || '', (v) => o.aoMudar(v), { permitirTema: o.permitirTema, familiaTema: o.familiaTema }));
    return o.rotulo ? campo(o.rotulo, b) : b;
  }

  /** Carrega a família escolhida antes de redesenhar (senão o autoajuste mede com a fonte reserva). */
  function garantirFonte(familia, peso) {
    if (!familia) return Promise.resolve();
    return R.carregarFamilia(familia).then(() => Promise.race([
      document.fonts.load((peso || 400) + ' 40px "' + R.nomeFonteLimpo(familia) + '"').catch(() => null),
      esperar(5000),
    ]));
  }

  // ---------------------------------------------------------------- ícones

  let promessaIcones = null;
  function carregarIcones() {
    if (!promessaIcones) {
      const url = APRES.icones_url || ((APRES.static_url || '/static/') + 'apresentacoes/icones.json');
      promessaIcones = fetch(url, { credentials: 'same-origin' }).then((r) => (r.ok ? r.json() : null)).catch(() => null).then((d) => {
        const lista = [];
        [['solid', 'fa-solid'], ['regular', 'fa-regular'], ['brands', 'fa-brands']].forEach((par) => {
          ((d && d[par[0]]) || []).forEach((nome) => lista.push({ nome, estilo: par[0], classe: par[1] + ' fa-' + nome }));
        });
        return lista;
      });
    }
    return promessaIcones;
  }

  // Busca em português: termo → nomes em inglês do Font Awesome.
  const SINONIMOS_ICONES = {
    casa: 'house home', inicio: 'house', usuario: 'user', pessoa: 'user person', pessoas: 'users people', equipe: 'users people-group',
    time: 'users people-group', cliente: 'user handshake', dinheiro: 'money coins dollar wallet sack', venda: 'cart bag store chart receipt',
    vendas: 'cart bag store chart', loja: 'store shop', carrinho: 'cart', sacola: 'bag', celular: 'mobile phone', telefone: 'phone mobile',
    ligacao: 'phone', email: 'envelope at', mensagem: 'message comment envelope', conversa: 'comments comment message', chat: 'comments message',
    grafico: 'chart', crescimento: 'arrow-trend-up chart-line', queda: 'arrow-trend-down', meta: 'bullseye flag trophy', objetivo: 'bullseye flag',
    trofeu: 'trophy award', premio: 'trophy award medal gift', medalha: 'medal award', estrela: 'star', coracao: 'heart', curtir: 'thumbs-up heart',
    certo: 'check circle-check', ok: 'check circle-check thumbs-up', erro: 'xmark circle-xmark bug', errado: 'xmark', alerta: 'triangle-exclamation bell',
    atencao: 'triangle-exclamation exclamation', aviso: 'triangle-exclamation bell bullhorn', informacao: 'circle-info info', ajuda: 'circle-question question life-ring',
    duvida: 'circle-question question', calendario: 'calendar', data: 'calendar', relogio: 'clock stopwatch', hora: 'clock', tempo: 'clock hourglass stopwatch',
    endereco: 'location-dot map', localizacao: 'location-dot map-pin', mapa: 'map location-dot', seguranca: 'shield lock', cadeado: 'lock', escudo: 'shield',
    chave: 'key', configuracao: 'gear gears sliders', engrenagem: 'gear gears', ferramenta: 'wrench screwdriver toolbox hammer', ideia: 'lightbulb',
    lampada: 'lightbulb', foguete: 'rocket', lancamento: 'rocket', internet: 'wifi globe signal', rede: 'network-wired sitemap wifi', nuvem: 'cloud',
    baixar: 'download', enviar: 'upload paper-plane share', arquivo: 'file', documento: 'file file-lines', pasta: 'folder', imprimir: 'print',
    camera: 'camera', foto: 'camera image', imagem: 'image images', video: 'video film play', musica: 'music', microfone: 'microphone',
    livro: 'book', estudo: 'book graduation-cap', treinamento: 'graduation-cap chalkboard-user book', escola: 'school graduation-cap', formatura: 'graduation-cap',
    saude: 'heart-pulse hospital', carro: 'car', caminhao: 'truck', entrega: 'truck box', caixa: 'box', aviao: 'plane', viagem: 'plane suitcase',
    predio: 'building', empresa: 'building briefcase', trabalho: 'briefcase', fabrica: 'industry', busca: 'magnifying-glass', pesquisa: 'magnifying-glass',
    lupa: 'magnifying-glass', filtro: 'filter', lista: 'list', tarefa: 'list-check square-check', editar: 'pen pencil', lapis: 'pencil pen',
    lixeira: 'trash', excluir: 'trash xmark', adicionar: 'plus', mais: 'plus', menos: 'minus', seta: 'arrow', proximo: 'arrow-right angle-right',
    voltar: 'arrow-left angle-left', cima: 'arrow-up', baixo: 'arrow-down', desconto: 'percent tag', porcentagem: 'percent', etiqueta: 'tag tags',
    preco: 'tag dollar', cartao: 'credit-card', pagamento: 'credit-card money', banco: 'building-columns', carteira: 'wallet', presente: 'gift',
    bandeira: 'flag', sino: 'bell', notificacao: 'bell', olho: 'eye', ver: 'eye', mao: 'hand', parceria: 'handshake', acordo: 'handshake',
    feliz: 'face-smile face-grin', sorriso: 'face-smile', triste: 'face-frown', cafe: 'mug-hot', comida: 'utensils burger', energia: 'bolt',
    raio: 'bolt', fogo: 'fire', agua: 'droplet water', sol: 'sun', lua: 'moon', mundo: 'earth globe', planeta: 'earth', codigo: 'code',
    computador: 'computer laptop desktop', notebook: 'laptop', bateria: 'battery', jogo: 'gamepad', qrcode: 'qrcode', barras: 'barcode',
    chip: 'sim-card microchip', sinal: 'signal tower-broadcast', antena: 'tower-broadcast tower-cell', fibra: 'network-wired ethernet',
    suporte: 'headset', atendimento: 'headset comments', fone: 'headphones headset', relatorio: 'file-lines chart-column', apresentacao: 'person-chalkboard chalkboard',
    quadro: 'chalkboard', reuniao: 'people-group handshake', lider: 'crown user-tie', gerente: 'user-tie', chefe: 'user-tie crown', troca: 'arrows-rotate right-left',
    renovar: 'arrows-rotate rotate', reciclar: 'recycle', planta: 'seedling leaf', folha: 'leaf', grafico_pizza: 'chart-pie', pizza: 'chart-pie',
    coroa: 'crown', diamante: 'gem', joia: 'gem', megafone: 'bullhorn', anuncio: 'bullhorn', compartilhar: 'share share-nodes', link: 'link',
    senha: 'key lock', perfil: 'user id-card', crachá: 'id-badge', cracha: 'id-badge id-card', contrato: 'file-signature', assinatura: 'signature file-signature',
  };

  function termosDaBusca(q) {
    const termos = [];
    semAcento(q).split(/\s+/).filter(Boolean).forEach((p) => {
      termos.push(p);
      Object.keys(SINONIMOS_ICONES).forEach((k) => { if (semAcento(k).indexOf(p) === 0) termos.push.apply(termos, SINONIMOS_ICONES[k].split(' ')); });
    });
    return Array.from(new Set(termos));
  }

  function abrirSeletorIcone(ancora, aoEscolher) {
    let estilo = '';
    return abrirPopover(ancora, (pop, fechar) => {
      const busca = h('input', { class: 'ed-entrada', type: 'search', placeholder: 'Buscar ícone (ex.: casa, venda, meta)', attrs: { autofocus: '', 'aria-label': 'Buscar ícone', autocomplete: 'off' } });
      const filtros = h('div', { class: 'ed-fontes-filtros' });
      const grade = h('div', { class: 'ed-icones-grade', attrs: { role: 'listbox', 'aria-label': 'Ícones' } });
      const rodape = h('p', { class: 'ed-icones-rodape' });
      [['', 'Todos'], ['solid', 'Sólidos'], ['regular', 'Contorno'], ['brands', 'Marcas']].forEach((c) => {
        filtros.appendChild(h('button', {
          type: 'button', class: 'ed-chip', text: c[1], attrs: { 'aria-pressed': c[0] === estilo ? 'true' : 'false' },
          on: { click: (ev) => { estilo = c[0]; $$('.ed-chip', filtros).forEach((x) => x.setAttribute('aria-pressed', 'false')); ev.currentTarget.setAttribute('aria-pressed', 'true'); desenhar(); } },
        }));
      });
      let todos = [];
      function desenhar() {
        grade.textContent = '';
        const q = busca.value.trim();
        let lista = estilo ? todos.filter((i) => i.estilo === estilo) : todos;
        if (q) {
          const termos = termosDaBusca(q);
          lista = lista.map((i) => {
            let nota = 0;
            termos.forEach((t) => {
              if (i.nome === t) nota = Math.max(nota, 3);
              else if (i.nome.indexOf(t) === 0) nota = Math.max(nota, 2);
              else if (i.nome.indexOf(t) >= 0) nota = Math.max(nota, 1);
            });
            return { i, nota };
          }).filter((x) => x.nota > 0).sort((a, b) => b.nota - a.nota).map((x) => x.i);
        }
        lista.slice(0, 196).forEach((i) => {
          grade.appendChild(h('button', {
            type: 'button', dica: i.nome.replace(/-/g, ' ') + (i.estilo === 'regular' ? ' (contorno)' : i.estilo === 'brands' ? ' (marca)' : ''),
            attrs: { role: 'option' }, on: { click: () => { fechar(); aoEscolher(i.classe); } },
          }, icone(i.classe)));
        });
        rodape.textContent = lista.length ? (lista.length > 196 ? 'Mostrando 196 de ' + lista.length + ' ícones' : lista.length + ' ícones') : 'Nenhum ícone encontrado. Tente outra palavra (em português ou inglês).';
      }
      busca.addEventListener('input', debounce(desenhar, 100));
      busca.addEventListener('keydown', (ev) => { if (ev.key === 'ArrowDown') { ev.preventDefault(); const p = $('button', grade); if (p) p.focus(); } });
      grade.addEventListener('keydown', (ev) => {
        const itens = $$('button', grade);
        const i = itens.indexOf(document.activeElement);
        const passo = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: 7, ArrowUp: -7 }[ev.key];
        if (passo) { ev.preventDefault(); const alvo = itens[i + passo]; if (alvo) alvo.focus(); else if (passo < 0) busca.focus(); }
      });
      grade.appendChild(h('span', { class: 'ed-giro', style: { gridColumn: '1 / -1', margin: '20px auto' } }));
      carregarIcones().then((lista) => { todos = lista; if (document.contains(grade)) desenhar(); });
      return h('div', { class: 'ed-icones' }, busca, filtros, grade, rodape);
    }, { rotulo: 'Escolher ícone', lado: 'esquerda' });
  }

  // =================================================================== C. mídias (envio e biblioteca)

  const LIMITES = {
    IMAGEM: { ext: ['.jpg', '.jpeg', '.png', '.webp', '.gif'], max: 15 * 1024 * 1024, nome: 'imagem' },
    VIDEO: { ext: ['.mp4', '.webm', '.mov'], max: 200 * 1024 * 1024, nome: 'vídeo' },
  };
  const EXT_POR_MIME = { 'image/png': '.png', 'image/jpeg': '.jpg', 'image/webp': '.webp', 'image/gif': '.gif', 'video/mp4': '.mp4', 'video/webm': '.webm', 'video/quicktime': '.mov' };

  function tipoDoArquivo(arquivo) {
    const nome = (arquivo.name || '').toLowerCase();
    const ext = nome.lastIndexOf('.') >= 0 ? nome.slice(nome.lastIndexOf('.')) : '';
    if (LIMITES.IMAGEM.ext.indexOf(ext) >= 0) return 'IMAGEM';
    if (LIMITES.VIDEO.ext.indexOf(ext) >= 0) return 'VIDEO';
    if (/^image\/(png|jpeg|webp|gif)$/.test(arquivo.type)) return 'IMAGEM';
    if (/^video\/(mp4|webm|quicktime)$/.test(arquivo.type)) return 'VIDEO';
    return null;
  }

  function escolherArquivos(aceitar, multiplo) {
    return new Promise((resolve) => {
      const entrada = h('input', { type: 'file', accept: aceitar, multiple: !!multiplo, style: { display: 'none' } });
      entrada.addEventListener('change', () => { resolve(Array.prototype.slice.call(entrada.files || [])); entrada.remove(); });
      document.body.appendChild(entrada);
      entrada.click();
    });
  }

  /** Envia para urls.midias (com o dono certo: apresentação ou template) e devolve a mídia. */
  async function enviarMidia(arquivo, opcoes) {
    const o = opcoes || {};
    const tipo = o.tipo || tipoDoArquivo(arquivo);
    if (!tipo) throw new ErroApi('Formato não aceito. Envie imagem (JPG, PNG, WEBP, GIF) ou vídeo (MP4, WEBM, MOV).', 400);
    if (arquivo.size > LIMITES[tipo].max) throw new ErroApi('Arquivo grande demais (máximo ' + Math.round(LIMITES[tipo].max / 1048576) + ' MB para ' + LIMITES[tipo].nome + ').', 400);
    let nome = arquivo.name || '';
    if (!/\.[a-z0-9]{2,4}$/i.test(nome)) nome = (nome || (tipo === 'IMAGEM' ? 'imagem-colada' : 'video')) + (EXT_POR_MIME[arquivo.type] || (tipo === 'IMAGEM' ? '.png' : '.mp4'));
    const form = new FormData();
    form.append('arquivo', arquivo, nome);
    form.append('nome', nome);
    if (MODO_TEMPLATE) form.append('template', String(APRES.upload_template || APRES.id));
    else form.append('apresentacao', String(APRES.id));
    if (o.origem) form.append('origem', o.origem);
    // .webm sem este campo vira áudio no servidor.
    if (tipo === 'VIDEO') form.append('tipo', 'VIDEO');
    const aviso = avisar('Enviando ' + LIMITES[tipo].nome + '… 0%', { duracao: 600000 });
    const texto = $('span', aviso);
    try {
      const r = await enviarFormulario(URLS.midias, form, (p) => { texto.textContent = 'Enviando ' + LIMITES[tipo].nome + '… ' + Math.round(p * 100) + '%'; });
      E.bibliotecaCache = null;
      return r.midia;
    } finally {
      aviso.remove();
    }
  }

  function medirImagemLocal(arquivo) {
    if (typeof createImageBitmap !== 'function') return Promise.resolve(null);
    return createImageBitmap(arquivo).then((bmp) => { const d = { w: bmp.width, h: bmp.height }; if (bmp.close) bmp.close(); return d; }).catch(() => null);
  }
  function medirImagemUrl(url) {
    return new Promise((resolve) => {
      const img = new Image();
      const t = setTimeout(() => resolve(null), 6000);
      img.onload = () => { clearTimeout(t); resolve({ w: img.naturalWidth, h: img.naturalHeight }); };
      img.onerror = () => { clearTimeout(t); resolve(null); };
      img.src = url;
    });
  }
  function medirVideo(url) {
    return new Promise((resolve) => {
      const v = document.createElement('video');
      const t = setTimeout(() => resolve(null), 6000);
      v.preload = 'metadata';
      v.muted = true;
      v.onloadedmetadata = () => { clearTimeout(t); resolve({ w: v.videoWidth || 1280, h: v.videoHeight || 720, duracao: v.duration }); v.removeAttribute('src'); v.load(); };
      v.onerror = () => { clearTimeout(t); resolve(null); };
      v.src = url;
    });
  }
  function caberEm(dim, maxW, maxH) {
    const w = (dim && dim.w) || 16;
    const h2 = (dim && dim.h) || 9;
    const f = Math.min(maxW / w, maxH / h2, 1e9);
    return { w: Math.round(w * f), h: Math.round(h2 * f) };
  }

  async function inserirArquivo(arquivo, opcoes) {
    const o = opcoes || {};
    const tipo = tipoDoArquivo(arquivo);
    if (!tipo) { avisar('"' + (arquivo.name || 'arquivo') + '" não é imagem nem vídeo aceito.', { tipo: 'erro' }); return null; }
    if (!slideAtual()) novoSlide(null);
    const slideId = E.slideId;
    try {
      const dim = tipo === 'IMAGEM' ? await medirImagemLocal(arquivo) : null;
      const midia = await enviarMidia(arquivo, { tipo, origem: o.origem });
      if (E.slideId !== slideId) selecionarSlide(slideId);
      if (tipo === 'IMAGEM') {
        const tam = caberEm(dim || (midia.largura ? { w: midia.largura, h: midia.altura } : await medirImagemUrl(midia.url)), 1100, 760);
        return inserirElemento(novoElemento('imagem', { src: midia.url, midia: midia.id, w: tam.w, h: tam.h, alt: (midia.nome || '').replace(/\.[a-z0-9]+$/i, '') }), 'Inserir imagem', { centro: o.centro });
      }
      const dimV = await medirVideo(midia.url);
      const tam = caberEm(dimV || { w: 16, h: 9 }, 1100, 700);
      return inserirElemento(novoElemento('video', { src: midia.url, midia: midia.id, w: tam.w, h: tam.h }), 'Inserir vídeo', { centro: o.centro });
    } catch (erro) {
      avisar(erro.message || 'Não foi possível enviar o arquivo.', { tipo: 'erro' });
      return null;
    }
  }

  function carregarBiblioteca(forcar) {
    if (!URLS.midias_lista) return Promise.resolve([]);
    if (!E.bibliotecaCache || forcar) {
      E.bibliotecaCache = api(URLS.midias_lista).then((r) => r.midias || []).catch((erro) => { E.bibliotecaCache = null; throw erro; });
    }
    return E.bibliotecaCache;
  }

  function abrirBiblioteca(ancora, tipo, aoEscolher, opcoes) {
    const o = opcoes || {};
    return abrirPopover(ancora, (pop, fechar) => {
      const grade = h('div', { class: 'ed-biblioteca-grade', attrs: { role: 'listbox', 'aria-label': 'Mídias enviadas' } }, h('span', { class: 'ed-giro', style: { gridColumn: '1 / -1', margin: '24px auto' } }));
      const enviar = botao(tipo === 'VIDEO' ? 'Enviar vídeo do computador' : 'Enviar imagem do computador', 'fa-solid fa-upload', async () => {
        const arquivos = await escolherArquivos(tipo === 'VIDEO' ? 'video/mp4,video/webm,video/quicktime,.mp4,.webm,.mov' : 'image/png,image/jpeg,image/webp,image/gif', false);
        if (!arquivos.length) return;
        fechar();
        try {
          const midia = await enviarMidia(arquivos[0], { tipo });
          aoEscolher(midia, arquivos[0]);
        } catch (erro) {
          avisar(erro.message, { tipo: 'erro' });
        }
      }, 'ed-btn-bloco ed-btn-primario');
      carregarBiblioteca().then((midias) => {
        if (!document.contains(grade)) return;
        grade.textContent = '';
        const lista = midias.filter((m) => m.tipo === tipo);
        if (!lista.length) grade.appendChild(h('p', { class: 'ed-dica', style: { gridColumn: '1 / -1', padding: '16px', textAlign: 'center' }, text: tipo === 'VIDEO' ? 'Nenhum vídeo enviado ainda.' : 'Nenhuma imagem enviada ainda.' }));
        lista.forEach((m) => {
          const miniatura = m.tipo === 'IMAGEM' ? h('img', { src: m.url, alt: '', loading: 'lazy', decoding: 'async' }) : h('div', { class: 'ed-bib-video' }, icone('fa-solid fa-film'));
          grade.appendChild(h('button', {
            type: 'button', attrs: { role: 'option' }, dica: m.nome || ('Mídia ' + m.id),
            on: { click: () => { fechar(); aoEscolher(m); } },
          }, miniatura, h('span', { text: m.nome || ('Mídia ' + m.id) })));
        });
      }).catch((erro) => {
        grade.textContent = '';
        grade.appendChild(h('p', { class: 'ed-dica', style: { gridColumn: '1 / -1', color: 'var(--ed-perigo)' }, text: erro.message }));
      });
      return h('div', { class: 'ed-biblioteca' },
        h('div', { class: 'ed-menu-titulo', style: { padding: '0' }, text: o.titulo || (tipo === 'VIDEO' ? 'Vídeos' : 'Imagens') }),
        enviar, grade,
        tipo === 'IMAGEM' ? h('p', { class: 'ed-dica', text: 'Dica: arraste uma imagem para o slide ou cole com Ctrl+V.' }) : null);
    }, { rotulo: 'Biblioteca de mídias', lado: o.lado || 'esquerda' });
  }

  function trocarMidiaDoElemento(el, ancora) {
    const tipo = el.tipo === 'video' ? 'VIDEO' : 'IMAGEM';
    abrirBiblioteca(ancora || UI.caixa, tipo, (midia) => {
      mudarElemento(elementoPorId(el.id) || el, (x) => { x.src = midia.url; x.midia = midia.id; }, tipo === 'VIDEO' ? 'Trocar vídeo' : 'Trocar imagem', { painel: true });
    }, { titulo: tipo === 'VIDEO' ? 'Trocar vídeo' : 'Trocar imagem' });
  }

  // =================================================================== C. inserir

  function novoElemento(tipo, extra) {
    const base = { id: novoId('e'), tipo, x: 0, y: 0, w: 400, h: 300, rotacao: 0, opacidade: 1, bloqueado: false, slot: '', animacao: ANIM_VAZIA(), link: '' };
    const porTipo = {
      texto: { html: 'Texto', estilo: padraoEstiloTexto(), autoajuste: true },
      imagem: { src: '', midia: null, ajuste: 'cover', foco: { x: 0.5, y: 0.5 }, raio: 0, borda: BORDA_VAZIA(), sombra: '', filtro: '', alt: '' },
      forma: { forma: 'retangulo', preenchimento: 'tema:primaria', gradiente: '', borda: BORDA_VAZIA(), raio: 0, sombra: '' },
      icone: { icone: 'fa-solid fa-star', cor: 'tema:destaque', fundo: '', raio: 0, w: 200, h: 200 },
      video: { src: '', midia: null, poster: '', autoplay: true, loop: false, mudo: true, controles: false, raio: 0, ajuste: 'cover', w: 960, h: 540 },
      tabela: {
        linhas: [['Coluna 1', 'Coluna 2', 'Coluna 3'], ['', '', ''], ['', '', '']], cabecalho: true, w: 1200, h: 330,
        estilo: { fonte: '', tamanho: 32, cor: 'tema:texto', fundo_cabecalho: 'tema:primaria', cor_cabecalho: '#FFFFFF', fundo_linhas: 'rgba(255,255,255,.06)', fundo_alternado: 'rgba(255,255,255,.12)', borda: 'rgba(255,255,255,.2)', alinhamento: 'left', raio: 16 },
      },
      apagar: { w: 600, h: 300 },
    };
    const el = Object.assign(base, clonar(porTipo[tipo] || {}), extra || {});
    if (extra && extra.estilo && tipo === 'texto') el.estilo = Object.assign(padraoEstiloTexto(), extra.estilo);
    return normalizarElemento(el);
  }

  function inserirElemento(el, rotulo, opcoes) {
    const o = opcoes || {};
    if (!slideAtual()) novoSlide(null);
    const s = slideAtual();
    finalizarEdicoes();
    if (o.centro) {
      el.x = o.centro.x - el.w / 2;
      el.y = o.centro.y - el.h / 2;
    } else if (!o.manterPosicao) {
      // Em cascata: inserir várias vezes seguidas não empilha tudo no mesmo lugar.
      const deslocamento = (E.cascata || 0) % 6 * 36;
      E.cascata = (E.cascata || 0) + 1;
      clearTimeout(E.cascataTimer);
      E.cascataTimer = setTimeout(() => { E.cascata = 0; }, 4000);
      el.x = (LARG - el.w) / 2 + deslocamento;
      el.y = (ALT - el.h) / 2 + deslocamento;
    }
    if (el.tipo === 'texto') E.tamanhoBase[el.id] = el.estilo.tamanho;
    s.elementos.push(el);
    E.selecao = [el.id];
    commit(rotulo || 'Inserir');
    renderizarQuadro();
    selecaoMudou();
    agendarMiniaturas();
    if (el.tipo === 'texto' && o.editar) editarTexto(el.id);
    return el;
  }

  const PREDEFINICOES_TEXTO = [
    { chave: 'titulo', rotulo: 'Título', icone: 'fa-solid fa-heading', estilo: { papel: 'titulo', tamanho: 96, peso: 800, entrelinha: 1.05 }, html: 'Título', w: 1300, h: 140 },
    { chave: 'subtitulo', rotulo: 'Subtítulo', icone: 'fa-solid fa-font', estilo: { tamanho: 52, peso: 600, cor: 'tema:texto_suave' }, html: 'Subtítulo', w: 1100, h: 90 },
    { chave: 'corpo', rotulo: 'Corpo de texto', icone: 'fa-solid fa-align-left', estilo: { tamanho: 36, peso: 400, entrelinha: 1.35 }, html: 'Digite seu texto aqui', w: 900, h: 200 },
    { chave: 'lista', rotulo: 'Lista', icone: 'fa-solid fa-list-ul', estilo: { tamanho: 36, entrelinha: 1.4 }, html: '<ul><li>Primeiro item</li><li>Segundo item</li><li>Terceiro item</li></ul>', w: 900, h: 260 },
    { chave: 'rotulo', rotulo: 'Rótulo pequeno', icone: 'fa-solid fa-tag', estilo: { tamanho: 24, peso: 700, espacamento: 8, maiusculas: true, cor: 'tema:primaria' }, html: 'Rótulo', w: 600, h: 44, autoajuste: false },
    { chave: 'numero', rotulo: 'Número do slide', icone: 'fa-solid fa-hashtag', estilo: { tamanho: 22, peso: 700, espacamento: 2, alinhamento: 'right', cor: 'tema:texto_suave' }, html: '{n} / {total}', w: 210, h: 40, slot: 'paginacao', x: 1560, y: 986, autoajuste: false },
  ];

  function inserirTexto(pre) {
    const el = novoElemento('texto', {
      html: pre.html, w: pre.w, h: pre.h, slot: pre.slot || '', autoajuste: pre.autoajuste !== false,
      estilo: Object.assign(padraoEstiloTexto(), pre.estilo),
    });
    if (pre.x != null) { el.x = pre.x; el.y = pre.y; }
    inserirElemento(el, 'Inserir ' + pre.rotulo.toLowerCase(), { manterPosicao: pre.x != null, editar: pre.chave !== 'numero' });
  }

  function tamanhoPadraoForma(forma) {
    return { circulo: [320, 320], linha: [640, 24], seta: [420, 170], pilula: [520, 120], estrela: [320, 320], hexagono: [360, 320], triangulo: [340, 300] }[forma] || [420, 280];
  }

  function desenhoDeForma(forma) {
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('viewBox', '0 0 54 40');
    svg.setAttribute('aria-hidden', 'true');
    const cor = 'currentColor';
    let el;
    if (forma === 'linha') {
      el = document.createElementNS(ns, 'rect');
      el.setAttribute('x', '2'); el.setAttribute('y', '18'); el.setAttribute('width', '50'); el.setAttribute('height', '4'); el.setAttribute('rx', '2');
    } else if (forma === 'circulo') {
      el = document.createElementNS(ns, 'ellipse');
      el.setAttribute('cx', '27'); el.setAttribute('cy', '20'); el.setAttribute('rx', '18'); el.setAttribute('ry', '18');
    } else if (['retangulo', 'retangulo-arredondado', 'pilula'].indexOf(forma) >= 0) {
      el = document.createElementNS(ns, 'rect');
      el.setAttribute('x', '4'); el.setAttribute('y', '6'); el.setAttribute('width', '46'); el.setAttribute('height', forma === 'pilula' ? '20' : '28');
      if (forma === 'pilula') el.setAttribute('y', '10');
      el.setAttribute('rx', forma === 'retangulo' ? '1' : forma === 'pilula' ? '10' : '7');
    } else {
      el = document.createElementNS(ns, 'polygon');
      const w = forma === 'seta' ? 50 : 40;
      const hh = forma === 'seta' ? 26 : 36;
      const ox = (54 - w) / 2;
      const oy = (40 - hh) / 2;
      el.setAttribute('points', R.pontosForma(forma, w, hh).map((p) => (p[0] + ox).toFixed(1) + ',' + (p[1] + oy).toFixed(1)).join(' '));
    }
    el.setAttribute('fill', cor);
    svg.appendChild(el);
    return svg;
  }

  function inserirForma(forma) {
    const t = tamanhoPadraoForma(forma);
    const extra = { forma, w: t[0], h: t[1] };
    if (forma === 'linha') { extra.preenchimento = ''; extra.borda = { cor: 'tema:primaria', largura: 6, estilo: 'solid' }; }
    inserirElemento(novoElemento('forma', extra), 'Inserir forma');
  }

  function montarBarraInserir(barra) {
    const itens = [
      ['Texto', 'fa-solid fa-font', (b) => abrirMenu(b, PREDEFINICOES_TEXTO.map((p) => ({ icone: p.icone, texto: p.rotulo, acao: () => inserirTexto(p) })))],
      ['Imagem', 'fa-regular fa-image', (b) => abrirBiblioteca(b, 'IMAGEM', (midia, arquivo) => inserirMidiaEscolhida(midia, arquivo), { lado: 'baixo', titulo: 'Inserir imagem' })],
      ['Forma', 'fa-solid fa-shapes', (b) => abrirPopover(b, (pop, fechar) => h('div', { class: 'ed-grade-formas' },
        R.FORMAS.map((f) => h('button', { type: 'button', on: { click: () => { fechar(); inserirForma(f[0]); } } }, desenhoDeForma(f[0]), h('span', { text: f[1] })))), { rotulo: 'Formas', focarPrimeiro: true })],
      ['Ícone', 'fa-regular fa-face-smile', (b) => abrirSeletorIcone(b, (classe) => inserirElemento(novoElemento('icone', { icone: classe }), 'Inserir ícone'))],
      ['Vídeo', 'fa-solid fa-film', (b) => abrirBiblioteca(b, 'VIDEO', (midia) => inserirMidiaEscolhida(midia), { lado: 'baixo', titulo: 'Inserir vídeo' })],
      ['Tabela', 'fa-solid fa-table', (b) => abrirSeletorTabela(b)],
    ];
    itens.forEach((i) => {
      const b = botao(i[0], i[1], (ev) => i[2](ev.currentTarget), '', { attrs: { 'aria-haspopup': 'true' }, dica: 'Inserir ' + i[0].toLowerCase() });
      barra.appendChild(b);
    });
    if (MODO_TEMPLATE) {
      barra.appendChild(h('span', { class: 'ed-divisor' }));
      barra.appendChild(botao('Área de conteúdo', 'fa-regular fa-square', inserirAreaConteudo, 'ed-btn-template', { dica: 'Onde a IA coloca o conteúdo deste layout' }));
      barra.appendChild(botao('Apagar do fundo', 'fa-solid fa-eraser', inserirApagar, 'ed-btn-template', { dica: 'Marque uma área da imagem de fundo para apagar' }));
      barra.appendChild(botao('Limpar fundo', 'fa-solid fa-broom', () => limparFundo(), 'ed-btn-template', { dica: 'Apaga do fundo as áreas marcadas' }));
    }
  }

  async function inserirMidiaEscolhida(midia) {
    if (midia.tipo === 'VIDEO') {
      const dim = await medirVideo(midia.url);
      const t = caberEm(dim || { w: 16, h: 9 }, 1100, 700);
      inserirElemento(novoElemento('video', { src: midia.url, midia: midia.id, w: t.w, h: t.h }), 'Inserir vídeo');
    } else {
      const dim = midia.largura ? { w: midia.largura, h: midia.altura } : await medirImagemUrl(midia.url);
      const t = caberEm(dim, 1100, 760);
      inserirElemento(novoElemento('imagem', { src: midia.url, midia: midia.id, w: t.w, h: t.h, alt: (midia.nome || '').replace(/\.[a-z0-9]+$/i, '') }), 'Inserir imagem');
    }
  }

  function abrirSeletorTabela(ancora) {
    abrirPopover(ancora, (pop, fechar) => {
      const MAX_L = 8;
      const MAX_C = 8;
      const legenda = h('div', { class: 'ed-rotulo', text: '3 × 3', style: { textAlign: 'center' } });
      const grade = h('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(' + MAX_C + ', 22px)', gap: '3px', padding: '4px' }, attrs: { role: 'grid', 'aria-label': 'Tamanho da tabela' } });
      function marcar(l, c) {
        legenda.textContent = l + ' linhas × ' + c + ' colunas';
        $$('button', grade).forEach((b) => {
          const on = Number(b.dataset.l) <= l && Number(b.dataset.c) <= c;
          b.style.background = on ? 'var(--ed-acento)' : 'var(--ed-painel-3)';
        });
      }
      for (let l = 1; l <= MAX_L; l += 1) {
        for (let c = 1; c <= MAX_C; c += 1) {
          grade.appendChild(h('button', {
            type: 'button', dataset: { l, c }, attrs: { 'aria-label': l + ' linhas e ' + c + ' colunas' },
            style: { width: '22px', height: '22px', border: '0', borderRadius: '4px', cursor: 'pointer', background: 'var(--ed-painel-3)' },
            on: {
              mouseenter: () => marcar(l, c), focus: () => marcar(l, c),
              click: () => {
                fechar();
                const linhas = [];
                for (let i = 0; i < l; i += 1) { const linha = []; for (let j = 0; j < c; j += 1) linha.push(i === 0 ? 'Coluna ' + (j + 1) : ''); linhas.push(linha); }
                inserirElemento(novoElemento('tabela', { linhas, w: Math.min(1700, 260 * c + 100), h: Math.min(900, 90 * l) }), 'Inserir tabela');
              },
            },
          }));
        }
      }
      setTimeout(() => marcar(3, 3), 0);
      return h('div', { style: { padding: '6px' } }, h('div', { class: 'ed-menu-titulo', style: { padding: '0 0 6px' }, text: 'Inserir tabela' }), grade, legenda);
    }, { rotulo: 'Inserir tabela' });
  }

  function inserirAreaConteudo() {
    inserirElemento(novoElemento('forma', {
      forma: 'retangulo', slot: 'area_conteudo', w: 1500, h: 600, preenchimento: 'rgba(139,61,255,.08)',
      borda: { cor: '#8B3DFF', largura: 3, estilo: 'dashed' }, raio: 12,
    }), 'Inserir área de conteúdo');
  }
  function inserirApagar() {
    inserirElemento(novoElemento('apagar', { w: 600, h: 300 }), 'Marcar área para apagar');
  }

  // =================================================================== C. painel de propriedades

  const ICONE_TIPO = { texto: 'fa-solid fa-font', imagem: 'fa-regular fa-image', forma: 'fa-solid fa-shapes', icone: 'fa-regular fa-face-smile', video: 'fa-solid fa-film', tabela: 'fa-solid fa-table', apagar: 'fa-solid fa-eraser' };
  const NOME_TIPO = { texto: 'Texto', imagem: 'Imagem', forma: 'Forma', icone: 'Ícone', video: 'Vídeo', tabela: 'Tabela', apagar: 'Área para apagar' };

  function secao(id, titulo, conteudo, abertaPadrao) {
    E.secoesAbertas = E.secoesAbertas || {};
    const aberta = E.secoesAbertas[id] == null ? abertaPadrao !== false : E.secoesAbertas[id];
    const d = h('details', { class: 'ed-secao', open: aberta, dataset: { secao: id } },
      h('summary', { text: titulo }), h('div', { class: 'ed-secao-corpo' }, conteudo));
    d.addEventListener('toggle', () => {
      E.secoesAbertas[id] = d.open;
      if (id === 'animacao') { E.mostrarOrdemAnim = d.open; desenharSobreposicao(); }
    });
    return d;
  }

  function cabecalhoPainel(iconeClasse, titulo, extra) {
    return h('div', { class: 'ed-props-cabeca' }, h('span', { class: 'ed-tipo-icone', attrs: { 'aria-hidden': 'true' } }, icone(iconeClasse)), h('h2', { text: titulo }), extra || null);
  }

  function renderizarPainel() {
    if (!UI.props || !E.doc || E.movel) return;
    if (E.painelIA) { renderizarPainelIA(); return; }
    const corpoAntigo = $('.ed-props-corpo', UI.props);
    const alvo = E.slideId + '|' + E.selecao.join(',');
    const rolagem = corpoAntigo && E.painelAlvo === alvo ? corpoAntigo.scrollTop : 0;
    E.painelAlvo = alvo;
    UI.props.textContent = '';
    UI.camposGeo = null;
    const s = slideAtual();
    const sel = selecionados();
    E.mostrarOrdemAnim = false;
    let corpo;
    if (!s) {
      UI.props.appendChild(cabecalhoPainel('fa-regular fa-file', 'Nada aberto'));
      corpo = h('div', { class: 'ed-props-corpo' }, h('div', { class: 'ed-secao-corpo', style: { paddingTop: '16px' } }, h('p', { class: 'ed-dica', text: 'Crie um slide para começar.' })));
    } else if (!sel.length) {
      corpo = painelDoSlide(s);
    } else if (sel.length === 1) {
      corpo = painelDoElemento(sel[0]);
    } else {
      corpo = painelMultiplo(sel);
    }
    UI.props.appendChild(corpo);
    if (rolagem) corpo.scrollTop = rolagem;
    const animAberta = $('details[data-secao="animacao"]', UI.props);
    E.mostrarOrdemAnim = !!(animAberta && animAberta.open);
    desenharSobreposicao();
  }

  function atualizarCamposGeometria() {
    const c = UI.camposGeo;
    if (!c) return;
    const el = elementoPorId(c.id);
    if (!el) return;
    c.x.definir(Math.round(el.x));
    c.y.definir(Math.round(el.y));
    c.w.definir(Math.round(el.w));
    c.h.definir(Math.round(el.h));
    c.r.definir(Math.round(el.rotacao || 0));
  }

  function acoesRapidas(sel) {
    const bloqueado = sel.every((el) => el.bloqueado);
    return h('div', { class: 'ed-acoes-rapidas', attrs: { role: 'toolbar', 'aria-label': 'Ações do elemento' } },
      botaoIcone('fa-solid fa-clone', 'Duplicar (Ctrl+D)', duplicarSelecao),
      botaoIcone('fa-regular fa-copy', 'Copiar (Ctrl+C)', () => copiarSelecao(false)),
      botaoIcone('fa-solid fa-arrow-up', 'Trazer para frente (Ctrl+])', () => ordenar('frente')),
      botaoIcone('fa-solid fa-arrow-down', 'Enviar para trás (Ctrl+[)', () => ordenar('tras')),
      botaoIcone(bloqueado ? 'fa-solid fa-lock' : 'fa-solid fa-lock-open', bloqueado ? 'Desbloquear' : 'Bloquear', alternarBloqueio, { attrs: { 'aria-pressed': bloqueado ? 'true' : 'false' } }),
      h('span', { class: 'ed-espaco' }),
      botaoIcone('fa-regular fa-trash-can', 'Excluir (Delete)', excluirSelecao, { class: 'ed-btn-icone ed-btn-perigo' }));
  }

  function linhaAlinhar(distribuir_) {
    const b = (chave, dica, fn) => h('button', { class: 'ed-btn-icone', type: 'button', dica, on: { click: fn } }, svgIcone(ICONES_ALINHAR[chave]));
    return h('div', { class: 'ed-linha', style: { flexWrap: 'wrap', gap: '2px' } },
      b('esquerda', 'Alinhar à esquerda', () => alinhar('esquerda')), b('centro', 'Centralizar na horizontal', () => alinhar('centro')), b('direita', 'Alinhar à direita', () => alinhar('direita')),
      b('topo', 'Alinhar ao topo', () => alinhar('topo')), b('meio', 'Centralizar na vertical', () => alinhar('meio')), b('base', 'Alinhar à base', () => alinhar('base')),
      distribuir_ ? b('distH', 'Distribuir na horizontal', () => distribuir('h')) : null,
      distribuir_ ? b('distV', 'Distribuir na vertical', () => distribuir('v')) : null);
  }

  /** Atalho para os controles: altera o elemento pelo id atual (o objeto pode ter sido trocado pelo desfazer). */
  function alterador(el, rotulo, opcoes) {
    return (fn, chaveExtra, final) => {
      const atual = elementoPorId(el.id);
      if (!atual) return;
      mudarElemento(atual, fn, rotulo, Object.assign({ chave: 'campo-' + rotulo + '-' + el.id + (chaveExtra || '') }, opcoes || {}));
    };
  }

  function secaoGeometria(el) {
    const mudar = (campoNome) => alterador(el, 'Posição e tamanho · ' + campoNome);
    const x = campoNumero({ valor: Math.round(el.x), curto: 'X', rotuloAcessivel: 'Posição X', aoMudar: (v) => mudar('x')((e) => { e.x = v; }) });
    const y = campoNumero({ valor: Math.round(el.y), curto: 'Y', rotuloAcessivel: 'Posição Y', aoMudar: (v) => mudar('y')((e) => { e.y = v; }) });
    const w = campoNumero({ valor: Math.round(el.w), curto: 'L', min: 1, rotuloAcessivel: 'Largura', aoMudar: (v) => mudar('w')((e) => { e.w = v; }) });
    const hh = campoNumero({ valor: Math.round(el.h), curto: 'A', min: 1, rotuloAcessivel: 'Altura', aoMudar: (v) => mudar('h')((e) => { e.h = v; }) });
    const r = campoNumero({ valor: Math.round(el.rotacao || 0), curto: '↻', unidade: '°', min: -360, max: 360, rotuloAcessivel: 'Rotação em graus', aoMudar: (v) => mudar('rotacao')((e) => { e.rotacao = v; }) });
    UI.camposGeo = { id: el.id, x, y, w, h: hh, r };
    return secao('geometria', 'Posição e tamanho', [
      h('div', { class: 'ed-grade' }, x, y, w, hh),
      h('div', { class: 'ed-grade' }, r, h('div')),
      campoFaixa({ rotulo: 'Opacidade', valor: num(el.opacidade, 1), fator: 100, min: 0, max: 100, unidade: '%', aoMudar: (v) => mudar('opacidade')((e) => { e.opacidade = v; }) }),
      h('div', { class: 'ed-campo' }, h('label', { text: 'Alinhar ao slide' }), linhaAlinhar(false)),
    ], true);
  }

  function secaoAnimacao(els) {
    const el = els[0];
    const a = Object.assign(ANIM_VAZIA(), el.animacao || {});
    const mudar = (rotulo, fn) => {
      const atuais = els.map((x) => elementoPorId(x.id)).filter(Boolean);
      mudarElemento(atuais, (x) => { x.animacao = Object.assign(ANIM_VAZIA(), x.animacao || {}); fn(x.animacao); }, 'Animação · ' + rotulo, { chave: 'anim-' + rotulo + '-' + els.map((x) => x.id).join(','), ajustar: false });
    };
    const tem = a.tipo && a.tipo !== 'nenhuma';
    const corpo = [
      campoSelect({ rotulo: 'Entrada', valor: a.tipo, opcoes: R.ANIMACOES, aoMudar: (v) => { mudar('tipo', (an) => { an.tipo = v; }); renderizarPainel(); if (v !== 'nenhuma' && els.length === 1) visualizarAnimacao(elementoPorId(el.id)); } }),
    ];
    if (tem) {
      corpo.push(
        campoSelect({ rotulo: 'Quando entra', valor: a.gatilho, opcoes: R.GATILHOS, aoMudar: (v) => { mudar('gatilho', (an) => { an.gatilho = v; }); desenharSobreposicao(); } }),
        campoFaixa({ rotulo: 'Duração', valor: a.tipo === 'aparecer' ? 0 : a.duracao, min: 0, max: 3000, passo: 50, unidade: 'ms', aoMudar: (v) => mudar('duracao', (an) => { an.duracao = v; }) }),
        campoFaixa({ rotulo: 'Atraso', valor: a.atraso, min: 0, max: 5000, passo: 50, unidade: 'ms', aoMudar: (v) => mudar('atraso', (an) => { an.atraso = v; }) }),
        els.length === 1 ? botao('Visualizar', 'fa-solid fa-play', () => visualizarAnimacao(elementoPorId(el.id)), 'ed-btn-bloco') : null,
        h('p', { class: 'ed-dica', text: 'Os números rosa no slide mostram a ordem de entrada; ▸ indica que espera um clique.' }));
    }
    return secao('animacao', 'Animação', corpo, false);
  }

  function secaoLink(el) {
    const mudar = alterador(el, 'Link', { ajustar: false });
    const erro = h('p', { class: 'ed-dica', style: { color: 'var(--ed-perigo)', display: 'none' }, text: 'Use http(s)://… ou #slide-N.' });
    const opcoesSlides = [['', 'Escolher slide…']].concat(E.doc.slides.map((s, i) => ['#slide-' + (i + 1), 'Slide ' + (i + 1) + (s.oculto ? ' (oculto)' : '')]));
    return secao('link', 'Link ao clicar', [
      campoTexto({
        rotulo: 'Endereço', valor: el.link, placeholder: 'https://… ou #slide-3',
        aoMudar: (v) => {
          const ok = !v.trim() || R.linkValido(v);
          erro.style.display = ok ? 'none' : '';
          if (ok) mudar((e) => { e.link = v.trim(); });
        },
      }),
      erro,
      campoSelect({ rotulo: 'Ou ir para um slide', valor: /^#slide-\d+$/.test(el.link) ? el.link : '', opcoes: opcoesSlides, aoMudar: (v) => { if (v) { mudar((e) => { e.link = v; }); renderizarPainel(); } } }),
      h('p', { class: 'ed-dica', text: 'Funciona no modo apresentação.' }),
    ], false);
  }

  function secaoSlot(el) {
    return campoSelect({ rotulo: 'Slot (papel no template)', valor: el.slot || '', opcoes: R.SLOTS, aoMudar: (v) => { alterador(el, 'Slot', { ajustar: false })((e) => { e.slot = v; }); renderizarPainel(); } });
  }

  function painelDoElemento(el) {
    const frag = [];
    UI.props.appendChild(cabecalhoPainel(ICONE_TIPO[el.tipo] || 'fa-solid fa-square', NOME_TIPO[el.tipo] || el.tipo,
      el.bloqueado ? h('span', { class: 'ed-chip', text: 'Bloqueado' }) : null));
    UI.props.appendChild(acoesRapidas([el]));
    if (MODO_TEMPLATE && el.tipo !== 'apagar') frag.push(h('div', { class: 'ed-secao' }, h('div', { class: 'ed-secao-corpo', style: { paddingTop: '14px' } }, secaoSlot(el))));
    const especificas = { texto: secoesTexto, imagem: secoesImagem, forma: secoesForma, icone: secoesIcone, video: secoesVideo, tabela: secoesTabela, apagar: secoesApagar }[el.tipo];
    if (especificas) frag.push.apply(frag, especificas(el));
    frag.push(secaoGeometria(el));
    if (el.tipo !== 'apagar' && el.slot !== 'area_conteudo') {
      frag.push(secaoAnimacao([el]));
      frag.push(secaoLink(el));
    }
    return h('div', { class: 'ed-props-corpo' }, frag);
  }

  function painelMultiplo(sel) {
    UI.props.appendChild(cabecalhoPainel('fa-solid fa-object-group', sel.length + ' elementos'));
    UI.props.appendChild(acoesRapidas(sel));
    const mudarOpac = (v) => mudarElemento(sel.map((x) => elementoPorId(x.id)).filter(Boolean), (x) => { x.opacidade = v; }, 'Opacidade', { chave: 'opac-multi', ajustar: false });
    return h('div', { class: 'ed-props-corpo' },
      secao('alinhar', 'Alinhar e distribuir', [linhaAlinhar(true), h('p', { class: 'ed-dica', text: 'Alinha pela caixa que envolve a seleção. Distribuir precisa de 3 ou mais.' })], true),
      secao('opacidade', 'Aparência', [campoFaixa({ rotulo: 'Opacidade', valor: num(sel[0].opacidade, 1), fator: 100, min: 0, max: 100, unidade: '%', aoMudar: mudarOpac })], true),
      secaoAnimacao(sel.filter((x) => x.tipo !== 'apagar')));
  }

  const PESOS = [[100, 'Fino (100)'], [200, 'Extraleve (200)'], [300, 'Leve (300)'], [400, 'Normal (400)'], [500, 'Médio (500)'], [600, 'Seminegrito (600)'], [700, 'Negrito (700)'], [800, 'Extranegrito (800)'], [900, 'Black (900)']];

  function secoesTexto(el) {
    const est = el.estilo;
    const mudar = (rotulo, fn, extra) => alterador(el, rotulo, extra)((e) => fn(e.estilo, e));
    const familiaTema = est.papel === 'titulo' ? E.doc.tema.fonte_titulo : E.doc.tema.fonte_texto;
    const pesosFonte = (R.infoFonte(est.fonte || familiaTema) || {}).pesos;
    const opcoesPeso = PESOS.filter((p) => !pesosFonte || !pesosFonte.length || pesosFonte.indexOf(p[0]) >= 0 || p[0] === est.peso);
    const texto = [
      botao('Editar texto no slide', 'fa-solid fa-i-cursor', () => editarTexto(el.id), 'ed-btn-bloco', { dica: 'Enter ou duplo clique também editam' }),
      campoFonte({
        rotulo: 'Fonte', valor: est.fonte, permitirTema: true, familiaTema,
        aoMudar: (v) => garantirFonte(v || familiaTema, est.peso).then(() => { mudar('Fonte', (s) => { s.fonte = v; }); renderizarPainel(); }),
      }),
      campoSegmentado({ rotulo: 'Fonte do tema usada', valor: est.papel, opcoes: [['titulo', 'Títulos'], ['texto', 'Textos']], aoMudar: (v) => { mudar('Papel do texto', (s) => { s.papel = v; }); renderizarPainel(); } }),
      h('div', { class: 'ed-grade' },
        campoNumero({ rotulo: 'Tamanho', valor: est.tamanho, min: 4, max: 600, unidade: 'px', aoMudar: (v) => { E.tamanhoBase[el.id] = v; mudar('Tamanho da fonte', (s) => { s.tamanho = v; }, { ajustar: false }); } }),
        campoSelect({ rotulo: 'Peso', valor: est.peso, opcoes: opcoesPeso, aoMudar: (v) => garantirFonte(est.fonte || familiaTema, v).then(() => mudar('Peso da fonte', (s) => { s.peso = Number(v); })) })),
      campoSegmentado({
        rotulo: 'Estilo', multi: true, valor: { italico: est.italico, sublinhado: est.sublinhado, tachado: est.tachado, maiusculas: est.maiusculas },
        opcoes: [['italico', null, 'fa-solid fa-italic', 'Itálico'], ['sublinhado', null, 'fa-solid fa-underline', 'Sublinhado'], ['tachado', null, 'fa-solid fa-strikethrough', 'Tachado'], ['maiusculas', null, h('b', { text: 'AA', style: { fontSize: '11px' } }), 'Tudo em maiúsculas']],
        aoMudar: (chave, ativo) => mudar('Estilo do texto', (s) => { s[chave] = ativo; }),
      }),
      campoCor({ rotulo: 'Cor', valor: est.cor, aoMudar: (v) => mudar('Cor do texto', (s) => { s.cor = v || 'tema:texto'; }, { ajustar: false }) }),
      campoSegmentado({
        rotulo: 'Alinhamento', valor: est.alinhamento,
        opcoes: [['left', null, 'fa-solid fa-align-left', 'À esquerda'], ['center', null, 'fa-solid fa-align-center', 'Centralizado'], ['right', null, 'fa-solid fa-align-right', 'À direita'], ['justify', null, 'fa-solid fa-align-justify', 'Justificado']],
        aoMudar: (v) => mudar('Alinhamento', (s) => { s.alinhamento = v; }),
      }),
      campoSegmentado({
        rotulo: 'Posição vertical', valor: est.vertical,
        opcoes: [['top', 'Topo', null, 'No topo da caixa'], ['middle', 'Meio', null, 'No meio da caixa'], ['bottom', 'Base', null, 'Na base da caixa']],
        aoMudar: (v) => mudar('Posição vertical', (s) => { s.vertical = v; }, { ajustar: false }),
      }),
      h('div', { class: 'ed-grade' },
        campoNumero({ rotulo: 'Entrelinha', valor: est.entrelinha, min: 0.5, max: 4, passo: 0.05, casas: 2, aoMudar: (v) => mudar('Entrelinha', (s) => { s.entrelinha = v; }) }),
        campoNumero({ rotulo: 'Espaçamento', valor: est.espacamento, min: -50, max: 200, passo: 0.5, casas: 1, unidade: 'px', aoMudar: (v) => mudar('Espaçamento entre letras', (s) => { s.espacamento = v; }) })),
      campoAlternar({
        rotulo: 'Autoajuste (diminui a fonte para caber)', valor: el.autoajuste, dica: 'Reduz o tamanho quando o texto não cabe na caixa',
        aoMudar: (v) => { alterador(el, 'Autoajuste')((e) => { e.autoajuste = v; if (v) E.tamanhoBase[e.id] = Math.max(E.tamanhoBase[e.id] || 0, e.estilo.tamanho); }); renderizarPainel(); },
      }),
    ];
    const caixa = [
      campoCor({ rotulo: 'Fundo da caixa', valor: est.fundo, permitirVazio: true, aoMudar: (v) => mudar('Fundo do texto', (s) => { s.fundo = v; }, { ajustar: false }) }),
      campoBorda(est.borda, (parcial) => mudar('Borda do texto', (s) => { s.borda = Object.assign(BORDA_VAZIA(), s.borda, parcial); })),
      h('div', { class: 'ed-grade' },
        campoNumero({ rotulo: 'Arredondar', valor: est.raio, min: 0, max: 2000, unidade: 'px', aoMudar: (v) => mudar('Raio do texto', (s) => { s.raio = v; }, { ajustar: false }) }),
        campoNumero({ rotulo: 'Margem interna', valor: est.preenchimento, min: 0, max: 400, unidade: 'px', aoMudar: (v) => mudar('Margem interna', (s) => { s.preenchimento = v; }) })),
      campoSombra(est.sombra, (v) => mudar('Sombra do texto', (s) => { s.sombra = v; }, { ajustar: false })),
    ];
    return [secao('texto', 'Texto', texto, true), secao('caixa-texto', 'Caixa do texto', caixa, false)];
  }

  function secoesImagem(el) {
    const mudar = (rotulo, fn, extra) => alterador(el, rotulo, Object.assign({ ajustar: false }, extra || {}))(fn);
    const previa = h('div', { style: { position: 'relative', borderRadius: '10px', overflow: 'hidden', background: 'var(--ed-painel-3)', aspectRatio: '16 / 10', cursor: el.ajuste === 'cover' ? 'crosshair' : 'default' }, dica: el.ajuste === 'cover' ? 'Clique para escolher o ponto de foco' : null });
    const src = R.resolverSrc(el.src);
    if (src) {
      previa.appendChild(h('img', { src, alt: '', style: { width: '100%', height: '100%', objectFit: 'contain', display: 'block' } }));
      if (el.ajuste === 'cover') {
        const ponto = h('span', { style: { position: 'absolute', width: '18px', height: '18px', margin: '-9px 0 0 -9px', borderRadius: '50%', border: '3px solid #fff', boxShadow: '0 0 0 2px rgba(0,0,0,.5)', left: num(el.foco.x, 0.5) * 100 + '%', top: num(el.foco.y, 0.5) * 100 + '%', pointerEvents: 'none' } });
        previa.appendChild(ponto);
        previa.addEventListener('click', (ev) => {
          // A prévia mostra a imagem inteira (contain): converte o clique para a área da imagem.
          const img = $('img', previa);
          const r = previa.getBoundingClientRect();
          const nw = img.naturalWidth || 1;
          const nh = img.naturalHeight || 1;
          const f = Math.min(r.width / nw, r.height / nh);
          const iw = nw * f;
          const ih = nh * f;
          const fx = limitar((ev.clientX - r.left - (r.width - iw) / 2) / iw, 0, 1);
          const fy = limitar((ev.clientY - r.top - (r.height - ih) / 2) / ih, 0, 1);
          ponto.style.left = ((r.width - iw) / 2 + fx * iw) / r.width * 100 + '%';
          ponto.style.top = ((r.height - ih) / 2 + fy * ih) / r.height * 100 + '%';
          mudar('Foco da imagem', (e) => { e.foco = { x: Math.round(fx * 1000) / 1000, y: Math.round(fy * 1000) / 1000 }; });
        });
      }
    } else {
      previa.appendChild(h('div', { class: 'apres-vazio', style: { fontSize: '13px' } }, icone('fa-regular fa-image'), h('span', { text: 'Sem imagem' })));
    }
    const botoes = h('div', { class: 'ed-grade' },
      botao('Trocar', 'fa-solid fa-arrow-right-arrow-left', (ev) => trocarMidiaDoElemento(el, ev.currentTarget), 'ed-btn-p'),
      iaDisponivel() ? botao('Gerar com IA', 'fa-solid fa-wand-magic-sparkles', () => abrirPainelIA({ foco: 'imagem' }), 'ed-btn-p') : h('span'));
    return [
      secao('imagem', 'Imagem', [
        previa, botoes,
        campoSegmentado({ rotulo: 'Ajuste', valor: el.ajuste, opcoes: [['cover', 'Preencher', null, 'Preenche a caixa cortando as sobras'], ['contain', 'Caber', null, 'Mostra a imagem inteira'], ['fill', 'Esticar', null, 'Estica para ocupar a caixa']], aoMudar: (v) => { mudar('Ajuste da imagem', (e) => { e.ajuste = v; }); renderizarPainel(); } }),
        el.ajuste === 'cover' ? h('div', { class: 'ed-grade' },
          campoNumero({ rotulo: 'Foco X', valor: Math.round(num(el.foco.x, 0.5) * 100), min: 0, max: 100, unidade: '%', aoMudar: (v) => mudar('Foco da imagem', (e) => { e.foco = Object.assign({}, e.foco, { x: v / 100 }); }) }),
          campoNumero({ rotulo: 'Foco Y', valor: Math.round(num(el.foco.y, 0.5) * 100), min: 0, max: 100, unidade: '%', aoMudar: (v) => mudar('Foco da imagem', (e) => { e.foco = Object.assign({}, e.foco, { y: v / 100 }); }) })) : null,
        campoSelect({ rotulo: 'Filtro', valor: el.filtro, opcoes: [['', 'Nenhum'], ['cinza', 'Preto e branco'], ['escurecer', 'Escurecer'], ['desfocar', 'Desfocar']], aoMudar: (v) => mudar('Filtro da imagem', (e) => { e.filtro = v; }) }),
        campoTexto({ rotulo: 'Texto alternativo', valor: el.alt, placeholder: 'Descreva a imagem', max: 300, aoMudar: (v) => mudar('Texto alternativo', (e) => { e.alt = v; }) }),
      ], true),
      secao('estilo-imagem', 'Borda e sombra', [
        campoNumero({ rotulo: 'Arredondar cantos', valor: el.raio, min: 0, max: 5000, unidade: 'px', aoMudar: (v) => mudar('Raio da imagem', (e) => { e.raio = v; }) }),
        campoBorda(el.borda, (parcial) => mudar('Borda da imagem', (e) => { e.borda = Object.assign(BORDA_VAZIA(), e.borda, parcial); })),
        campoSombra(el.sombra, (v) => mudar('Sombra da imagem', (e) => { e.sombra = v; })),
      ], false),
    ];
  }

  function secoesForma(el) {
    const mudar = (rotulo, fn) => alterador(el, rotulo, { ajustar: false })(fn);
    const tipos = h('div', { class: 'ed-grade-formas', style: { width: 'auto', padding: '0' } });
    R.FORMAS.forEach((f) => {
      const b = h('button', { type: 'button', dica: f[1], attrs: { 'aria-pressed': el.forma === f[0] ? 'true' : 'false' }, style: el.forma === f[0] ? { borderColor: 'var(--ed-acento)', color: 'var(--ed-acento)' } : null, on: { click: () => { mudar('Tipo de forma', (e) => { e.forma = f[0]; if (f[0] === 'linha' && !num(e.borda.largura, 0)) e.borda = { cor: e.preenchimento || 'tema:primaria', largura: 6, estilo: 'solid' }; }); renderizarPainel(); } } }, desenhoDeForma(f[0]), h('span', { text: f[1] }));
      tipos.appendChild(b);
    });
    const linha = el.forma === 'linha';
    return [
      secao('forma', 'Forma', [
        tipos,
        linha ? null : campoCor({ rotulo: 'Preenchimento', valor: el.preenchimento, permitirVazio: true, aoMudar: (v) => mudar('Preenchimento', (e) => { e.preenchimento = v; }) }),
        linha ? null : campoGradiente({ rotulo: 'Gradiente (tem prioridade sobre a cor)', valor: el.gradiente, aoMudar: (v) => mudar('Gradiente', (e) => { e.gradiente = v; }) }),
        campoBorda(el.borda, (parcial) => mudar(linha ? 'Linha' : 'Borda da forma', (e) => { e.borda = Object.assign(BORDA_VAZIA(), e.borda, parcial); })),
        ['retangulo', 'retangulo-arredondado', 'linha'].indexOf(el.forma) >= 0
          ? campoNumero({ rotulo: 'Arredondar cantos', valor: el.raio, min: 0, max: 5000, unidade: 'px', aoMudar: (v) => mudar('Raio da forma', (e) => { e.raio = v; }) }) : null,
        campoSombra(el.sombra, (v) => mudar('Sombra da forma', (e) => { e.sombra = v; })),
        linha ? h('p', { class: 'ed-dica', text: 'A espessura da linha é a largura da borda; gire para inclinar.' }) : null,
      ], true),
    ];
  }

  function secoesIcone(el) {
    const mudar = (rotulo, fn) => alterador(el, rotulo, { ajustar: false })(fn);
    const b = h('button', { class: 'ed-btn ed-btn-bloco', type: 'button', style: { height: '64px', justifyContent: 'flex-start', gap: '14px' }, attrs: { 'aria-haspopup': 'dialog' } },
      h('span', { style: { width: '44px', height: '44px', borderRadius: '10px', background: 'var(--ed-painel-3)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: '22px' } }, icone(R.classeIcone(el.icone))),
      h('span', { text: 'Trocar ícone' }));
    b.addEventListener('click', () => abrirSeletorIcone(b, (classe) => { mudar('Trocar ícone', (e) => { e.icone = classe; }); renderizarPainel(); }));
    return [secao('icone', 'Ícone', [
      b,
      campoCor({ rotulo: 'Cor do ícone', valor: el.cor, aoMudar: (v) => mudar('Cor do ícone', (e) => { e.cor = v || 'tema:destaque'; }) }),
      campoCor({ rotulo: 'Fundo', valor: el.fundo, permitirVazio: true, aoMudar: (v) => mudar('Fundo do ícone', (e) => { e.fundo = v; }) }),
      campoNumero({ rotulo: 'Arredondar fundo', valor: el.raio, min: 0, max: 5000, unidade: 'px', aoMudar: (v) => mudar('Raio do ícone', (e) => { e.raio = v; }) }),
      h('div', { class: 'ed-linha' }, botao('Fundo redondo', 'fa-regular fa-circle', () => { mudar('Fundo do ícone', (e) => { if (!e.fundo) e.fundo = '#FFFFFF1F'; e.raio = 5000; }); renderizarPainel(); }, 'ed-btn-p')),
    ], true)];
  }

  function secoesVideo(el) {
    const mudar = (rotulo, fn) => alterador(el, rotulo, { ajustar: false })(fn);
    const poster = R.resolverSrc(el.poster);
    return [
      secao('video', 'Vídeo', [
        h('div', { style: { borderRadius: '10px', overflow: 'hidden', background: '#000', aspectRatio: '16 / 9' } },
          R.resolverSrc(el.src) ? h('video', { src: R.resolverSrc(el.src), poster: poster || null, muted: true, controls: true, preload: 'metadata', style: { width: '100%', height: '100%', objectFit: 'contain', display: 'block' } }) : h('div', { class: 'apres-vazio', style: { position: 'static', height: '100%', fontSize: '13px' } }, icone('fa-solid fa-film'), h('span', { text: 'Sem vídeo' }))),
        h('div', { class: 'ed-grade' },
          botao('Trocar vídeo', 'fa-solid fa-arrow-right-arrow-left', (ev) => trocarMidiaDoElemento(el, ev.currentTarget), 'ed-btn-p'),
          iaDisponivel() ? botao('Gerar com IA', 'fa-solid fa-wand-magic-sparkles', () => abrirPainelIA({ foco: 'video' }), 'ed-btn-p') : h('span')),
        campoAlternar({ rotulo: 'Tocar sozinho ao abrir o slide', valor: el.autoplay, aoMudar: (v) => mudar('Reprodução automática', (e) => { e.autoplay = v; }) }),
        campoAlternar({ rotulo: 'Repetir sem parar', valor: el.loop, aoMudar: (v) => mudar('Repetir vídeo', (e) => { e.loop = v; }) }),
        campoAlternar({ rotulo: 'Sem som', valor: el.mudo, aoMudar: (v) => mudar('Som do vídeo', (e) => { e.mudo = v; }) }),
        campoAlternar({ rotulo: 'Mostrar controles', valor: el.controles, aoMudar: (v) => mudar('Controles do vídeo', (e) => { e.controles = v; }) }),
        campoSegmentado({ rotulo: 'Ajuste', valor: el.ajuste, opcoes: [['cover', 'Preencher'], ['contain', 'Caber'], ['fill', 'Esticar']], aoMudar: (v) => mudar('Ajuste do vídeo', (e) => { e.ajuste = v; }) }),
        campoNumero({ rotulo: 'Arredondar cantos', valor: el.raio, min: 0, max: 5000, unidade: 'px', aoMudar: (v) => mudar('Raio do vídeo', (e) => { e.raio = v; }) }),
      ], true),
      secao('poster', 'Pôster (capa do vídeo)', [
        poster ? h('img', { src: poster, alt: 'Pôster atual', style: { width: '100%', borderRadius: '8px', display: 'block' } }) : h('p', { class: 'ed-dica', text: 'Sem pôster: no PDF e nas miniaturas o vídeo aparece como um quadro escuro.' }),
        h('div', { class: 'ed-grade' },
          botao(poster ? 'Trocar pôster' : 'Escolher pôster', 'fa-regular fa-image', (ev) => abrirBiblioteca(ev.currentTarget, 'IMAGEM', (m) => { mudar('Pôster do vídeo', (e) => { e.poster = m.url; }); renderizarPainel(); }, { titulo: 'Pôster do vídeo' }), 'ed-btn-p'),
          poster ? botao('Remover', 'fa-solid fa-xmark', () => { mudar('Remover pôster', (e) => { e.poster = ''; }); renderizarPainel(); }, 'ed-btn-p ed-btn-perigo') : h('span')),
      ], false),
    ];
  }

  function secoesTabela(el) {
    const est = el.estilo;
    const mudar = (rotulo, fn, extra) => alterador(el, rotulo, Object.assign({ ajustar: false }, extra || {}))(fn);
    const nLinhas = el.linhas.length;
    const nCols = (el.linhas[0] || []).length;
    const estrutura = (rotulo, fn) => { mudar(rotulo, fn, { chave: null }); renderizarPainel(); };
    return [
      secao('tabela', 'Tabela', [
        h('p', { class: 'ed-dica', text: nLinhas + ' linhas × ' + nCols + ' colunas. Dê dois cliques numa célula para escrever; Tab passa para a próxima.' }),
        botao('Editar células', 'fa-solid fa-i-cursor', () => editarCelula(el.id, 0, 0), 'ed-btn-bloco'),
        h('div', { class: 'ed-grade' },
          botao('Linha', 'fa-solid fa-plus', () => estrutura('Adicionar linha', (e) => { e.linhas.push(e.linhas[0].map(() => '')); e.h += e.h / nLinhas; }), 'ed-btn-p', { dica: 'Adicionar linha no fim' }),
          botao('Linha', 'fa-solid fa-minus', () => { if (nLinhas > 1) estrutura('Remover linha', (e) => { e.linhas.pop(); e.h -= e.h / nLinhas; }); }, 'ed-btn-p', { dica: 'Remover a última linha', disabled: nLinhas <= 1 }),
          botao('Coluna', 'fa-solid fa-plus', () => estrutura('Adicionar coluna', (e) => { e.linhas.forEach((l, i) => l.push(i === 0 && e.cabecalho ? 'Coluna ' + (l.length + 1) : '')); e.w += e.w / nCols; }), 'ed-btn-p', { dica: 'Adicionar coluna no fim' }),
          botao('Coluna', 'fa-solid fa-minus', () => { if (nCols > 1) estrutura('Remover coluna', (e) => { e.linhas.forEach((l) => l.pop()); e.w -= e.w / nCols; }); }, 'ed-btn-p', { dica: 'Remover a última coluna', disabled: nCols <= 1 })),
        campoAlternar({ rotulo: 'Primeira linha é cabeçalho', valor: el.cabecalho, aoMudar: (v) => mudar('Cabeçalho da tabela', (e) => { e.cabecalho = v; }) }),
        campoFonte({ rotulo: 'Fonte', valor: est.fonte, permitirTema: true, familiaTema: E.doc.tema.fonte_texto, aoMudar: (v) => garantirFonte(v || E.doc.tema.fonte_texto, 400).then(() => { mudar('Fonte da tabela', (e) => { e.estilo.fonte = v; }); renderizarPainel(); }) }),
        h('div', { class: 'ed-grade' },
          campoNumero({ rotulo: 'Tamanho', valor: est.tamanho, min: 6, max: 200, unidade: 'px', aoMudar: (v) => mudar('Tamanho da tabela', (e) => { e.estilo.tamanho = v; }) }),
          campoNumero({ rotulo: 'Arredondar', valor: est.raio, min: 0, max: 200, unidade: 'px', aoMudar: (v) => mudar('Raio da tabela', (e) => { e.estilo.raio = v; }) })),
        campoSegmentado({ rotulo: 'Alinhamento', valor: est.alinhamento, opcoes: [['left', null, 'fa-solid fa-align-left', 'À esquerda'], ['center', null, 'fa-solid fa-align-center', 'Centralizado'], ['right', null, 'fa-solid fa-align-right', 'À direita']], aoMudar: (v) => mudar('Alinhamento da tabela', (e) => { e.estilo.alinhamento = v; }) }),
      ], true),
      secao('cores-tabela', 'Cores da tabela', [
        h('div', { class: 'ed-grade' },
          campoCor({ rotulo: 'Texto', valor: est.cor, aoMudar: (v) => mudar('Cor do texto da tabela', (e) => { e.estilo.cor = v || 'tema:texto'; }) }),
          campoCor({ rotulo: 'Bordas', valor: est.borda, permitirVazio: true, aoMudar: (v) => mudar('Bordas da tabela', (e) => { e.estilo.borda = v; }) }),
          campoCor({ rotulo: 'Fundo do cabeçalho', valor: est.fundo_cabecalho, permitirVazio: true, aoMudar: (v) => mudar('Fundo do cabeçalho', (e) => { e.estilo.fundo_cabecalho = v; }) }),
          campoCor({ rotulo: 'Texto do cabeçalho', valor: est.cor_cabecalho, aoMudar: (v) => mudar('Texto do cabeçalho', (e) => { e.estilo.cor_cabecalho = v; }) }),
          campoCor({ rotulo: 'Fundo das linhas', valor: est.fundo_linhas, permitirVazio: true, aoMudar: (v) => mudar('Fundo das linhas', (e) => { e.estilo.fundo_linhas = v; }) }),
          campoCor({ rotulo: 'Linhas alternadas', valor: est.fundo_alternado, permitirVazio: true, aoMudar: (v) => mudar('Linhas alternadas', (e) => { e.estilo.fundo_alternado = v; }) })),
      ], false),
    ];
  }

  function secoesApagar() {
    const s = slideAtual();
    return [secao('apagar', 'Apagar do fundo', [
      h('div', { class: 'ed-aviso-caixa' }, icone('fa-solid fa-eraser'), h('span', { text: 'Posicione o retângulo sobre o que deve sumir da imagem de fundo (textos antigos, logos). Pode marcar várias áreas.' })),
      botao('Limpar fundo agora', 'fa-solid fa-broom', () => limparFundo(), 'ed-btn-bloco ed-btn-primario', { disabled: !(s && s.fundo && s.fundo.imagem) }),
      !(s && s.fundo && s.fundo.imagem) ? h('p', { class: 'ed-dica', text: 'Este layout não tem imagem de fundo.' }) : null,
    ], true)];
  }

  function painelDoSlide(s) {
    const i = indiceDoSlide(s.id);
    const n = R.numeracao(E.doc, s.id);
    UI.props.appendChild(cabecalhoPainel(MODO_TEMPLATE ? 'fa-solid fa-table-cells-large' : 'fa-regular fa-file', (MODO_TEMPLATE ? 'Layout ' : 'Slide ') + (i + 1),
      s.oculto ? h('span', { class: 'ed-chip', text: 'Oculto' }) : (!MODO_TEMPLATE ? h('span', { class: 'ed-contador', text: n.n + ' de ' + n.total, dica: 'Número na apresentação (slides ocultos não contam)' }) : null)));
    const mudar = (rotulo, fn, extra) => mudarSlide(fn, rotulo, Object.assign({ chave: 'slide-' + rotulo + '-' + s.id }, extra || {}));
    const frag = [];
    if (MODO_TEMPLATE) {
      const apagar = s.elementos.filter((el) => el.tipo === 'apagar').length;
      frag.push(secao('layout', 'Layout', [
        campoSelect({ rotulo: 'Papel do layout', valor: s.layout, opcoes: R.PAPEIS, aoMudar: (v) => { mudar('Papel do layout', (x) => { x.layout = v; }); renderizarListaSlides(); } }),
        campoTexto({ rotulo: 'Nome do layout', valor: s.nome, placeholder: 'Ex.: Capa com foto', max: 120, aoMudar: (v) => { mudar('Nome do layout', (x) => { x.nome = v; }); agendarMiniaturas(); } }),
        h('p', { class: 'ed-dica', text: 'A IA escolhe o layout pelo papel e preenche os elementos pelo slot (Título, Texto, Paginação…).' }),
      ], true));
      frag.push(secao('limpar', 'Limpar fundo', [
        h('p', { class: 'ed-dica', text: apagar ? apagar + ' área(s) marcada(s) para apagar.' : 'Use "Apagar do fundo" na barra de inserir para marcar o que deve sumir da imagem.' }),
        botao('Limpar fundo', 'fa-solid fa-broom', () => limparFundo(), 'ed-btn-bloco' + (apagar ? ' ed-btn-primario' : ''), { disabled: !apagar || !s.fundo.imagem }),
      ], apagar > 0));
    }
    const fundoImg = R.resolverSrc(s.fundo.imagem);
    frag.push(secao('fundo', 'Fundo', [
      campoCor({ rotulo: 'Cor', valor: s.fundo.cor, aoMudar: (v) => mudar('Cor de fundo', (x) => { x.fundo.cor = v || 'tema:fundo'; }) }),
      campoGradiente({ rotulo: 'Gradiente por cima da cor', valor: s.fundo.gradiente, aoMudar: (v) => mudar('Gradiente de fundo', (x) => { x.fundo.gradiente = v; }) }),
      h('div', { class: 'ed-campo' },
        h('label', { text: 'Imagem' }),
        fundoImg ? h('img', { src: fundoImg, alt: 'Imagem de fundo atual', style: { width: '100%', aspectRatio: '16 / 9', objectFit: 'cover', borderRadius: '8px', display: 'block' } }) : null,
        h('div', { class: 'ed-grade' },
          botao(fundoImg ? 'Trocar' : 'Escolher imagem', 'fa-regular fa-image', (ev) => abrirBiblioteca(ev.currentTarget, 'IMAGEM', (m) => { mudar('Imagem de fundo', (x) => { x.fundo.imagem = m.url; }, { painel: true }); }, { titulo: 'Imagem de fundo' }), 'ed-btn-p'),
          fundoImg ? botao('Remover', 'fa-solid fa-xmark', () => mudar('Remover imagem de fundo', (x) => { x.fundo.imagem = ''; }, { painel: true }), 'ed-btn-p ed-btn-perigo') : h('span'))),
      fundoImg ? campoSegmentado({ rotulo: 'Ajuste da imagem', valor: s.fundo.ajuste, opcoes: [['cover', 'Preencher'], ['contain', 'Caber']], aoMudar: (v) => mudar('Ajuste do fundo', (x) => { x.fundo.ajuste = v; }) }) : null,
    ], true));
    frag.push(secao('transicao', 'Transição', [
      campoSelect({ rotulo: 'Ao entrar neste slide', valor: s.transicao.tipo, opcoes: R.TRANSICOES, aoMudar: (v) => { mudar('Transição', (x) => { x.transicao.tipo = v; }); renderizarPainel(); if (v !== 'nenhuma') visualizarTransicao(); } }),
      s.transicao.tipo !== 'nenhuma' ? campoFaixa({ rotulo: 'Duração', valor: s.transicao.duracao, min: 100, max: 3000, passo: 50, unidade: 'ms', aoMudar: (v) => mudar('Duração da transição', (x) => { x.transicao.duracao = v; }) }) : null,
      h('div', { class: 'ed-grade' },
        botao('Visualizar', 'fa-solid fa-play', () => visualizarTransicao(), 'ed-btn-p', { disabled: s.transicao.tipo === 'nenhuma' && !temAnimacoes(s), dica: 'Transição e animações deste slide' }),
        botao('Aplicar a todos', 'fa-solid fa-layer-group', () => {
          const t = clonar(slideAtual().transicao);
          E.doc.slides.forEach((x) => { x.transicao = clonar(t); });
          commit('Transição em todos os slides');
          agendarMiniaturas();
          avisar('Transição aplicada a todos os slides.', { tipo: 'ok', acao: { texto: 'Desfazer', fn: desfazer } });
        }, 'ed-btn-p')),
    ], true));
    if (!MODO_TEMPLATE) {
      frag.push(secao('notas', 'Notas do apresentador', [
        campoTexto({ area: true, linhas: 5, valor: s.notas, placeholder: 'O que falar neste slide (aparece com a tecla N ao apresentar)', aoMudar: (v) => { mudar('Notas', (x) => { x.notas = v; }, { chave: 'notas-' + s.id }); } }),
      ], !!s.notas));
      const narracao = s.narracao && R.resolverSrc(s.narracao.url);
      frag.push(secao('narracao', 'Narração', [
        narracao ? h('audio', { src: narracao, controls: true, preload: 'none', style: { width: '100%' } }) : h('p', { class: 'ed-dica', text: iaDisponivel() ? 'Sem narração. Gere pelo Assistente IA (narração de todos os slides).' : 'Sem narração.' }),
        narracao ? h('p', { class: 'ed-dica', text: 'Duração: ' + formatarNumero(s.narracao.duracao || 0, 1) + ' s. Usada no vídeo narrado e em "apresentar com narração".' }) : null,
        narracao ? botao('Remover narração', 'fa-solid fa-xmark', () => mudar('Remover narração', (x) => { x.narracao = null; }, { painel: true }), 'ed-btn-p ed-btn-perigo') : null,
        !narracao && iaDisponivel() ? botao('Abrir Assistente IA', 'fa-solid fa-wand-magic-sparkles', () => abrirPainelIA({ foco: 'narracao' }), 'ed-btn-p') : null,
      ], !!narracao));
      frag.push(h('div', { class: 'ed-secao' }, h('div', { class: 'ed-secao-corpo', style: { paddingTop: '12px' } },
        campoAlternar({ rotulo: 'Ocultar na apresentação', valor: s.oculto, dica: 'Não aparece ao apresentar nem no PDF', aoMudar: () => alternarOculto(s.id) }))));
    }
    frag.push(secaoTema());
    frag.push(h('div', { class: 'ed-secao-corpo', style: { paddingTop: '14px' } }, h('p', { class: 'ed-dica' },
      'Clique num elemento para editar. ', h('kbd', { text: 'Shift' }), ' + clique seleciona vários; arraste no vazio para selecionar em área.')));
    return h('div', { class: 'ed-props-corpo' }, frag);
  }

  function temAnimacoes(s) {
    return (s.elementos || []).some((el) => el.animacao && el.animacao.tipo && el.animacao.tipo !== 'nenhuma');
  }

  function secaoTema() {
    const tema = E.doc.tema;
    const mudarTema = (rotulo, fn, chave) => {
      finalizarEdicoes();
      fn(tema);
      commit(rotulo, chave);
      renderizarQuadro();
      agendarMiniaturas();
    };
    const fonteTema = (qual, rotulo) => campoFonte({
      rotulo, valor: tema[qual],
      aoMudar: (v) => {
        if (!v) return;
        garantirFonte(v, qual === 'fonte_titulo' ? 800 : 400).then(() => {
          mudarTema('Fonte do tema', (t) => { t[qual] = v; });
          if (ajustarDocumentoInteiro()) { commit('Fonte do tema', 'autoajuste-tema'); renderizarQuadro(); }
          renderizarPainel();
        });
      },
    });
    const cores = h('div', { class: 'ed-tema-cores' });
    Object.keys(ROTULOS_TEMA).forEach((k) => {
      cores.appendChild(campoCor({
        rotulo: ROTULOS_TEMA[k], valor: tema.cores[k], semTema: true,
        aoMudar: (v, final) => { if (v) mudarTema('Cor do tema · ' + ROTULOS_TEMA[k], (t) => { t.cores[k] = v; }, 'tema-cor-' + k); },
      }));
    });
    return secao('tema', MODO_TEMPLATE ? 'Tema do template' : 'Tema da apresentação', [
      h('p', { class: 'ed-dica', text: 'Vale para todos os slides: o que usa as cores e fontes do tema muda junto.' }),
      fonteTema('fonte_titulo', 'Fonte dos títulos'),
      fonteTema('fonte_texto', 'Fonte dos textos'),
      cores,
    ], false);
  }

  // =================================================================== C. visualizar animação e transição no quadro

  async function visualizarAnimacao(el) {
    if (!el || E.previa) return;
    finalizarEdicoes();
    const nodo = nodoDoElemento(el.id);
    if (!nodo || !el.animacao || el.animacao.tipo === 'nenhuma') return;
    E.previa = true;
    desenharSobreposicao();
    const anim = nodo.querySelector('.apres-anim');
    anim.classList.add('apres-anim-pendente');
    await esperar(120);
    await R.animarEntrada(nodo, Object.assign({}, el.animacao, { atraso: Math.min(num(el.animacao.atraso, 0), 1500) }));
    E.previa = false;
    desenharSobreposicao();
  }

  async function visualizarTransicao() {
    const s = slideAtual();
    if (!s || E.previa) return;
    finalizarEdicoes();
    E.previa = true;
    desenharSobreposicao();
    const i = indiceDoSlide(s.id);
    const anterior = i > 0 ? E.doc.slides[i - 1] : null;
    UI.caixa.style.overflow = 'hidden';
    UI.escala.textContent = '';
    const deSlide = anterior ? R.criarSlide(anterior, E.doc, { modo: 'exportacao' }) : h('div', { class: 'apres-slide', style: { background: '#000' } });
    const paraSlide = R.criarSlide(s, E.doc, { modo: 'apresentacao' });
    paraSlide.querySelectorAll('video').forEach((v) => { v.muted = true; });
    [deSlide, paraSlide].forEach((n) => { n.style.position = 'absolute'; n.style.left = '0'; n.style.top = '0'; });
    UI.escala.append(deSlide, paraSlide);
    try {
      await R.aguardarMidias(paraSlide, 2500);
      await esperar(250);
      await R.transicionar(deSlide, paraSlide, s.transicao, 1);
      deSlide.remove();
      const plano = R.planoAnimacoes(s, { template: MODO_TEMPLATE });
      for (let k = 0; k < plano.length; k += 1) {
        if (k > 0) await esperar(500);
        await R.executarPasso(paraSlide, plano[k]);
      }
      await esperar(600);
    } finally {
      UI.caixa.style.overflow = '';
      E.previa = false;
      renderizarQuadro();
    }
  }

  // (fechamento provisório: faltam partes)
})();
