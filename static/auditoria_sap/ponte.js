/* Automação SAP dentro do portal: conversa com a Ponte do computador (127.0.0.1) e desenha o app aqui.
   Imagem: faixas PNG que mudaram. Mouse/teclado: mensagens para a ponte, que entrega ao app. */
(function () {
  'use strict';
  var raiz = document.getElementById('pnApp');
  if (!raiz) return;
  var PONTE = 'http://127.0.0.1:47861';
  var WS = 'ws://127.0.0.1:47861/ws';
  var tela = raiz.querySelector('[data-pn-tela]');
  var palco = raiz.querySelector('[data-pn-palco]');
  var aviso = raiz.querySelector('[data-pn-aviso]');
  var chipStatus = raiz.querySelector('[data-pn-status]');
  var rodape = raiz.querySelector('[data-pn-rodape]');
  var tituloApp = raiz.querySelector('[data-pn-titulo]');
  // O teclado entra por um campo invisível: pega tecla de verdade, acento composto, emoji (Win+.) e ditado.
  var teclado = document.createElement('textarea');
  teclado.className = 'pn-teclado';
  teclado.setAttribute('aria-label', 'Teclado do app');
  teclado.setAttribute('autocomplete', 'off');
  teclado.setAttribute('autocorrect', 'off');
  teclado.setAttribute('autocapitalize', 'off');
  teclado.spellcheck = false;
  tela.appendChild(teclado);
  function focarTeclado() { teclado.focus({preventScroll: true}); }
  tela.addEventListener('mousedown', function (e) { if (e.target === tela) { e.preventDefault(); focarTeclado(); } });
  var ws = null, camadas = {}, principal = null, estado = null, sessao = null;
  var tentando = null, dpr = window.devicePixelRatio || 1, ultimoTamanho = '', apertado = false;
  var filaDesenho = {};
  var abrindoDesde = 0;           // clicou em abrir/reiniciar: mostra "abrindo" até o app aparecer (ou 30 s)
  // Sempre a versão publicada mais atual (24/11/2026: "está usando versões anteriores"). A página
  // sabe qual é a publicada; a Ponte diz qual está instalada. Diferente (ou instalação antiga, sem
  // número) e o app fechado: a página manda a Ponte instalar a publicada antes de abrir. Funciona
  // com qualquer Ponte (o /app/instalar sempre baixa a publicada). App aberto: avisa e atualiza
  // assim que ele fechar.
  var PUBLICADA = raiz.dataset.versao || '';
  var atualizando = false, abrirDepois = false, falhouEm = '';
  function desatualizado(e) {
    return !!(e && e.app && e.app.instalado && PUBLICADA && e.app.versao !== PUBLICADA && falhouEm !== PUBLICADA);
  }

  function el(html) { var d = document.createElement('div'); d.innerHTML = html.trim(); return d.firstChild; }
  function esc(t) { var s = document.createElement('span'); s.textContent = t == null ? '' : String(t); return s.innerHTML; }

  // ── Estados (o aviso por cima da tela) ───────────────────────────────────
  var TELAS = {
    windows: function () {
      return '<i class="fab fa-windows pn-ico"></i><h3>Abra num computador com Windows</h3>' +
        '<p>A Automação SAP comanda o SAP GUI do próprio computador. Abra esta página no Windows da loja, onde o SAP está instalado.</p>';
    },
    procurando: function () {
      return '<span class="pn-gira"></span><h3>Procurando a Ponte neste computador…</h3><p>Se o navegador perguntar se o portal pode acessar outros apps deste dispositivo, clique em <b>Permitir</b>.</p>';
    },
    sem_ponte: function () {
      var baixar = raiz.dataset.baixarPonte;
      return '<i class="fas fa-plug pn-ico"></i><h3>Instale a Ponte (uma vez só)</h3>' +
        '<p>A Ponte é um programinha leve que roda o app aqui dentro do portal. Não precisa de administrador.</p>' +
        '<ol class="pn-passos"><li>' + (baixar ? '<a class="pn-botao" href="' + baixar + '"><i class="fas fa-download"></i>Baixar a Ponte</a>' :
          '<span class="pn-nota">O instalador da Ponte ainda não foi publicado pelo SUPERADMIN.</span>') + '</li>' +
        '<li>Abra o arquivo baixado e siga. Se o Windows avisar, clique em <b>Mais informações › Executar assim mesmo</b>.</li>' +
        '<li>Pronto: esta tela conecta sozinha.</li></ol>' +
        '<p class="pn-nota">Já instalou? <a href="ponte-sap://abrir" class="pn-link">Abrir a Ponte</a> · procurando de novo a cada poucos segundos.</p>';
    },
    sem_app: function () {
      var i = estado && estado.app.instalando;
      if (i && !i.erro && i.progresso < 100) {
        return '<i class="fas fa-box-open pn-ico"></i><h3>' + esc(i.etapa) + '</h3><div class="pn-barra-prog"><span style="width:' + (i.progresso || 3) + '%"></span></div>';
      }
      return '<i class="fas fa-box-open pn-ico"></i><h3>O app ainda não está neste computador</h3>' +
        (i && i.erro ? '<p class="pn-erro">' + esc(i.erro) + '</p>' : '<p>A Ponte baixa a versão publicada aqui no portal e instala. As pastas config e logs de uma instalação antiga são mantidas.</p>') +
        '<button type="button" class="pn-botao" data-pn-acao="instalar"><i class="fas fa-download"></i>Instalar a Automação SAP' + (raiz.dataset.versao ? ' ' + esc(raiz.dataset.versao) : '') + '</button>';
    },
    fechado: function () {
      return '<img src="' + raiz.dataset.logo + '" alt="" class="pn-logo"><h3>Automação SAP</h3><p>O app está instalado neste computador. Abra o SAP e faça o login antes de usar.</p>' +
        '<button type="button" class="pn-botao" data-pn-acao="iniciar"><i class="fas fa-play"></i>Abrir aqui no portal</button>';
    },
    abrindo: function () { return '<span class="pn-gira"></span><h3>Abrindo a Automação SAP…</h3>'; },
    atualizando: function () {
      var i = estado && estado.app && estado.app.instalando;
      return '<i class="fas fa-cloud-arrow-down pn-ico"></i><h3>Atualizando a Automação SAP para a ' + esc(PUBLICADA) + '</h3>' +
        '<p>' + esc((i && i.etapa) || 'Começando…') + '</p>' +
        '<div class="pn-barra-prog"><span style="width:' + ((i && i.progresso) || 3) + '%"></span></div>' +
        '<p class="pn-nota">Neste computador: ' + esc((estado && estado.app && estado.app.versao) || 'versão antiga, sem número') +
        '. As pastas config e logs da loja ficam como estão.</p>';
    },
    erro: function (texto) {
      return '<i class="fas fa-triangle-exclamation pn-ico pn-ico-erro"></i><h3>' + esc(texto) + '</h3>' +
        '<button type="button" class="pn-botao" data-pn-acao="reconectar"><i class="fas fa-rotate"></i>Conectar de novo</button>';
    }
  };

  function mostrarAviso(nome, extra) {
    if (!nome) { aviso.hidden = true; raiz.classList.add('pn-ativo'); return; }
    raiz.classList.remove('pn-ativo');
    aviso.innerHTML = '<div class="pn-aviso-caixa">' + TELAS[nome](extra) + '</div>';
    aviso.hidden = false;
  }

  function status(texto, tipo) {
    chipStatus.className = 'pn-chip pn-chip-' + (tipo || 'neutro');
    chipStatus.innerHTML = '<span class="pn-ponto"></span>' + esc(texto);
  }

  aviso.addEventListener('click', function (e) {
    var b = e.target.closest('[data-pn-acao]');
    if (!b) return;
    var acao = b.dataset.pnAcao;
    if (acao === 'iniciar') {
      if (desatualizado(estado) && !estado.app.rodando) { atualizarApp(true); return; }
      enviar({t: 'iniciar'}); abrindoDesde = Date.now(); mostrarAviso('abrindo');
    }
    else if (acao === 'instalar') {
      fetch(PONTE + '/app/instalar', {method: 'POST', body: '{}', targetAddressSpace: 'loopback'}).then(atualizarEstado);
      mostrarAviso('sem_app');
    } else if (acao === 'reconectar') { conectar(); }
  });

  // ── Conexão ──────────────────────────────────────────────────────────────
  function ponte(caminho, opcoes) {
    return fetch(PONTE + caminho, Object.assign({cache: 'no-store', targetAddressSpace: 'loopback'}, opcoes || {}))
      .then(function (r) { if (!r.ok) throw new Error('ponte ' + r.status); return r.json(); });
  }

  function atualizarEstado() {
    return ponte('/estado').then(function (e) {
      estado = e;
      rodape.textContent = 'Ponte ' + e.ponte + (e.pc ? ' · computador ' + e.pc : '') + (e.app.versao ? ' · app ' + e.app.versao : '') +
        (PUBLICADA && e.app.versao && e.app.versao !== PUBLICADA ? ' (publicada: ' + PUBLICADA + ')' : '');
      return e;
    });
  }

  function conectar() {
    clearTimeout(tentando);
    delete raiz.dataset.pnFinal;
    if (!/win/i.test((navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || '')) {
      status('Precisa de Windows', 'aviso'); mostrarAviso('windows'); return;
    }
    status('Procurando a Ponte…', 'neutro');
    mostrarAviso('procurando');
    fetch(raiz.dataset.sessao, {credentials: 'same-origin', cache: 'no-store'})
      .then(function (r) { if (!r.ok) throw new Error('sessao'); return r.json(); })
      .then(function (s) { sessao = s; return atualizarEstado(); })
      .then(function (e) {
        // Pareia (ou repareia) a ponte com este portal: a chave do app vem da própria página.
        return ponte('/parear', {method: 'POST', body: JSON.stringify({chave: sessao.chave})}).then(function () { return e; });
      })
      .then(function (e) {
        if (!e.app.instalado) { status('Ponte conectada', 'ok'); mostrarAviso('sem_app'); vigiarInstalacao(); return; }
        if (desatualizado(e) && !e.app.rodando) { atualizarApp(false); return; }
        abrirWs();
      })
      .catch(function () {
        status('Ponte não encontrada', 'aviso');
        mostrarAviso('sem_ponte');
        tentando = setTimeout(conectar, 4000);
      });
  }

  function vigiarInstalacao() {
    clearTimeout(tentando);
    tentando = setTimeout(function () {
      atualizarEstado().then(function (e) {
        if (e.app.instalado && !(e.app.instalando && e.app.instalando.progresso < 100 && !e.app.instalando.erro)) { abrirWs(); return; }
        mostrarAviso('sem_app');
        vigiarInstalacao();
      }).catch(conectar);
    }, 1200);
  }

  // Instala a publicada pela Ponte e acompanha; no fim conecta (e abre, se a pessoa tinha pedido).
  function atualizarApp(abrir) {
    abrirDepois = abrirDepois || !!abrir;
    if (atualizando) return;
    atualizando = true;
    if (ws) { ws.onclose = null; try { ws.close(); } catch (e) { /* já fechado */ } ws = null; }
    limparCamadas();
    status('Atualizando para a ' + PUBLICADA, 'neutro');
    mostrarAviso('atualizando');
    ponte('/app/instalar', {method: 'POST', body: '{}'}).catch(function () {}).then(vigiarAtualizacao);
  }

  function vigiarAtualizacao() {
    clearTimeout(tentando);
    tentando = setTimeout(function () {
      atualizarEstado().then(function (e) {
        var i = e.app.instalando;
        if (i && !i.erro && (i.progresso || 0) < 100) { mostrarAviso('atualizando'); vigiarAtualizacao(); return; }
        atualizando = false;
        if (i && i.erro) {
          // Não insiste em loop: segue com a instalada e diz o porquê.
          falhouEm = PUBLICADA;
          status('Não atualizou para a ' + PUBLICADA + ': ' + i.erro, 'aviso');
        }
        abrirWs();
      }).catch(function () { atualizando = false; conectar(); });
    }, 1000);
  }

  function abrirWs() {
    if (ws) { try { ws.close(); } catch (e) { /* já fechado */ } }
    ws = new WebSocket(WS + '?token=' + encodeURIComponent(sessao.token));
    ws.binaryType = 'arraybuffer';
    ws.onopen = function () { status('Conectado', 'ok'); };
    ws.onmessage = function (m) { typeof m.data === 'string' ? mensagem(JSON.parse(m.data)) : faixa(m.data); };
    ws.onclose = function () {
      if (raiz.dataset.pnFinal) return;
      status('Desconectado', 'aviso');
      limparCamadas();
      tentando = setTimeout(conectar, 3000);
    };
  }

  function enviar(obj) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj)); }

  function mensagem(m) {
    if (m.t === 'ola') {
      estado = Object.assign(estado || {}, m);
      if (desatualizado(estado) && !estado.app.rodando) atualizarApp(false);
      return;
    }
    if (m.t === 'recusado' || m.t === 'assumido') {
      raiz.dataset.pnFinal = '1';
      status(m.t === 'assumido' ? 'Aberto em outra aba' : 'Recusado', 'erro');
      mostrarAviso('erro', m.erro);
      return;
    }
    if (m.t === 'layout') layout(m);
  }

  // ── Camadas: a janela principal e as perguntas do app por cima ───────────
  function limparCamadas() {
    Object.keys(camadas).forEach(function (id) { camadas[id].el.remove(); });
    camadas = {}; principal = null;
  }

  function layout(m) {
    if (!m.instalado) { mostrarAviso('sem_app'); return; }
    if (!m.aberto) {
      limparCamadas();
      var abrindo = Date.now() - abrindoDesde <= 30000;
      if (abrirDepois && !abrindo) { abrirDepois = false; enviar({t: 'iniciar'}); abrindoDesde = Date.now(); mostrarAviso('abrindo'); return; }
      // Fechou o app desatualizado: atualiza agora (não no meio de um "abrindo").
      if (!abrindo && desatualizado(estado)) {
        atualizarEstado().then(function (e) { if (desatualizado(e) && !e.app.rodando) atualizarApp(false); }).catch(function () {});
      }
      status('App fechado', 'neutro');
      if (!abrindo) mostrarAviso('fechado');
      return;
    }
    abrindoDesde = 0;
    mostrarAviso(null);
    if (desatualizado(estado)) status('Nova versão ' + PUBLICADA + ': feche o app para atualizar', 'aviso');
    else status('App aberto', 'ok');
    var vivos = {};
    m.camadas.forEach(function (c) {
      vivos[c.id] = true;
      var cam = camadas[c.id];
      if (!cam) {
        var canvas = document.createElement('canvas');
        canvas.className = c.tipo === 'principal' ? 'pn-principal' : 'pn-popup';
        canvas.dataset.alvo = c.id;
        palco.appendChild(canvas);
        cam = camadas[c.id] = {el: canvas, ctx: canvas.getContext('2d')};
        ligarEntrada(canvas, c.id);
      }
      cam.info = c;
      if (cam.el.width !== c.l || cam.el.height !== c.a) {
        cam.el.width = c.l; cam.el.height = c.a;
        enviar({t: 'tudo'});
      }
      if (c.tipo === 'principal') {
        if (principal !== cam) { ultimoTamanho = ''; setTimeout(pedirTamanho, 50); }   // app (re)aberto: ajusta ao tamanho daqui
        principal = cam; tituloApp.textContent = c.titulo || 'Automação SAP';
      }
      else { cam.el.title = c.titulo; }
    });
    Object.keys(camadas).forEach(function (id) { if (!vivos[id]) { camadas[id].el.remove(); delete camadas[id]; } });
    posicionar();
    if (!document.activeElement || !raiz.contains(document.activeElement)) focarTeclado();
  }

  function escala() {
    if (!principal) return 1;
    var maxL = tela.clientWidth, maxA = tela.clientHeight;
    return Math.min(1 / dpr, maxL / principal.el.width, maxA / principal.el.height);
  }

  function posicionar() {
    if (!principal) return;
    var s = escala();
    var l = principal.el.width * s, a = principal.el.height * s;
    palco.style.width = l + 'px'; palco.style.height = a + 'px';
    Object.keys(camadas).forEach(function (id) {
      var cam = camadas[id], c = cam.info;
      cam.el.style.width = c.l * s + 'px'; cam.el.style.height = c.a * s + 'px';
      cam.el.style.left = c.x * s + 'px'; cam.el.style.top = c.y * s + 'px';
    });
  }

  function faixa(buffer) {
    var vista = new DataView(buffer);
    var n = vista.getUint32(0);
    var cab = JSON.parse(new TextDecoder().decode(new Uint8Array(buffer, 4, n)));
    var cam = camadas[cab.id];
    if (!cam) return;
    var blob = new Blob([new Uint8Array(buffer, 4 + n)], {type: 'image/png'});
    // Uma faixa de cada vez por camada: decodificar fora de ordem desenharia uma imagem velha por cima da nova.
    filaDesenho[cab.id] = (filaDesenho[cab.id] || Promise.resolve()).then(function () {
      return createImageBitmap(blob).then(function (img) {
        if (camadas[cab.id] === cam) cam.ctx.drawImage(img, 0, cab.y);
        img.close && img.close();
      }).catch(function () {});
    });
  }

  // ── Tamanho: o app acompanha a janela do portal ──────────────────────────
  function pedirTamanho() {
    var l = Math.floor(tela.clientWidth * dpr), a = Math.floor(tela.clientHeight * dpr);
    var chave = l + 'x' + a;
    if (chave === ultimoTamanho || l < 100) return;
    ultimoTamanho = chave;
    enviar({t: 'tamanho', l: l, a: a});
  }
  var esperaTamanho = null;
  new ResizeObserver(function () {
    dpr = window.devicePixelRatio || 1;
    posicionar();
    clearTimeout(esperaTamanho);
    esperaTamanho = setTimeout(pedirTamanho, 250);
  }).observe(tela);
  setInterval(function () { if (principal) pedirTamanho(); }, 2000);

  // ── Mouse ────────────────────────────────────────────────────────────────
  function ponto(canvas, e) {
    var r = canvas.getBoundingClientRect();
    return {x: Math.round((e.clientX - r.left) * canvas.width / r.width), y: Math.round((e.clientY - r.top) * canvas.height / r.height)};
  }
  function mods(e) { return (e.shiftKey ? 1 : 0) | (e.ctrlKey ? 2 : 0) | (e.altKey ? 4 : 0); }

  function ligarEntrada(canvas, id) {
    var ultimoMover = 0;
    canvas.addEventListener('mousedown', function (e) {
      e.preventDefault();
      focarTeclado();
      apertado = true;
      var p = ponto(canvas, e);
      enviar({t: 'mouse', acao: 'descer', alvo: id, x: p.x, y: p.y, botao: e.button === 2 ? 'direito' : 'esquerdo', mods: mods(e)});
    });
    canvas.addEventListener('mouseup', function (e) {
      apertado = false;
      var p = ponto(canvas, e);
      enviar({t: 'mouse', acao: 'subir', alvo: id, x: p.x, y: p.y, botao: e.button === 2 ? 'direito' : 'esquerdo', mods: mods(e)});
    });
    canvas.addEventListener('mousemove', function (e) {
      var agora = performance.now();
      if (agora - ultimoMover < 33) return;
      ultimoMover = agora;
      var p = ponto(canvas, e);
      enviar({t: 'mouse', acao: 'mover', alvo: id, x: p.x, y: p.y, apertado: apertado, mods: mods(e)});
    });
    canvas.addEventListener('wheel', function (e) {
      e.preventDefault();
      var p = ponto(canvas, e);
      var passos = Math.max(-5, Math.min(5, Math.round(-e.deltaY / (e.deltaMode ? 1 : 100)) || (e.deltaY > 0 ? -1 : 1)));
      enviar({t: 'roda', alvo: id, x: p.x, y: p.y, delta: passos * 120});
    }, {passive: false});
    canvas.addEventListener('contextmenu', function (e) { e.preventDefault(); });
  }
  window.addEventListener('mouseup', function () { apertado = false; });

  // ── Teclado: vai para a pergunta aberta (se houver) ou para o app ───────
  function alvoTeclado() {
    var ids = Object.keys(camadas).filter(function (id) { return camadas[id] !== principal; });
    return ids.length ? Number(ids[ids.length - 1]) : (principal ? Number(principal.el.dataset.alvo) : 0);
  }
  var ESPECIAIS = /^(Enter|Tab|Escape|Backspace|Delete|Insert|Arrow\w+|Home|End|PageUp|PageDown|F\d{1,2})$/;
  var compondo = false;
  teclado.addEventListener('keydown', function (e) {
    if (!principal || compondo || e.isComposing) return;
    if (e.key === 'F11' || (e.key === 'F5' && !e.ctrlKey)) return;      // deixa o navegador
    var alvo = alvoTeclado();
    var atalho = (e.ctrlKey || e.altKey) && !(e.ctrlKey && e.altKey);     // AltGr (Ctrl+Alt) digita @, €…
    if (ESPECIAIS.test(e.key) || (atalho && e.key.length === 1)) {
      e.preventDefault();
      enviar({t: 'tecla', alvo: alvo, key: e.key, code: e.code, mods: mods(e)});
    } else if (e.key.length === 1) {
      e.preventDefault();
      enviar({t: 'texto', alvo: alvo, texto: e.key});
    }
  });
  // Texto que entrou sem passar pelo keydown acima (composição de acento, emoji, ditado, inserção).
  teclado.addEventListener('compositionstart', function () { compondo = true; });
  teclado.addEventListener('compositionend', function () { compondo = false; despejar(); });
  teclado.addEventListener('input', function () { if (!compondo) despejar(); });
  function despejar() {
    var texto = teclado.value;
    teclado.value = '';
    if (texto && principal) enviar({t: 'texto', alvo: alvoTeclado(), texto: texto});
  }
  teclado.addEventListener('paste', function (e) {
    var texto = (e.clipboardData || window.clipboardData).getData('text');
    if (texto && principal) { e.preventDefault(); enviar({t: 'texto', alvo: alvoTeclado(), texto: texto}); }
  });
  teclado.addEventListener('focus', function () { raiz.classList.add('pn-com-teclado'); });
  teclado.addEventListener('blur', function () { raiz.classList.remove('pn-com-teclado'); });

  // ── Barra de título ──────────────────────────────────────────────────────
  raiz.querySelector('[data-pn-cheia]').addEventListener('click', function () {
    if (document.fullscreenElement) document.exitFullscreen(); else raiz.requestFullscreen().catch(function () {});
  });
  document.addEventListener('fullscreenchange', function () { raiz.classList.toggle('pn-cheia', document.fullscreenElement === raiz); });
  raiz.querySelector('[data-pn-reiniciar]').addEventListener('click', function () {
    if (confirm('Reiniciar a Automação SAP? Se ela estiver gravando algo no SAP, espere terminar.')) {
      enviar({t: 'reiniciar'}); abrindoDesde = Date.now(); mostrarAviso('abrindo');
    }
  });
  raiz.querySelector('[data-pn-windows]').addEventListener('click', function () {
    if (ws) { ws.onclose = null; ws.close(); ws = null; }
    limparCamadas();
    ponte('/app/mostrar', {method: 'POST', body: '{}'}).catch(function () {});
    status('No Windows', 'neutro');
    mostrarAviso('erro', 'O app foi para a área de trabalho do Windows.');
  });

  conectar();
})();
