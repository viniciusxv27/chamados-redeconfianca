/*
 * Assistente de Apresentações — exportação no navegador (window.APRES_EXPORTAR):
 * PDF, imagem de um slide e vídeo narrado.
 *
 * Cada slide é desenhado pelo APRES_RENDER (modo "exportacao") e vira uma
 * imagem SVG (foreignObject) que leva junto o CSS dos slides, as fontes e as
 * mídias embutidas; essa imagem é pintada num canvas. É o mesmo desenho do
 * editor — por isso o PDF sai igual ao que a pessoa viu. As mídias vêm da
 * mesma origem (/apresentacoes/midia/<id>/) e as fontes de CDN com CORS (os
 * <link> das fontes têm crossorigin, senão as regras @font-face ficam
 * ilegíveis): assim o canvas não fica "contaminado" e dá para ler os pixels.
 *
 * O PDF é montado aqui mesmo, sem biblioteca: uma página JPEG por slide em
 * 960×540 pt (16:9). O vídeo grava o canvas em tempo real com a narração de
 * cada slide (MediaRecorder, webm) e o servidor converte para mp4.
 */
(function () {
  'use strict';

  const R = window.APRES_RENDER;
  const LARG = 1920;
  const ALT = 1080;
  const XHTML = 'http://www.w3.org/1999/xhtml';
  const TRANSICAO_VIDEO = 500;          // ms do esmaecer entre slides no vídeo
  const PAUSA_DEPOIS_DA_FALA = 700;     // ms parado depois da narração, antes do próximo slide
  const SEGUNDOS_SEM_NARRACAO = 5;

  class ErroExportacao extends Error {}
  class Cancelado extends Error {
    constructor() { super('Exportação cancelada.'); this.cancelado = true; }
  }

  function num(v, padrao) { const n = Number(v); return Number.isFinite(n) ? n : padrao; }
  function esperar(ms) { return new Promise((r) => setTimeout(r, ms)); }
  function quadro() { return new Promise((r) => requestAnimationFrame(() => r())); }

  // ------------------------------------------------------------------ arquivos em data URL

  const cacheDados = new Map();

  function paraDataUrl(blob) {
    return new Promise((resolve, reject) => {
      const leitor = new FileReader();
      leitor.onload = () => resolve(leitor.result);
      leitor.onerror = () => reject(leitor.error);
      leitor.readAsDataURL(blob);
    });
  }

  /** Arquivo → data URL (null se não carregou). A imagem SVG não busca nada fora dela mesma. */
  function dadosDe(url) {
    let absoluta;
    try { absoluta = new URL(url, window.location.href).href; } catch (e) { return Promise.resolve(null); }
    if (/^data:/i.test(absoluta)) return Promise.resolve(absoluta);
    if (!cacheDados.has(absoluta)) {
      const mesmaOrigem = /^blob:/i.test(absoluta) || new URL(absoluta).origin === window.location.origin;
      cacheDados.set(absoluta, fetch(absoluta, { credentials: mesmaOrigem ? 'same-origin' : 'omit', mode: mesmaOrigem ? 'same-origin' : 'cors' })
        .then((r) => (r.ok ? r.blob() : null))
        .then((b) => (b ? paraDataUrl(b) : null))
        .catch(() => null));
    }
    return cacheDados.get(absoluta);
  }

  // ------------------------------------------------------------------ CSS que vai dentro da imagem

  function primeiraUrl(src) {
    const itens = Array.from(String(src || '').matchAll(/url\(\s*(['"]?)([^'")]+)\1\s*\)\s*(?:format\(\s*(['"]?)([^'")]+)\3\s*\))?/g));
    const woff2 = itens.find((m) => /woff2/i.test(m[4] || '') || /\.woff2(?:[?#]|$)/i.test(m[2]));
    return (woff2 || itens[0] || [])[2] || '';
  }

  /** Regra do Font Awesome que interessa: só classes de ícone usadas no documento (e o ::before delas). */
  function regraDeIcone(seletor, usadas) {
    return String(seletor || '').split(',').some((parte) => {
      const classes = parte.match(/\.[A-Za-z0-9_-]+/g);
      if (!classes || !classes.every((c) => usadas.has(c.slice(1)))) return false;
      return /^[\s.A-Za-z0-9_:-]*$/.test(parte);
    });
  }

  /**
   * CSS do documento para a imagem: regras .apres-* (render), Font Awesome dos ícones usados e
   * @font-face das famílias usadas — só o subconjunto latino, com o arquivo embutido.
   */
  async function cssDoDocumento(doc) {
    const usadas = R.fontesUsadas(doc);
    const familias = new Set(Array.from(usadas.familias.keys()).map((f) => f.toLowerCase()));
    const classesIcone = new Set();
    ((doc && doc.slides) || []).forEach((s) => ((s && s.elementos) || []).forEach((el) => {
      if (el && el.tipo === 'icone') R.classeIcone(el.icone).split(/\s+/).forEach((c) => classesIcone.add(c));
    }));
    if (usadas.icones) {
      familias.add('font awesome 6 free');
      familias.add('font awesome 6 brands');
    }
    const regras = [];
    const faces = [];

    function coletar(lista, base) {
      Array.prototype.forEach.call(lista || [], (regra) => {
        if (regra.type === 3 && regra.styleSheet) {                          // @import
          try { coletar(regra.styleSheet.cssRules, regra.styleSheet.href || base); } catch (e) { /* outra origem */ }
          return;
        }
        if (regra.type === 4) {                                              // @media
          const media = regra.media && regra.media.mediaText;
          if (!media || window.matchMedia(media).matches) coletar(regra.cssRules, base);
          return;
        }
        if (regra.type === 5) {                                              // @font-face
          const familia = (regra.style.getPropertyValue('font-family') || '').replace(/["']/g, '').trim().toLowerCase();
          if (!familias.has(familia)) return;
          // Português cabe no latim básico + estendido; cirílico, grego e vietnamita só pesariam.
          // O navegador escreve a faixa com ou sem zeros à esquerda (U+0000-00FF ou U+0-FF).
          const faixa = (regra.style.getPropertyValue('unicode-range') || '').replace(/\s/g, '')
            .replace(/U\+0+([0-9A-F])/gi, 'U+$1').replace(/-0+([0-9A-F])/gi, '-$1');
          if (faixa && !/U\+0-FF\b|U\+100-/i.test(faixa)) return;
          faces.push({ regra, base });
          return;
        }
        if (regra.type !== 1) return;                                        // só regras de estilo
        const seletor = regra.selectorText || '';
        if (seletor.indexOf('.apres-') >= 0) regras.push(regra.cssText);
        else if (usadas.icones && (regraDeIcone(seletor, classesIcone) || (/:root/.test(seletor) && /--fa/.test(regra.cssText)))) regras.push(regra.cssText);
      });
    }

    Array.prototype.forEach.call(document.styleSheets, (folha) => {
      let lista = null;
      try { lista = folha.cssRules; } catch (e) { lista = null; }            // folha de outra origem sem CORS
      if (lista) coletar(lista, folha.href || window.location.href);
    });

    const blocos = await Promise.all(faces.map(async (f) => {
      const estilo = f.regra.style;
      const url = primeiraUrl(estilo.getPropertyValue('src'));
      if (!url) return '';
      let absoluta;
      try { absoluta = new URL(url, f.base).href; } catch (e) { return ''; }
      const dados = await dadosDe(absoluta);
      if (!dados) return '';
      const props = ['font-family', 'font-style', 'font-weight', 'font-stretch', 'unicode-range']
        .map((p) => [p, estilo.getPropertyValue(p)]).filter((p) => p[1]).map((p) => p[0] + ':' + p[1]);
      props.push('font-display:block');
      props.push('src:url("' + dados + '")');
      return '@font-face{' + props.join(';') + '}';
    }));
    return blocos.filter(Boolean).concat(regras).join('\n');
  }

  // ------------------------------------------------------------------ slide → canvas

  async function embutirMidias(nodo) {
    const imagens = Array.prototype.slice.call(nodo.querySelectorAll('img'));
    await Promise.all(imagens.map(async (img) => {
      const src = img.getAttribute('src');
      if (!src || /^data:/i.test(src)) return;
      const dados = await dadosDe(src);
      if (dados) img.setAttribute('src', dados);
      else img.removeAttribute('src');
    }));
  }

  async function svgDoSlide(slide, doc, css, ctx) {
    const nodo = R.criarSlide(slide, doc, Object.assign({ modo: 'exportacao' }, ctx || {}));
    await embutirMidias(nodo);
    const envoltorio = document.createElementNS(XHTML, 'div');
    envoltorio.setAttribute('style', 'width:' + LARG + 'px;height:' + ALT + 'px;overflow:hidden;margin:0;padding:0;');
    const estilo = document.createElementNS(XHTML, 'style');
    estilo.textContent = css;
    envoltorio.append(estilo, nodo);
    const xhtml = new XMLSerializer().serializeToString(envoltorio);
    return '<svg xmlns="http://www.w3.org/2000/svg" width="' + LARG + '" height="' + ALT + '" viewBox="0 0 ' + LARG + ' ' + ALT + '">'
      + '<foreignObject x="0" y="0" width="' + LARG + '" height="' + ALT + '">' + xhtml + '</foreignObject></svg>';
  }

  async function canvasDoSlide(slide, doc, css, ctx, escala) {
    const svg = await svgDoSlide(slide, doc, css, ctx);
    const img = new Image();
    const carregou = new Promise((resolve, reject) => {
      img.onload = resolve;
      img.onerror = () => reject(new ErroExportacao('O navegador não conseguiu desenhar um dos slides.'));
    });
    img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
    await carregou;
    try { if (img.decode) await img.decode(); } catch (e) { /* já carregada */ }
    // Dois quadros: as fontes embutidas terminam de aplicar antes de pintar.
    await quadro();
    await quadro();
    const canvas = document.createElement('canvas');
    canvas.width = Math.round(LARG * (escala || 1));
    canvas.height = Math.round(ALT * (escala || 1));
    const g = canvas.getContext('2d');
    g.fillStyle = '#000000';
    g.fillRect(0, 0, canvas.width, canvas.height);
    g.drawImage(img, 0, 0, canvas.width, canvas.height);
    return canvas;
  }

  function jpeg(canvas, qualidade) {
    return new Promise((resolve, reject) => {
      try {
        canvas.toBlob((b) => (b ? resolve(b) : reject(new ErroExportacao('Não foi possível gerar a imagem do slide.'))), 'image/jpeg', qualidade || 0.92);
      } catch (e) {
        reject(new ErroExportacao('Este navegador não deixa ler o slide desenhado. Use o Chrome ou o Edge no computador, ou baixe o PowerPoint.'));
      }
    });
  }

  function contexto(doc, slide) {
    const n = R.numeracao(doc, slide.id);
    return { indice: n.n, total: n.total };
  }

  // ------------------------------------------------------------------ PDF

  function textoPdf(t) {
    let hex = 'FEFF';
    for (const ch of String(t || '')) {
      const c = ch.codePointAt(0);
      if (c > 0xFFFF) {
        const v = c - 0x10000;
        hex += (0xD800 + (v >> 10)).toString(16).padStart(4, '0') + (0xDC00 + (v & 0x3FF)).toString(16).padStart(4, '0');
      } else {
        hex += c.toString(16).padStart(4, '0');
      }
    }
    return '<' + hex.toUpperCase() + '>';
  }

  function dataPdf() {
    const d = new Date();
    const p = (n) => String(n).padStart(2, '0');
    return '(D:' + d.getFullYear() + p(d.getMonth() + 1) + p(d.getDate()) + p(d.getHours()) + p(d.getMinutes()) + p(d.getSeconds()) + ')';
  }

  /** PDF 1.4 mínimo: catálogo, páginas, info e, por slide, uma página com a imagem JPEG (DCTDecode). */
  function montarPdf(paginas, titulo) {
    const LARG_PT = 960;
    const ALT_PT = 540;
    const codificador = new TextEncoder();
    const partes = [];
    const deslocamentos = [];
    let tamanho = 0;
    const escrever = (x) => {
      const b = typeof x === 'string' ? codificador.encode(x) : x;
      partes.push(b);
      tamanho += b.length;
    };
    const objeto = (n, corpo) => {
      deslocamentos[n] = tamanho;
      escrever(n + ' 0 obj\n');
      corpo();
      escrever('\nendobj\n');
    };
    const idPagina = (k) => 4 + k * 3;
    escrever('%PDF-1.4\n');
    escrever(new Uint8Array([0x25, 0xE2, 0xE3, 0xCF, 0xD3, 0x0A]));
    objeto(1, () => escrever('<< /Type /Catalog /Pages 2 0 R >>'));
    objeto(2, () => escrever('<< /Type /Pages /Count ' + paginas.length + ' /Kids [' + paginas.map((p, k) => idPagina(k) + ' 0 R').join(' ') + '] >>'));
    objeto(3, () => escrever('<< /Title ' + textoPdf(titulo) + ' /Producer (Portal Rede Confianca) /CreationDate ' + dataPdf() + ' >>'));
    paginas.forEach((p, k) => {
      const pagina = idPagina(k);
      objeto(pagina, () => escrever('<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ' + LARG_PT + ' ' + ALT_PT + '] /Resources << /XObject << /Im' + k + ' '
        + (pagina + 1) + ' 0 R >> /ProcSet [/PDF /ImageC] >> /Contents ' + (pagina + 2) + ' 0 R >>'));
      objeto(pagina + 1, () => {
        escrever('<< /Type /XObject /Subtype /Image /Width ' + p.largura + ' /Height ' + p.altura
          + ' /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ' + p.bytes.length + ' >>\nstream\n');
        escrever(p.bytes);
        escrever('\nendstream');
      });
      const desenho = 'q ' + LARG_PT + ' 0 0 ' + ALT_PT + ' 0 0 cm /Im' + k + ' Do Q';
      objeto(pagina + 2, () => escrever('<< /Length ' + desenho.length + ' >>\nstream\n' + desenho + '\nendstream'));
    });
    const total = 4 + paginas.length * 3;
    const inicioXref = tamanho;
    let xref = 'xref\n0 ' + total + '\n0000000000 65535 f \n';
    for (let n = 1; n < total; n += 1) xref += String(deslocamentos[n]).padStart(10, '0') + ' 00000 n \n';
    escrever(xref);
    escrever('trailer\n<< /Size ' + total + ' /Root 1 0 R /Info 3 0 R >>\nstartxref\n' + inicioXref + '\n%%EOF\n');
    return new Blob(partes, { type: 'application/pdf' });
  }

  function verificador(o) {
    return () => { if (o.cancelado && o.cancelado()) throw new Cancelado(); };
  }

  /**
   * PDF dos slides visíveis. opcoes: titulo, escala (1 = 1920×1080), qualidade (JPEG),
   * aoProgresso(feito, total, etapa), cancelado() → true interrompe.
   */
  async function gerarPdf(doc, opcoes) {
    const o = opcoes || {};
    const conferir = verificador(o);
    const slides = R.slidesVisiveis(doc);
    if (!slides.length) throw new ErroExportacao('Não há slides visíveis para exportar.');
    const progresso = (feito, etapa) => { if (o.aoProgresso) o.aoProgresso(feito, slides.length, etapa); };
    progresso(0, 'Carregando fontes e imagens…');
    await R.carregarFontes(doc, { limite: 10000 });
    conferir();
    const css = await cssDoDocumento(doc);
    conferir();
    // Um desenho pequeno de aquecimento: a primeira imagem SVG às vezes pinta antes de as fontes embutidas aplicarem.
    await canvasDoSlide(slides[0], doc, css, contexto(doc, slides[0]), 0.05);
    const paginas = [];
    for (let i = 0; i < slides.length; i += 1) {
      conferir();
      progresso(i, 'Desenhando o slide ' + (i + 1) + ' de ' + slides.length + '…');
      const canvas = await canvasDoSlide(slides[i], doc, css, contexto(doc, slides[i]), o.escala || 1);
      const blob = await jpeg(canvas, o.qualidade || 0.92);
      paginas.push({ bytes: new Uint8Array(await blob.arrayBuffer()), largura: canvas.width, altura: canvas.height });
      canvas.width = 0;
      canvas.height = 0;
    }
    conferir();
    progresso(slides.length, 'Montando o PDF…');
    return montarPdf(paginas, o.titulo || 'Apresentação');
  }

  /** Um slide em JPEG (1920×1080 na escala 1). */
  async function imagemDoSlide(slide, doc, opcoes) {
    const o = opcoes || {};
    await R.carregarFontes(doc, { limite: 8000 });
    const css = await cssDoDocumento(doc);
    await canvasDoSlide(slide, doc, css, contexto(doc, slide), 0.05);
    const canvas = await canvasDoSlide(slide, doc, css, contexto(doc, slide), o.escala || 1);
    return jpeg(canvas, o.qualidade || 0.92);
  }

  // ------------------------------------------------------------------ vídeo narrado

  function formatoDeVideo() {
    if (typeof MediaRecorder !== 'function' || !window.HTMLCanvasElement || !HTMLCanvasElement.prototype.captureStream) return '';
    return ['video/webm;codecs=vp9,opus', 'video/webm;codecs=vp8,opus', 'video/webm'].find((t) => MediaRecorder.isTypeSupported(t)) || '';
  }

  /**
   * Grava os slides visíveis com a narração de cada um (em tempo real: a gravação leva o tempo do vídeo).
   * opcoes: aoProgresso(fracao 0..1, etapa), cancelado(), segundosSemNarracao. Devolve {blob, avisos, segundos}.
   */
  async function gravarVideo(doc, opcoes) {
    const o = opcoes || {};
    const conferir = verificador(o);
    const tipo = formatoDeVideo();
    if (!tipo) throw new ErroExportacao('Este navegador não grava vídeo. Use o Chrome ou o Edge no computador.');
    const Contexto = window.AudioContext || window.webkitAudioContext;
    if (!Contexto) throw new ErroExportacao('Este navegador não mistura o áudio da narração. Use o Chrome ou o Edge.');
    const slides = R.slidesVisiveis(doc);
    if (!slides.length) throw new ErroExportacao('Não há slides visíveis para gravar.');
    const progresso = (fracao, etapa) => { if (o.aoProgresso) o.aoProgresso(Math.max(0, Math.min(1, fracao)), etapa); };
    const avisos = [];
    const audio = new Contexto();
    const quadros = [];
    try {
      // 1) Prepara tudo antes de gravar: a gravação é em tempo real e não pode esperar download.
      progresso(0, 'Carregando fontes e imagens…');
      await R.carregarFontes(doc, { limite: 10000 });
      const css = await cssDoDocumento(doc);
      await canvasDoSlide(slides[0], doc, css, contexto(doc, slides[0]), 0.05);
      for (let i = 0; i < slides.length; i += 1) {
        conferir();
        progresso((i / slides.length) * 0.25, 'Preparando o slide ' + (i + 1) + ' de ' + slides.length + '…');
        const canvas = await canvasDoSlide(slides[i], doc, css, contexto(doc, slides[i]), 1);
        let som = null;
        const url = slides[i].narracao && R.resolverSrc(slides[i].narracao.url);
        if (url) {
          try {
            const resp = await fetch(url, { credentials: 'same-origin' });
            if (!resp.ok) throw new Error(String(resp.status));
            som = await audio.decodeAudioData(await resp.arrayBuffer());
          } catch (e) {
            avisos.push('A narração do slide ' + (i + 1) + ' não carregou; ele entrou sem som.');
          }
        }
        const duracao = som ? som.duration * 1000 + PAUSA_DEPOIS_DA_FALA : num(o.segundosSemNarracao, SEGUNDOS_SEM_NARRACAO) * 1000;
        quadros.push({ canvas, som, duracao });
      }
      conferir();

      // 2) Grava: canvas (30 quadros/s) + narração mixada num destino de áudio.
      const tela = document.createElement('canvas');
      tela.width = LARG;
      tela.height = ALT;
      const g = tela.getContext('2d');
      g.fillStyle = '#000000';
      g.fillRect(0, 0, LARG, ALT);
      const fluxoVideo = tela.captureStream(30);
      const destino = audio.createMediaStreamDestination();
      const fluxo = new MediaStream(fluxoVideo.getVideoTracks().concat(destino.stream.getAudioTracks()));
      const gravador = new MediaRecorder(fluxo, { mimeType: tipo, videoBitsPerSecond: 2500000, audioBitsPerSecond: 128000 });
      const pedacos = [];
      gravador.ondataavailable = (ev) => { if (ev.data && ev.data.size) pedacos.push(ev.data); };
      const parou = new Promise((resolve) => { gravador.onstop = resolve; });
      if (audio.state === 'suspended') await audio.resume();
      const total = quadros.reduce((s, q) => s + q.duracao, 0) + 600;
      let decorrido = 0;
      let anterior = null;
      gravador.start(1000);
      try {
        for (let k = 0; k < quadros.length; k += 1) {
          const q = quadros[k];
          if (q.som) {
            const fonte = audio.createBufferSource();
            fonte.buffer = q.som;
            fonte.connect(destino);
            fonte.start(audio.currentTime + 0.05);
          }
          const inicio = performance.now();
          // setTimeout (e não requestAnimationFrame): o rAF para quando a aba perde o foco.
          await new Promise((resolve, reject) => {
            const tique = () => {
              if (o.cancelado && o.cancelado()) { reject(new Cancelado()); return; }
              const t = performance.now() - inicio;
              const alfa = anterior ? Math.min(1, t / TRANSICAO_VIDEO) : 1;
              if (anterior && alfa < 1) g.drawImage(anterior, 0, 0);
              g.globalAlpha = alfa;
              g.drawImage(q.canvas, 0, 0);
              g.globalAlpha = 1;
              progresso(0.25 + 0.75 * ((decorrido + Math.min(t, q.duracao)) / total), 'Gravando o slide ' + (k + 1) + ' de ' + quadros.length + '…');
              if (t >= q.duracao) { resolve(); return; }
              setTimeout(tique, 33);
            };
            tique();
          });
          decorrido += q.duracao;
          anterior = q.canvas;
        }
        await esperar(600);
      } finally {
        if (gravador.state !== 'inactive') gravador.stop();
        await parou;
        fluxoVideo.getTracks().forEach((t) => t.stop());
      }
      progresso(1, 'Gravação concluída.');
      return { blob: new Blob(pedacos, { type: 'video/webm' }), avisos, segundos: Math.round(total / 1000) };
    } finally {
      quadros.forEach((q) => { q.canvas.width = 0; q.canvas.height = 0; });
      try { audio.close(); } catch (e) { /* já fechado */ }
    }
  }

  // ------------------------------------------------------------------ download

  function nomeDoArquivo(titulo, extensao) {
    const base = String(titulo || 'apresentacao').normalize('NFD').replace(/[̀-ͯ]/g, '')
      .replace(/[^A-Za-z0-9 _-]+/g, ' ').trim().replace(/\s+/g, '-').slice(0, 80) || 'apresentacao';
    return base + '.' + extensao;
  }

  function baixar(blob, nome) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = nome;
    link.rel = 'noopener';
    link.style.display = 'none';
    document.body.appendChild(link);
    link.click();
    setTimeout(() => { URL.revokeObjectURL(url); link.remove(); }, 60000);
  }

  window.APRES_EXPORTAR = {
    gerarPdf, imagemDoSlide, gravarVideo, formatoDeVideo, baixar, nomeDoArquivo, montarPdf, cssDoDocumento,
    ErroExportacao, Cancelado,
  };
})();
