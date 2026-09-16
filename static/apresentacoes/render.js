/*
 * Assistente de Apresentações — renderização dos slides (window.APRES_RENDER).
 *
 * Um único desenho do slide serve o editor, as miniaturas, a apresentação e a
 * exportação (PDF e vídeo). Um caminho só é o que garante que o PDF saia igual
 * ao que a pessoa viu no editor.
 *
 * Segurança: o HTML dos textos passa SEMPRE pelo DOMPurify (lista curta de
 * tags e estilos); todas as outras strings entram por textContent ou por
 * propriedades de estilo já validadas. Mídia só da própria origem
 * (/apresentacoes/midia/<id>/) ou de arquivos estáticos do portal.
 */
(function () {
  'use strict';

  const LARGURA = 1920;
  const ALTURA = 1080;

  // ------------------------------------------------------------------ utilidades

  function cfg() { return window.APRES || {}; }
  function num(v, padrao) { const n = Number(v); return Number.isFinite(n) ? n : padrao; }
  function limitar(v, min, max) { return Math.min(max, Math.max(min, v)); }
  function esperar(ms) { return new Promise((r) => setTimeout(r, ms)); }
  function div(classe) {
    const d = document.createElement('div');
    if (classe) d.className = classe;
    return d;
  }
  function pad2(n) { return String(Math.max(0, Math.round(num(n, 0)))).padStart(2, '0'); }

  // ------------------------------------------------------------------ rótulos (pt-BR)

  const ANIMACOES = [
    ['nenhuma', 'Nenhuma'], ['aparecer', 'Aparecer'], ['fade', 'Esmaecer'], ['subir', 'Subir'],
    ['descer', 'Descer'], ['entrar-esquerda', 'Entrar pela esquerda'], ['entrar-direita', 'Entrar pela direita'],
    ['zoom', 'Zoom'], ['revelar', 'Revelar'],
  ];
  const GATILHOS = [
    ['auto', 'Ao abrir o slide (na sequência)'], ['clique', 'Ao clicar / avançar'], ['junto', 'Junto com o anterior'],
  ];
  const TRANSICOES = [
    ['nenhuma', 'Nenhuma'], ['fade', 'Esmaecer'], ['empurrar-esquerda', 'Empurrar para a esquerda'],
    ['empurrar-direita', 'Empurrar para a direita'], ['empurrar-cima', 'Empurrar para cima'],
    ['empurrar-baixo', 'Empurrar para baixo'], ['revelar', 'Revelar'], ['zoom', 'Zoom'], ['cobrir', 'Cobrir'],
  ];
  const FORMAS = [
    ['retangulo', 'Retângulo'], ['retangulo-arredondado', 'Retângulo arredondado'], ['pilula', 'Pílula'],
    ['circulo', 'Círculo'], ['linha', 'Linha'], ['seta', 'Seta'], ['triangulo', 'Triângulo'],
    ['estrela', 'Estrela'], ['hexagono', 'Hexágono'],
  ];
  const SLOTS = [
    ['', 'Livre (sem slot)'], ['rotulo', 'Rótulo'], ['titulo', 'Título'], ['titulo_destaque', 'Título em destaque'],
    ['subtitulo', 'Subtítulo'], ['texto', 'Texto'], ['botao', 'Botão'], ['paginacao', 'Paginação'],
    ['imagem', 'Imagem'], ['logo', 'Logo'], ['decoracao', 'Decoração'], ['area_conteudo', 'Área de conteúdo'],
  ];
  const PAPEIS = [
    ['capa', 'Capa'], ['secao', 'Seção'], ['conteudo', 'Conteúdo'], ['quadro', 'Quadro'],
    ['encerramento', 'Encerramento'], ['livre', 'Livre'],
  ];

  // ------------------------------------------------------------------ cores e mídia

  const RE_HEX = /^#(?:[0-9a-f]{3}|[0-9a-f]{4}|[0-9a-f]{6}|[0-9a-f]{8})$/i;
  const RE_RGB = /^rgba?\(\s*\d{1,3}(?:\.\d+)?%?\s*,\s*\d{1,3}(?:\.\d+)?%?\s*,\s*\d{1,3}(?:\.\d+)?%?\s*(?:,\s*(?:\d+|\d*\.\d+)%?\s*)?\)$/i;

  /** Cor do documento → cor CSS. Aceita `tema:<chave>` (resolvida no tema), hex, rgb(a) e transparent. */
  function resolverCor(cor, tema, profundidade) {
    if (typeof cor !== 'string') return '';
    const c = cor.trim();
    if (!c) return '';
    if (c.slice(0, 5).toLowerCase() === 'tema:') {
      const valor = ((tema && tema.cores) || {})[c.slice(5).trim()];
      // Um tema apontando para outro `tema:` é aceito, mas sem laço infinito.
      if (typeof valor !== 'string' || (profundidade || 0) > 2) return '';
      return resolverCor(valor, tema, (profundidade || 0) + 1);
    }
    if (c.toLowerCase() === 'transparent') return 'transparent';
    if (RE_HEX.test(c) || RE_RGB.test(c)) return c;
    return '';
  }

  /** Gradiente CSS (linear/radial/conic) com `tema:*` resolvido; sem url() nem nada que saia do CSS. */
  function resolverGradiente(grad, tema) {
    if (typeof grad !== 'string') return '';
    let g = grad.trim();
    if (!g || g.length > 600) return '';
    if (!/^(?:repeating-)?(?:linear|radial|conic)-gradient\(/i.test(g)) return '';
    if (/url\s*\(|expression|javascript:|[;{}<>\\]/i.test(g)) return '';
    g = g.replace(/tema:([a-z_]+)/gi, (m, chave) => resolverCor('tema:' + chave, tema) || 'transparent');
    if (window.CSS && CSS.supports && !CSS.supports('background-image', g)) return '';
    return g;
  }

  /** Fonte de mídia permitida → URL. Qualquer outra coisa vira '' (o servidor também descarta). */
  function resolverSrc(src) {
    if (typeof src !== 'string') return '';
    const s = src.trim();
    if (!s) return '';
    if (s.slice(0, 7) === 'static:') {
      const caminho = s.slice(7).replace(/^\/+/, '');
      if (!/^apresentacoes\/[A-Za-z0-9_\-./]+$/.test(caminho) || caminho.indexOf('..') >= 0) return '';
      let base = cfg().static_url || '/static/';
      if (base.slice(-1) !== '/') base += '/';
      return base + caminho;
    }
    if (/^\/apresentacoes\/midia\/\d+\/(?:\?[A-Za-z0-9=&_-]*)?$/.test(s)) return s;
    // blob: só existe nesta aba (prévia local enquanto um arquivo sobe).
    if (/^blob:/.test(s)) return s;
    return '';
  }

  // ------------------------------------------------------------------ saneamento do HTML dos textos

  const TAGS_PERMITIDAS = ['b', 'strong', 'i', 'em', 'u', 's', 'br', 'p', 'div', 'span', 'ul', 'ol', 'li', 'a'];
  const ESTILOS_SPAN = ['color', 'background-color', 'font-size', 'font-weight', 'font-style', 'text-decoration', 'font-family'];
  let purificador = null;
  let avisouSemPurify = false;

  function linkValido(href) {
    if (typeof href !== 'string') return false;
    const h = href.trim();
    return /^https?:\/\/[^\s"'<>]+$/i.test(h) || /^#slide-\d{1,4}$/.test(h);
  }

  function filtrarEstiloSpan(css) {
    if (typeof css !== 'string') return '';
    const saida = [];
    css.split(';').forEach((decl) => {
      const i = decl.indexOf(':');
      if (i < 0) return;
      let prop = decl.slice(0, i).trim().toLowerCase();
      const valor = decl.slice(i + 1).trim();
      // O Chrome grava sublinhado/tachado como text-decoration-line; a lista só conhece text-decoration.
      if (prop === 'text-decoration-line') prop = 'text-decoration';
      if (ESTILOS_SPAN.indexOf(prop) < 0 || !valor || valor.length > 200) return;
      if (/url\s*\(|expression|javascript:|[<>{}\\]|@import/i.test(valor)) return;
      saida.push(prop + ': ' + valor);
    });
    return saida.join('; ');
  }

  function obterPurificador() {
    if (purificador) return purificador;
    if (typeof window.DOMPurify !== 'function') return null;
    // Instância própria: os ganchos abaixo não vazam para outros usos do DOMPurify na página.
    const p = window.DOMPurify(window);
    p.addHook('uponSanitizeAttribute', (nodo, dados) => {
      const tag = String(nodo.nodeName || '').toLowerCase();
      if (dados.attrName === 'style') {
        const limpo = tag === 'span' ? filtrarEstiloSpan(dados.attrValue) : '';
        if (limpo) dados.attrValue = limpo;
        else dados.keepAttr = false;
      } else if (dados.attrName === 'href') {
        if (tag !== 'a' || !linkValido(dados.attrValue)) dados.keepAttr = false;
        else dados.attrValue = dados.attrValue.trim();
      }
    });
    purificador = p;
    return p;
  }

  /** HTML de texto saneado (string). Sem DOMPurify carregado, devolve o texto escapado. */
  function sanearHtml(html) {
    if (html == null) return '';
    const p = obterPurificador();
    if (!p) {
      if (!avisouSemPurify) { avisouSemPurify = true; console.warn('[apresentações] DOMPurify ausente: textos exibidos sem formatação.'); }
      return escaparHtml(textoPlano(html)).replace(/\n/g, '<br>');
    }
    return p.sanitize(String(html), {
      ALLOWED_TAGS: TAGS_PERMITIDAS, ALLOWED_ATTR: ['href', 'style'],
      ALLOW_DATA_ATTR: false, ALLOW_ARIA_ATTR: false, KEEP_CONTENT: true,
    });
  }

  function escaparHtml(t) {
    return String(t).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  /** Texto puro de um HTML (documento inerte do DOMParser: nada executa, nada carrega). */
  function textoPlano(html) {
    if (html == null) return '';
    const d = new DOMParser().parseFromString('<!doctype html><body>' + String(html), 'text/html');
    d.querySelectorAll('br').forEach((br) => br.replaceWith('\n'));
    d.querySelectorAll('p,div,li').forEach((b) => b.append('\n'));
    return (d.body.textContent || '').replace(/\n{3,}/g, '\n\n').trim();
  }

  /** Coloca HTML saneado no nó (único ponto que escreve innerHTML vindo do documento). */
  function preencherHtml(nodo, html) {
    if (obterPurificador()) nodo.innerHTML = sanearHtml(html);
    else nodo.textContent = textoPlano(html);
    nodo.querySelectorAll('a[href]').forEach((a) => {
      if (/^https?:/i.test(a.getAttribute('href'))) { a.target = '_blank'; a.rel = 'noopener noreferrer'; }
    });
  }

  // ------------------------------------------------------------------ fontes

  let catalogo = null;
  let promessaCatalogo = null;
  const CATEGORIA_RESERVA = { 'sans-serif': 'sans-serif', serif: 'serif', monospace: 'monospace', display: 'sans-serif', handwriting: 'cursive' };

  function carregarCatalogoFontes() {
    if (promessaCatalogo) return promessaCatalogo;
    const c = cfg();
    const url = c.fontes_url || ((c.static_url || '/static/') + 'apresentacoes/fontes.json');
    promessaCatalogo = fetch(url, { credentials: 'same-origin' })
      .then((r) => (r.ok ? r.json() : null))
      .catch(() => null)
      .then((dados) => {
        const google = dados && Array.isArray(dados.google) ? dados.google : [];
        const sistema = dados && Array.isArray(dados.sistema) ? dados.sistema : [];
        const mapa = new Map();
        sistema.forEach((f) => { if (f && f.familia) mapa.set(String(f.familia).toLowerCase(), Object.assign({ origem: 'sistema' }, f)); });
        google.forEach((f) => { if (f && f.familia) mapa.set(String(f.familia).toLowerCase(), Object.assign({ origem: 'google' }, f)); });
        catalogo = { google, sistema, mapa };
        return catalogo;
      });
    return promessaCatalogo;
  }

  function infoFonte(familia) {
    return catalogo ? catalogo.mapa.get(String(familia || '').trim().toLowerCase()) || null : null;
  }

  function nomeFonteLimpo(familia) {
    return String(familia || '').replace(/["'\\;{}<>]/g, '').trim();
  }

  function pilhaFonte(familia) {
    const f = nomeFonteLimpo(familia) || 'Montserrat';
    const info = infoFonte(f);
    const reserva = (info && CATEGORIA_RESERVA[info.categoria]) || 'sans-serif';
    return '"' + f + '", ' + reserva;
  }

  function familiaDoTexto(el, tema) {
    const est = (el && el.estilo) || {};
    const t = tema || {};
    return nomeFonteLimpo(est.fonte) || nomeFonteLimpo(est.papel === 'titulo' ? t.fonte_titulo : t.fonte_texto) || 'Montserrat';
  }

  /** URL do css2 do Google Fonts só com pesos que a família tem (peso inexistente devolve 400). */
  function urlGoogleFonts(info, texto) {
    const familia = encodeURIComponent(info.familia).replace(/%20/g, '+');
    let pesos = (Array.isArray(info.pesos) ? info.pesos : []).map(Number).filter((p) => p >= 1 && p <= 1000);
    pesos = Array.from(new Set(pesos)).sort((a, b) => a - b);
    let eixo = '';
    if (texto) {
      // Prévia do seletor: um peso só basta.
      const p = pesos.indexOf(400) >= 0 ? 400 : pesos[0];
      eixo = p ? ':wght@' + p : '';
    } else if (pesos.length && info.italico) {
      // fontes.json ainda não diz quais famílias têm itálico; se um dia disser, carregamos o eixo.
      eixo = ':ital,wght@' + pesos.map((p) => '0,' + p).concat(pesos.map((p) => '1,' + p)).join(';');
    } else if (pesos.length) {
      eixo = ':wght@' + pesos.join(';');
    }
    let url = 'https://fonts.googleapis.com/css2?family=' + familia + eixo + '&display=swap';
    if (texto) url += '&text=' + encodeURIComponent(texto);
    return url;
  }

  const linksFontes = new Map();

  /** Garante o <link> do Google Fonts da família (fontes do sistema não carregam nada). */
  function carregarFamilia(familia) {
    const nome = nomeFonteLimpo(familia);
    if (!nome) return Promise.resolve(false);
    const chave = nome.toLowerCase();
    if (linksFontes.has(chave)) return linksFontes.get(chave);
    const promessa = carregarCatalogoFontes().then(() => {
      const info = infoFonte(nome);
      if (!info || info.origem !== 'google') return false;
      const jaTem = Array.prototype.some.call(document.querySelectorAll('link[data-apres-fonte]'),
        (l) => String(l.getAttribute('data-apres-fonte') || '').toLowerCase() === chave);
      if (jaTem) return true;
      return new Promise((resolve) => {
        const link = document.createElement('link');
        link.rel = 'stylesheet';
        // crossorigin: sem ele as regras @font-face ficam ilegíveis e a exportação não embute a fonte.
        link.crossOrigin = 'anonymous';
        link.href = urlGoogleFonts(info);
        link.setAttribute('data-apres-fonte', info.familia);
        link.onload = () => resolve(true);
        link.onerror = () => resolve(false);
        document.head.appendChild(link);
      });
    });
    linksFontes.set(chave, promessa);
    return promessa;
  }

  /** Família → conjunto "peso|estilo" usados no documento (inclui fontes em <span style>). */
  function fontesUsadas(doc) {
    const usadas = new Map();
    const tema = (doc && doc.tema) || {};
    function add(familia, peso, italico) {
      const nome = nomeFonteLimpo(familia);
      if (!nome) return;
      if (!usadas.has(nome)) usadas.set(nome, new Set());
      usadas.get(nome).add(limitar(Math.round(num(peso, 400) / 100) * 100, 100, 900) + '|' + (italico ? 'italic' : 'normal'));
    }
    add(tema.fonte_titulo || 'Montserrat', 800);
    add(tema.fonte_texto || 'Montserrat', 400);
    let icones = false;
    ((doc && doc.slides) || []).forEach((s) => ((s && s.elementos) || []).forEach((el) => {
      if (!el) return;
      if (el.tipo === 'texto') {
        const est = el.estilo || {};
        const fam = familiaDoTexto(el, tema);
        add(fam, est.peso || 400, est.italico);
        const html = String(el.html || '');
        if (/<(b|strong)\b/i.test(html)) add(fam, 700, est.italico);
        if (/font-family/i.test(html)) {
          const t = document.createElement('template');
          t.innerHTML = sanearHtml(html);
          t.content.querySelectorAll('span[style]').forEach((sp) => {
            const f = (sp.style.fontFamily || '').split(',')[0];
            if (f) add(f, sp.style.fontWeight === 'bold' ? 700 : (sp.style.fontWeight || est.peso), sp.style.fontStyle === 'italic' || est.italico);
          });
        }
      } else if (el.tipo === 'tabela') {
        const fam = nomeFonteLimpo((el.estilo || {}).fonte) || tema.fonte_texto || 'Montserrat';
        add(fam, 400); add(fam, 700);
      } else if (el.tipo === 'icone') {
        icones = true;
      }
    }));
    return { familias: usadas, icones };
  }

  /** Carrega as fontes do documento e espera ficarem prontas (com limite de tempo). */
  async function carregarFontes(doc, opcoes) {
    const limite = (opcoes && opcoes.limite) || 10000;
    await carregarCatalogoFontes();
    const { familias, icones } = fontesUsadas(doc);
    await Promise.race([Promise.all(Array.from(familias.keys()).map(carregarFamilia)), esperar(limite)]);
    if (!document.fonts || !document.fonts.load) return;
    const cargas = [];
    familias.forEach((variantes, familia) => {
      if (!infoFonte(familia) || infoFonte(familia).origem !== 'google') return;
      variantes.forEach((v) => {
        const partes = v.split('|');
        cargas.push(document.fonts.load(partes[1] + ' ' + partes[0] + ' 40px "' + familia + '"').catch(() => []));
      });
    });
    if (icones) {
      cargas.push(document.fonts.load('900 40px "Font Awesome 6 Free"').catch(() => []));
      cargas.push(document.fonts.load('400 40px "Font Awesome 6 Free"').catch(() => []));
      cargas.push(document.fonts.load('400 40px "Font Awesome 6 Brands"').catch(() => []));
    }
    await Promise.race([Promise.all(cargas), esperar(limite)]);
    try { await Promise.race([document.fonts.ready, esperar(limite)]); } catch (e) { /* segue com o que tiver */ }
  }

  // ------------------------------------------------------------------ numeração

  function slidesVisiveis(doc) {
    return ((doc && doc.slides) || []).filter((s) => s && !s.oculto);
  }

  /** {n, total} contando só slides visíveis; um slide oculto recebe o número que teria. */
  function numeracao(doc, slideId) {
    let total = 0;
    let n = 0;
    ((doc && doc.slides) || []).forEach((s) => {
      if (!s) return;
      if (!s.oculto) total += 1;
      if (s.id === slideId) n = s.oculto ? total + 1 : total;
    });
    return { n, total };
  }

  function aplicarPaginacao(raiz, n, total) {
    const walker = document.createTreeWalker(raiz, NodeFilter.SHOW_TEXT);
    const textos = [];
    while (walker.nextNode()) textos.push(walker.currentNode);
    textos.forEach((t) => {
      if (/\{(?:n|total)\}/.test(t.nodeValue)) {
        t.nodeValue = t.nodeValue.replace(/\{n\}/g, pad2(n)).replace(/\{total\}/g, pad2(total));
      }
    });
  }

  // ------------------------------------------------------------------ sombras

  function sombraCaixa(tipo, tema) {
    const primaria = resolverCor('tema:primaria', tema) || '#E23FCF';
    if (tipo === 'suave') return '0 12px 32px rgba(0,0,0,.28)';
    if (tipo === 'forte') return '0 24px 64px rgba(0,0,0,.62)';
    if (tipo === 'brilho') return '0 0 42px ' + primaria + ', 0 0 14px ' + primaria;
    return '';
  }
  function sombraTexto(tipo, tema) {
    const primaria = resolverCor('tema:primaria', tema) || '#E23FCF';
    if (tipo === 'suave') return '0 4px 14px rgba(0,0,0,.35)';
    if (tipo === 'forte') return '0 8px 28px rgba(0,0,0,.75)';
    if (tipo === 'brilho') return '0 0 28px ' + primaria + ', 0 0 10px ' + primaria;
    return '';
  }
  function sombraFiltro(tipo, tema) {
    const primaria = resolverCor('tema:primaria', tema) || '#E23FCF';
    if (tipo === 'suave') return 'drop-shadow(0 12px 20px rgba(0,0,0,.3))';
    if (tipo === 'forte') return 'drop-shadow(0 22px 38px rgba(0,0,0,.62))';
    if (tipo === 'brilho') return 'drop-shadow(0 0 26px ' + primaria + ')';
    return '';
  }

  function aplicarBorda(nodo, borda, tema) {
    const b = borda || {};
    const largura = num(b.largura, 0);
    const cor = resolverCor(b.cor, tema);
    if (largura > 0 && cor) {
      const estilo = ['solid', 'dashed', 'dotted', 'double'].indexOf(b.estilo) >= 0 ? b.estilo : 'solid';
      nodo.style.border = largura + 'px ' + estilo + ' ' + cor;
    }
  }

  function aplicarFundoCss(nodo, valor, tema) {
    if (!valor) return;
    const grad = resolverGradiente(valor, tema);
    if (grad) { nodo.style.backgroundImage = grad; return; }
    const cor = resolverCor(valor, tema);
    if (cor) nodo.style.backgroundColor = cor;
  }

  // ------------------------------------------------------------------ elementos

  /** Posição/tamanho/rotação/opacidade na caixa externa do elemento (o editor usa isto ao arrastar). */
  function aplicarCaixa(nodo, el) {
    const w = Math.max(1, num(el.w, 100));
    const h = Math.max(1, num(el.h, 100));
    nodo.style.left = num(el.x, 0) + 'px';
    nodo.style.top = num(el.y, 0) + 'px';
    nodo.style.width = w + 'px';
    nodo.style.height = h + 'px';
    const rot = num(el.rotacao, 0);
    nodo.style.transform = rot ? 'rotate(' + rot + 'deg)' : '';
    const op = limitar(num(el.opacidade, 1), 0, 1);
    nodo.style.opacity = op < 1 ? String(op) : '';
  }

  function modoMostraTemplate(ctx) { return ctx.modo === 'template' || !!ctx.template; }
  function modoEstatico(ctx) { return ctx.modo === 'miniatura' || ctx.modo === 'exportacao'; }
  function modoEdicao(ctx) { return ctx.modo === 'editor' || ctx.modo === 'template' || ctx.modo === 'miniatura'; }

  function vazio(icone, texto) {
    const v = div('apres-vazio');
    const i = document.createElement('i');
    i.className = icone;
    i.setAttribute('aria-hidden', 'true');
    const s = document.createElement('span');
    s.textContent = texto;
    v.append(i, s);
    return v;
  }

  function construirTexto(el, tema, ctx) {
    const est = el.estilo || {};
    const caixa = div('apres-texto');
    caixa.style.justifyContent = { middle: 'center', bottom: 'flex-end' }[est.vertical] || 'flex-start';
    caixa.style.fontFamily = pilhaFonte(familiaDoTexto(el, tema));
    caixa.style.fontSize = limitar(num(est.tamanho, 32), 1, 2000) + 'px';
    caixa.style.fontWeight = String(limitar(Math.round(num(est.peso, 400) / 100) * 100, 100, 900));
    if (est.italico) caixa.style.fontStyle = 'italic';
    const deco = [];
    if (est.sublinhado) deco.push('underline');
    if (est.tachado) deco.push('line-through');
    if (deco.length) caixa.style.textDecoration = deco.join(' ');
    if (est.maiusculas) caixa.style.textTransform = 'uppercase';
    caixa.style.color = resolverCor(est.cor, tema) || resolverCor('tema:texto', tema) || '#FFFFFF';
    caixa.style.textAlign = ['left', 'center', 'right', 'justify'].indexOf(est.alinhamento) >= 0 ? est.alinhamento : 'left';
    caixa.style.lineHeight = String(limitar(num(est.entrelinha, 1.15), 0.5, 4));
    if (num(est.espacamento, 0)) caixa.style.letterSpacing = num(est.espacamento, 0) + 'px';
    aplicarFundoCss(caixa, est.fundo, tema);
    aplicarBorda(caixa, est.borda, tema);
    if (num(est.raio, 0) > 0) caixa.style.borderRadius = num(est.raio, 0) + 'px';
    if (num(est.preenchimento, 0) > 0) caixa.style.padding = num(est.preenchimento, 0) + 'px';
    if (est.sombra) {
      const temFundo = !!(resolverCor(est.fundo, tema) || resolverGradiente(est.fundo, tema));
      if (temFundo) caixa.style.boxShadow = sombraCaixa(est.sombra, tema);
      else caixa.style.textShadow = sombraTexto(est.sombra, tema);
    }
    const conteudo = div('apres-texto-conteudo');
    preencherHtml(conteudo, el.html || '');
    if (el.slot === 'paginacao' && !ctx.paginacaoCrua) aplicarPaginacao(conteudo, ctx.indice, ctx.total);
    caixa.appendChild(conteudo);
    return caixa;
  }

  function construirImagem(el, tema, ctx) {
    const caixa = div('apres-imagem');
    if (num(el.raio, 0) > 0) caixa.style.borderRadius = num(el.raio, 0) + 'px';
    aplicarBorda(caixa, el.borda, tema);
    if (el.sombra) caixa.style.boxShadow = sombraCaixa(el.sombra, tema);
    const src = resolverSrc(el.src);
    if (!src) {
      if (modoEdicao(ctx)) caixa.appendChild(vazio('fa-regular fa-image', 'Imagem'));
      return caixa;
    }
    const img = document.createElement('img');
    img.alt = typeof el.alt === 'string' ? el.alt : '';
    img.draggable = false;
    img.decoding = 'async';
    img.src = src;
    img.style.objectFit = ['cover', 'contain', 'fill'].indexOf(el.ajuste) >= 0 ? el.ajuste : 'cover';
    const foco = el.foco || {};
    img.style.objectPosition = (limitar(num(foco.x, 0.5), 0, 1) * 100) + '% ' + (limitar(num(foco.y, 0.5), 0, 1) * 100) + '%';
    const filtro = { cinza: 'grayscale(1)', escurecer: 'brightness(.55)', desfocar: 'blur(8px)' }[el.filtro];
    if (filtro) img.style.filter = filtro;
    caixa.appendChild(img);
    return caixa;
  }

  function pontosForma(forma, w, h) {
    if (forma === 'triangulo') return [[w / 2, 0], [w, h], [0, h]];
    if (forma === 'hexagono') return [[w * 0.25, 0], [w * 0.75, 0], [w, h / 2], [w * 0.75, h], [w * 0.25, h], [0, h / 2]];
    if (forma === 'seta') {
      const cabeca = Math.min(w * 0.45, h * 0.9);
      const haste = h * 0.38;
      const y1 = (h - haste) / 2;
      const y2 = y1 + haste;
      return [[0, y1], [w - cabeca, y1], [w - cabeca, 0], [w, h / 2], [w - cabeca, h], [w - cabeca, y2], [0, y2]];
    }
    if (forma === 'estrela') {
      // Estrela de 5 pontas esticada para ocupar a caixa inteira.
      const brutos = [];
      for (let i = 0; i < 10; i += 1) {
        const a = (-90 + i * 36) * Math.PI / 180;
        const r = i % 2 === 0 ? 1 : 0.46;
        brutos.push([Math.cos(a) * r, Math.sin(a) * r]);
      }
      const xs = brutos.map((p) => p[0]);
      const ys = brutos.map((p) => p[1]);
      const minX = Math.min.apply(null, xs), maxX = Math.max.apply(null, xs);
      const minY = Math.min.apply(null, ys), maxY = Math.max.apply(null, ys);
      return brutos.map((p) => [(p[0] - minX) / (maxX - minX) * w, (p[1] - minY) / (maxY - minY) * h]);
    }
    return null;
  }

  function construirForma(el, tema, ctx) {
    const forma = el.forma || 'retangulo';
    const w = Math.max(1, num(el.w, 100));
    const h = Math.max(1, num(el.h, 100));
    const preench = resolverGradiente(el.gradiente, tema) || resolverCor(el.preenchimento, tema);
    const borda = el.borda || {};
    const bordaLarg = num(borda.largura, 0);
    const bordaCor = resolverCor(borda.cor, tema);
    const estilo = ['solid', 'dashed', 'dotted', 'double'].indexOf(borda.estilo) >= 0 ? borda.estilo : 'solid';
    const caixa = div('apres-forma apres-forma-' + forma);

    if (forma === 'linha') {
      const esp = bordaLarg > 0 ? bordaLarg : 4;
      const cor = bordaCor || resolverCor(el.preenchimento, tema) || resolverCor('tema:primaria', tema) || '#E23FCF';
      const traco = div('apres-linha');
      traco.style.top = (h - esp) / 2 + 'px';
      traco.style.height = esp + 'px';
      if (estilo === 'solid' || estilo === 'double') {
        const grad = !bordaCor && resolverGradiente(el.gradiente, tema);
        if (grad) traco.style.backgroundImage = grad;
        else traco.style.backgroundColor = cor;
      } else {
        traco.style.height = '0px';
        traco.style.top = (h - esp) / 2 + 'px';
        traco.style.borderTop = esp + 'px ' + estilo + ' ' + cor;
      }
      if (num(el.raio, 0) > 0) traco.style.borderRadius = num(el.raio, 0) + 'px';
      if (el.sombra) traco.style.boxShadow = sombraCaixa(el.sombra, tema);
      caixa.appendChild(traco);
      return caixa;
    }

    const pontos = pontosForma(forma, w, h);
    if (!pontos) {
      // retângulo, arredondado, pílula e círculo: CSS puro (borda e sombra seguem o contorno).
      const corpo = div('apres-forma-corpo');
      if (preench) {
        if (preench.indexOf('gradient(') >= 0) corpo.style.backgroundImage = preench;
        else corpo.style.backgroundColor = preench;
      }
      if (bordaLarg > 0 && bordaCor) corpo.style.border = bordaLarg + 'px ' + estilo + ' ' + bordaCor;
      let raio = '';
      if (forma === 'circulo') raio = '50%';
      else if (forma === 'pilula') raio = Math.min(w, h) / 2 + 'px';
      else if (forma === 'retangulo-arredondado') raio = (num(el.raio, 0) > 0 ? num(el.raio, 0) : Math.round(Math.min(w, h) * 0.16)) + 'px';
      else if (num(el.raio, 0) > 0) raio = num(el.raio, 0) + 'px';
      if (raio) corpo.style.borderRadius = raio;
      if (el.sombra) corpo.style.boxShadow = sombraCaixa(el.sombra, tema);
      caixa.appendChild(corpo);
    } else {
      // Polígonos: preenchimento recortado (aceita gradiente CSS) + contorno em SVG por cima.
      if (el.sombra) caixa.style.filter = sombraFiltro(el.sombra, tema);
      const corpo = div('apres-forma-corpo');
      corpo.style.clipPath = 'polygon(' + pontos.map((p) => p[0].toFixed(2) + 'px ' + p[1].toFixed(2) + 'px').join(', ') + ')';
      if (preench) {
        if (preench.indexOf('gradient(') >= 0) corpo.style.backgroundImage = preench;
        else corpo.style.backgroundColor = preench;
      }
      caixa.appendChild(corpo);
      if (bordaLarg > 0 && bordaCor) {
        const ns = 'http://www.w3.org/2000/svg';
        const svg = document.createElementNS(ns, 'svg');
        svg.setAttribute('class', 'apres-forma-traco');
        svg.setAttribute('width', String(w));
        svg.setAttribute('height', String(h));
        svg.setAttribute('viewBox', '0 0 ' + w + ' ' + h);
        const poli = document.createElementNS(ns, 'polygon');
        poli.setAttribute('points', pontos.map((p) => p[0].toFixed(2) + ',' + p[1].toFixed(2)).join(' '));
        poli.setAttribute('fill', 'none');
        poli.setAttribute('stroke', bordaCor);
        poli.setAttribute('stroke-width', String(bordaLarg));
        poli.setAttribute('stroke-linejoin', 'round');
        if (estilo === 'dashed') poli.setAttribute('stroke-dasharray', bordaLarg * 3 + ' ' + bordaLarg * 2);
        if (estilo === 'dotted') { poli.setAttribute('stroke-dasharray', '0 ' + bordaLarg * 2); poli.setAttribute('stroke-linecap', 'round'); }
        svg.appendChild(poli);
        caixa.appendChild(svg);
      }
    }
    if (el.slot === 'area_conteudo' && modoMostraTemplate(ctx)) {
      const rot = div('apres-rotulo-template');
      rot.textContent = 'Área de conteúdo';
      caixa.appendChild(rot);
    }
    return caixa;
  }

  const RE_CLASSE_ICONE = /^(?:fa-(?:solid|regular|brands)|fa[srb]|fa-[a-z0-9]+(?:-[a-z0-9]+)*)$/;

  function classeIcone(valor) {
    const partes = String(valor || '').split(/\s+/).filter((p) => RE_CLASSE_ICONE.test(p));
    if (!partes.some((p) => /^fa-(?:solid|regular|brands)$|^fa[srb]$/.test(p))) partes.unshift('fa-solid');
    if (!partes.some((p) => !/^fa-(?:solid|regular|brands)$|^fa[srb]$/.test(p))) partes.push('fa-star');
    return partes.slice(0, 4).join(' ');
  }

  function construirIcone(el, tema) {
    const w = Math.max(1, num(el.w, 100));
    const h = Math.max(1, num(el.h, 100));
    const caixa = div('apres-icone');
    const fundo = resolverCor(el.fundo, tema) || resolverGradiente(el.fundo, tema);
    if (fundo) aplicarFundoCss(caixa, el.fundo, tema);
    if (num(el.raio, 0) > 0) caixa.style.borderRadius = num(el.raio, 0) + 'px';
    const i = document.createElement('i');
    i.className = classeIcone(el.icone);
    i.setAttribute('aria-hidden', 'true');
    // Largura dos glifos chega a 1,25em: o limite pela largura evita estourar a caixa.
    const fator = fundo ? 0.56 : 0.86;
    i.style.fontSize = Math.max(4, Math.min(h * fator, (w * fator) / 1.25)).toFixed(1) + 'px';
    i.style.color = resolverCor(el.cor, tema) || resolverCor('tema:destaque', tema) || '#FFD15C';
    if (el.sombra) {
      if (fundo) caixa.style.boxShadow = sombraCaixa(el.sombra, tema);
      else i.style.textShadow = sombraTexto(el.sombra, tema);
    }
    caixa.appendChild(i);
    return caixa;
  }

  function construirVideo(el, tema, ctx) {
    const caixa = div('apres-video');
    if (num(el.raio, 0) > 0) caixa.style.borderRadius = num(el.raio, 0) + 'px';
    const src = resolverSrc(el.src);
    const poster = resolverSrc(el.poster);
    const ajuste = ['cover', 'contain', 'fill'].indexOf(el.ajuste) >= 0 ? el.ajuste : 'cover';
    if (modoEstatico(ctx) || !src) {
      // Miniatura/PDF não tocam vídeo: vale o pôster (ou um quadro neutro).
      if (poster) {
        const img = document.createElement('img');
        img.alt = '';
        img.draggable = false;
        img.src = poster;
        img.style.objectFit = ajuste;
        caixa.appendChild(img);
      } else {
        caixa.appendChild(vazio('fa-solid fa-film', src ? 'Vídeo' : 'Vídeo sem arquivo'));
      }
      return caixa;
    }
    const v = document.createElement('video');
    v.src = src;
    if (poster) v.poster = poster;
    v.playsInline = true;
    v.setAttribute('playsinline', '');
    v.style.objectFit = ajuste;
    if (ctx.modo === 'apresentacao') {
      v.muted = !!el.mudo;
      if (el.mudo) v.setAttribute('muted', '');
      v.loop = !!el.loop;
      v.controls = !!el.controles;
      v.preload = 'auto';
      if (el.autoplay) v.setAttribute('data-autoplay', '1');
    } else {
      v.muted = true;
      v.setAttribute('muted', '');
      v.preload = 'metadata';
      v.tabIndex = -1;
      const selo = div('apres-video-selo');
      const i = document.createElement('i');
      i.className = 'fa-solid fa-play';
      i.setAttribute('aria-hidden', 'true');
      selo.appendChild(i);
      caixa.append(v, selo);
      return caixa;
    }
    caixa.appendChild(v);
    return caixa;
  }

  function construirTabela(el, tema) {
    const est = el.estilo || {};
    const caixa = div('apres-tabela');
    if (num(est.raio, 0) > 0) caixa.style.borderRadius = num(est.raio, 0) + 'px';
    const tabela = document.createElement('table');
    tabela.className = 'apres-tabela-grade';
    tabela.style.fontFamily = pilhaFonte(nomeFonteLimpo(est.fonte) || tema.fonte_texto || 'Montserrat');
    tabela.style.fontSize = limitar(num(est.tamanho, 28), 4, 400) + 'px';
    tabela.style.color = resolverCor(est.cor, tema) || resolverCor('tema:texto', tema) || '#FFFFFF';
    const alinhamento = ['left', 'center', 'right'].indexOf(est.alinhamento) >= 0 ? est.alinhamento : 'left';
    const borda = resolverCor(est.borda, tema);
    const linhas = (Array.isArray(el.linhas) ? el.linhas : []).map((l) => (Array.isArray(l) ? l : []));
    const nLinhas = Math.max(1, linhas.length);
    const nCols = Math.max(1, linhas.reduce((m, l) => Math.max(m, l.length), 0));
    const corpo = document.createElement('tbody');
    for (let i = 0; i < nLinhas; i += 1) {
      const linha = linhas[i] || [];
      const cab = !!el.cabecalho && i === 0;
      const tr = document.createElement('tr');
      const dados = el.cabecalho ? i - 1 : i;
      const fundo = cab ? est.fundo_cabecalho : (dados % 2 === 1 ? est.fundo_alternado : est.fundo_linhas);
      for (let j = 0; j < nCols; j += 1) {
        const cel = document.createElement(cab ? 'th' : 'td');
        cel.textContent = linha[j] == null ? '' : String(linha[j]);
        cel.setAttribute('data-linha', String(i));
        cel.setAttribute('data-coluna', String(j));
        cel.style.textAlign = alinhamento;
        aplicarFundoCss(cel, fundo, tema);
        if (cab) cel.style.color = resolverCor(est.cor_cabecalho, tema) || tabela.style.color;
        if (borda) {
          if (i < nLinhas - 1) cel.style.borderBottom = '2px solid ' + borda;
          if (j < nCols - 1) cel.style.borderRight = '2px solid ' + borda;
        }
        tr.appendChild(cel);
      }
      corpo.appendChild(tr);
    }
    tabela.appendChild(corpo);
    caixa.appendChild(tabela);
    if (borda) caixa.style.boxShadow = 'inset 0 0 0 2px ' + borda;
    return caixa;
  }

  function construirApagar() {
    const caixa = div('apres-apagar');
    const rot = div('apres-rotulo-template');
    rot.textContent = 'Apagar do fundo';
    caixa.appendChild(rot);
    return caixa;
  }

  const CONSTRUTORES = {
    texto: construirTexto, imagem: construirImagem, forma: construirForma, icone: construirIcone,
    video: construirVideo, tabela: construirTabela, apagar: construirApagar,
  };

  /**
   * Nó de um elemento. Estrutura: .apres-el (posição, rotação, opacidade)
   * > .apres-anim (recebe a animação de entrada, para não brigar com a rotação) > conteúdo.
   */
  function criarElemento(el, doc, ctx) {
    ctx = ctx || {};
    if (!el || !CONSTRUTORES[el.tipo]) return null;
    if ((el.tipo === 'apagar' || el.slot === 'area_conteudo') && !modoMostraTemplate(ctx)) return null;
    const tema = (doc && doc.tema) || {};
    const nodo = div('apres-el apres-el-' + el.tipo);
    nodo.setAttribute('data-id', String(el.id || ''));
    nodo.setAttribute('data-tipo', el.tipo);
    if (el.slot) nodo.setAttribute('data-slot', String(el.slot));
    aplicarCaixa(nodo, el);
    const anim = div('apres-anim');
    anim.appendChild(CONSTRUTORES[el.tipo](el, tema, ctx));
    nodo.appendChild(anim);
    if (el.link && linkValido(el.link)) {
      nodo.setAttribute('data-link', el.link.trim());
      if (ctx.modo === 'apresentacao') nodo.classList.add('apres-com-link');
    }
    const a = el.animacao;
    if (ctx.modo === 'apresentacao' && ctx.animar !== false && a && a.tipo && a.tipo !== 'nenhuma') {
      anim.classList.add('apres-anim-pendente');
    }
    return nodo;
  }

  /** Slide completo 1920×1080 (posição relativa; quem usa escala com transform no pai). */
  function criarSlide(slide, doc, ctx) {
    ctx = Object.assign({ modo: 'editor' }, ctx || {});
    slide = slide || {};
    const tema = (doc && doc.tema) || {};
    if (ctx.indice == null || ctx.total == null) {
      const num_ = numeracao(doc, slide.id);
      if (ctx.indice == null) ctx.indice = num_.n;
      if (ctx.total == null) ctx.total = num_.total;
    }
    const raiz = div('apres-slide apres-modo-' + ctx.modo);
    raiz.setAttribute('data-slide-id', String(slide.id || ''));
    const fundo = slide.fundo || {};
    raiz.style.backgroundColor = resolverCor(fundo.cor, tema) || resolverCor('tema:fundo', tema) || '#0B0612';
    const grad = resolverGradiente(fundo.gradiente, tema);
    if (grad) {
      const g = div('apres-fundo-gradiente');
      g.style.backgroundImage = grad;
      raiz.appendChild(g);
    }
    const srcFundo = resolverSrc(fundo.imagem);
    if (srcFundo) {
      const img = document.createElement('img');
      img.className = 'apres-fundo-imagem';
      img.alt = '';
      img.draggable = false;
      img.decoding = 'async';
      img.src = srcFundo;
      img.style.objectFit = fundo.ajuste === 'contain' ? 'contain' : 'cover';
      raiz.appendChild(img);
    }
    (slide.elementos || []).forEach((el) => {
      const nodo = criarElemento(el, doc, ctx);
      if (nodo) raiz.appendChild(nodo);
    });
    return raiz;
  }

  /** Espera as imagens do nó carregarem/decodificarem (com limite). */
  function aguardarMidias(raiz, limite) {
    const imgs = Array.prototype.slice.call(raiz.querySelectorAll('img'));
    const promessas = imgs.map((img) => {
      const decodificar = () => (img.decode ? img.decode().catch(() => {}) : Promise.resolve());
      if (img.complete) return img.naturalWidth ? decodificar() : Promise.resolve();
      return new Promise((res) => {
        img.addEventListener('load', () => decodificar().then(res), { once: true });
        img.addEventListener('error', res, { once: true });
      });
    });
    return Promise.race([Promise.all(promessas), esperar(limite || 10000)]);
  }

  // ------------------------------------------------------------------ animações de entrada

  const QUADROS_ENTRADA = {
    aparecer: [{ opacity: 0 }, { opacity: 1 }],
    fade: [{ opacity: 0 }, { opacity: 1 }],
    subir: [{ opacity: 0, transform: 'translateY(90px)' }, { opacity: 1, transform: 'translateY(0)' }],
    descer: [{ opacity: 0, transform: 'translateY(-90px)' }, { opacity: 1, transform: 'translateY(0)' }],
    'entrar-esquerda': [{ opacity: 0, transform: 'translateX(-180px)' }, { opacity: 1, transform: 'translateX(0)' }],
    'entrar-direita': [{ opacity: 0, transform: 'translateX(180px)' }, { opacity: 1, transform: 'translateX(0)' }],
    zoom: [{ opacity: 0, transform: 'scale(.55)' }, { opacity: 1, transform: 'scale(1)' }],
    revelar: [{ opacity: 1, clipPath: 'inset(0 100% 0 0)' }, { opacity: 1, clipPath: 'inset(0 0% 0 0)' }],
  };

  /** Toca a entrada de um elemento (nó .apres-el ou .apres-anim). Resolve ao terminar. */
  function animarEntrada(nodo, animacao) {
    if (!nodo) return Promise.resolve();
    const alvo = nodo.classList && nodo.classList.contains('apres-anim') ? nodo : (nodo.querySelector && nodo.querySelector('.apres-anim')) || nodo;
    const a = animacao || {};
    const quadros = QUADROS_ENTRADA[a.tipo];
    if (!quadros || typeof alvo.animate !== 'function') {
      alvo.classList.remove('apres-anim-pendente');
      return Promise.resolve();
    }
    const duracao = a.tipo === 'aparecer' ? 1 : limitar(num(a.duracao, 600), 1, 20000);
    const animacaoWeb = alvo.animate(quadros, {
      duration: duracao, delay: limitar(num(a.atraso, 0), 0, 60000),
      easing: a.tipo === 'zoom' ? 'cubic-bezier(.2,.9,.25,1.15)' : 'cubic-bezier(.2,.75,.25,1)',
      fill: 'both',
    });
    // O fill 'both' segura o estado inicial durante o atraso; a classe pode sair já.
    alvo.classList.remove('apres-anim-pendente');
    alvo._apresAnimacao = animacaoWeb;
    return animacaoWeb.finished.then(() => { animacaoWeb.cancel(); }, () => {});
  }

  /** Mostra tudo no estado final (pula animações em andamento ou pendentes). */
  function finalizarAnimacoes(raiz) {
    raiz.querySelectorAll('.apres-anim').forEach((n) => {
      n.classList.remove('apres-anim-pendente');
      if (n._apresAnimacao) { try { n._apresAnimacao.cancel(); } catch (e) { /* já terminou */ } n._apresAnimacao = null; }
    });
  }

  /**
   * Plano das entradas do slide: lista de passos. O passo 0 roda sozinho ao abrir;
   * cada passo seguinte espera um avanço (clique). Um passo é uma lista de lotes que
   * rodam em sequência; os elementos de um lote entram juntos.
   */
  function planoAnimacoes(slide, opcoes) {
    const template = !!(opcoes && opcoes.template);
    const passos = [[]];
    let loteAtual = null;
    ((slide && slide.elementos) || []).forEach((el) => {
      if (!el || !CONSTRUTORES[el.tipo]) return;
      if ((el.tipo === 'apagar' || el.slot === 'area_conteudo') && !template) return;
      const a = el.animacao;
      if (!a || !QUADROS_ENTRADA[a.tipo]) return;
      const gatilho = a.gatilho || 'auto';
      if (gatilho === 'clique') {
        loteAtual = [el];
        passos.push([loteAtual]);
      } else if (gatilho === 'junto' && loteAtual) {
        loteAtual.push(el);
      } else {
        loteAtual = [el];
        passos[passos.length - 1].push(loteAtual);
      }
    });
    return passos;
  }

  async function executarPasso(raizSlide, passo) {
    for (const lote of passo || []) {
      await Promise.all(lote.map((el) => {
        const nodo = raizSlide.querySelector('.apres-el[data-id="' + cssEscape(el.id) + '"]');
        return nodo ? animarEntrada(nodo, el.animacao) : Promise.resolve();
      }));
    }
  }

  function cssEscape(v) {
    return window.CSS && CSS.escape ? CSS.escape(String(v)) : String(v).replace(/["\\\]]/g, '\\$&');
  }

  // ------------------------------------------------------------------ transições de slide

  /**
   * Transição entre dois nós de slide já no mesmo palco (ambos em left/top 0).
   * direcao: 1 avançando, -1 voltando (inverte os empurrões). Quem chama remove o `atual` depois.
   */
  function transicionar(atual, proximo, transicao, direcao) {
    const t = transicao || {};
    const tipo = t.tipo || 'nenhuma';
    const duracao = limitar(num(t.duracao, 700), 0, 10000);
    if (!atual || !proximo || tipo === 'nenhuma' || duracao <= 0 || typeof proximo.animate !== 'function') {
      if (atual) atual.style.visibility = 'hidden';
      return Promise.resolve();
    }
    const d = direcao < 0 ? -1 : 1;
    const op = { duration: duracao, easing: 'cubic-bezier(.45,0,.2,1)', fill: 'both' };
    const anims = [];
    proximo.style.zIndex = '2';
    atual.style.zIndex = '1';
    const mover = (nodo, de, para) => anims.push(nodo.animate([{ transform: de }, { transform: para }], op));
    switch (tipo) {
      case 'fade':
        anims.push(proximo.animate([{ opacity: 0 }, { opacity: 1 }], op));
        break;
      case 'empurrar-esquerda':
        mover(proximo, 'translateX(' + 100 * d + '%)', 'translateX(0)');
        mover(atual, 'translateX(0)', 'translateX(' + -100 * d + '%)');
        break;
      case 'empurrar-direita':
        mover(proximo, 'translateX(' + -100 * d + '%)', 'translateX(0)');
        mover(atual, 'translateX(0)', 'translateX(' + 100 * d + '%)');
        break;
      case 'empurrar-cima':
        mover(proximo, 'translateY(' + 100 * d + '%)', 'translateY(0)');
        mover(atual, 'translateY(0)', 'translateY(' + -100 * d + '%)');
        break;
      case 'empurrar-baixo':
        mover(proximo, 'translateY(' + -100 * d + '%)', 'translateY(0)');
        mover(atual, 'translateY(0)', 'translateY(' + 100 * d + '%)');
        break;
      case 'revelar':
        // O slide atual sai por cima e revela o próximo, parado embaixo.
        atual.style.zIndex = '2';
        proximo.style.zIndex = '1';
        mover(atual, 'translateX(0)', 'translateX(' + -100 * d + '%)');
        break;
      case 'zoom':
        anims.push(proximo.animate([{ opacity: 0, transform: 'scale(.82)' }, { opacity: 1, transform: 'scale(1)' }], op));
        anims.push(atual.animate([{ opacity: 1, transform: 'scale(1)' }, { opacity: 0, transform: 'scale(1.14)' }], op));
        break;
      case 'cobrir':
        mover(proximo, 'translateX(' + 100 * d + '%)', 'translateX(0)');
        break;
      default:
        atual.style.visibility = 'hidden';
        return Promise.resolve();
    }
    return Promise.all(anims.map((a) => a.finished.catch(() => {}))).then(() => {
      // Esconde antes de cancelar: cancelar devolve o atual ao lugar e piscaria.
      atual.style.visibility = 'hidden';
      anims.forEach((a) => { try { a.cancel(); } catch (e) { /* ok */ } });
      proximo.style.zIndex = '';
      atual.style.zIndex = '';
    });
  }

  window.APRES_RENDER = {
    LARGURA, ALTURA, ANIMACOES, GATILHOS, TRANSICOES, FORMAS, SLOTS, PAPEIS,
    criarSlide, criarElemento, aplicarCaixa, resolverCor, resolverGradiente, resolverSrc,
    carregarFontes, carregarCatalogoFontes, carregarFamilia, infoFonte, pilhaFonte, familiaDoTexto,
    urlGoogleFonts, fontesUsadas, nomeFonteLimpo,
    sanearHtml, textoPlano, preencherHtml, linkValido, escaparHtml,
    numeracao, slidesVisiveis, aguardarMidias,
    animarEntrada, finalizarAnimacoes, planoAnimacoes, executarPasso, transicionar,
    cssEscape, pontosForma, classeIcone,
    get catalogo() { return catalogo; },
  };
})();
