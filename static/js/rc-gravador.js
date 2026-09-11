/*
 * RCGravador — gravação de áudio do portal que não se perde.
 *
 * Usado pelo gravador da agenda (/agenda/transcricoes/nova/ e a janela
 * /agenda/transcricoes/gravador/) e pela sala de reunião. As proteções, cada
 * uma independente das outras:
 *
 * 1. Cada pedaço gravado (30 s) vai para o IndexedDB ANTES de subir, e só sai
 *    de lá quando o servidor confirma. Aba fechada, navegador que travou,
 *    internet que caiu: o que foi gravado continua neste computador e é
 *    oferecido para envio na próxima vez que a pessoa abrir a tela.
 * 2. A sessão de gravação é aberta no servidor antes do primeiro pedaço: se o
 *    navegador sumir de vez, o portal fecha a gravação sozinho e processa o que
 *    chegou.
 * 3. O envio tenta de novo para sempre, com espera crescente; sessão expirada
 *    (resposta 401, redirecionamento para o login, página no lugar de JSON)
 *    nunca é contada como pedaço salvo — pausa e retoma quando a pessoa entra
 *    de novo.
 * 4. Outras janelas acompanham e comandam a gravação pelo BroadcastChannel
 *    (com evento de localStorage como plano B).
 *
 * Sem dependências e sem módulos ES: expõe window.RCGravador.
 */
(function (global) {
    'use strict';

    var DB_NOME = 'rc-gravador';
    var DB_VERSAO = 1;
    var CANAL = 'rc-gravador';
    var CHAVE_CANAL_LOCAL = 'rc-gravador:msg';
    var TIMESLICE_PADRAO = 30000;
    var ENVIO_TIMEOUT_MS = 60000;
    var ESPERA_MAXIMA_MS = 30000;
    var MIMES = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus', 'audio/mp4'];

    // ------------------------------------------------------------------
    // Utilidades
    // ------------------------------------------------------------------
    function esperar(ms) {
        return new Promise(function (resolve) { setTimeout(resolve, ms); });
    }

    function backoff(tentativas) {
        return Math.min(ESPERA_MAXIMA_MS, 1500 * Math.pow(1.6, Math.min(tentativas, 8)));
    }

    function lerCookie(nome) {
        var m = document.cookie.match(new RegExp('(?:^|; )' + nome + '=([^;]*)'));
        return m ? decodeURIComponent(m[1]) : '';
    }

    // O token do CSRF muda quando a pessoa entra de novo no portal (sessão que
    // expirou no meio da gravação). Lido do cookie a cada envio, o gravador
    // continua funcionando depois do novo login sem recarregar a página.
    function csrfAtual(padrao) {
        return lerCookie('csrftoken') || padrao || '';
    }

    function novoUploadId() {
        if (global.crypto && typeof global.crypto.randomUUID === 'function') {
            return global.crypto.randomUUID();
        }
        return 'grav_' + Date.now().toString(36) + '_' + Math.random().toString(36).slice(2, 12);
    }

    function escolherMime() {
        if (typeof MediaRecorder === 'undefined' || !MediaRecorder.isTypeSupported) return '';
        for (var i = 0; i < MIMES.length; i++) {
            if (MediaRecorder.isTypeSupported(MIMES[i])) return MIMES[i];
        }
        return '';
    }

    function extensaoDoMime(mime) {
        mime = (mime || '').toLowerCase();
        if (mime.indexOf('mp4') !== -1 || mime.indexOf('aac') !== -1) return '.mp4';
        if (mime.indexOf('ogg') !== -1) return '.ogg';
        return '.webm';
    }

    function formatarTempo(segundos) {
        segundos = Math.max(0, Math.floor(segundos || 0));
        var h = Math.floor(segundos / 3600);
        var m = String(Math.floor((segundos % 3600) / 60)).padStart(2, '0');
        var s = String(segundos % 60).padStart(2, '0');
        return h > 0 ? h + ':' + m + ':' + s : m + ':' + s;
    }

    function erroAmigavel(mensagem) {
        var e = new Error(mensagem);
        e.amigavel = true;
        return e;
    }

    function mensagemDeMidia(e) {
        if (e && e.amigavel) return e.message;
        var nome = e && e.name;
        if (nome === 'NotAllowedError' || nome === 'SecurityError') {
            return 'O navegador não liberou o microfone. Clique no cadeado da barra de endereço, permita o microfone e tente de novo.';
        }
        if (nome === 'NotFoundError' || nome === 'OverconstrainedError') {
            return 'Nenhum microfone encontrado. Conecte um microfone e tente de novo.';
        }
        if (nome === 'NotReadableError') {
            return 'O microfone está sendo usado por outro programa. Feche o outro programa e tente de novo.';
        }
        return 'Não foi possível começar a gravar: ' + ((e && e.message) || e);
    }

    async function lerJson(resp) {
        var tipo = (resp.headers && resp.headers.get('content-type')) || '';
        if (tipo.indexOf('application/json') === -1) return null;
        try { return await resp.json(); } catch (e) { return null; }
    }

    // ------------------------------------------------------------------
    // IndexedDB — sessões e partes guardadas neste computador
    // ------------------------------------------------------------------
    var bancoPromessa = null;

    function abrirBanco() {
        if (bancoPromessa) return bancoPromessa;
        bancoPromessa = new Promise(function (resolve) {
            if (typeof indexedDB === 'undefined') { resolve(null); return; }
            var pedido;
            try {
                pedido = indexedDB.open(DB_NOME, DB_VERSAO);
            } catch (e) {
                resolve(null);
                return;
            }
            pedido.onupgradeneeded = function () {
                var db = pedido.result;
                if (!db.objectStoreNames.contains('sessoes')) {
                    db.createObjectStore('sessoes', { keyPath: 'uploadId' });
                }
                if (!db.objectStoreNames.contains('partes')) {
                    var partes = db.createObjectStore('partes', { keyPath: ['uploadId', 'indice'] });
                    partes.createIndex('porSessao', 'uploadId', { unique: false });
                }
            };
            pedido.onsuccess = function () { resolve(pedido.result); };
            // Modo anônimo de alguns navegadores, cota cheia, IndexedDB desligado:
            // grava só em memória e avisa na tela que não há cópia local.
            pedido.onerror = function () { resolve(null); };
            pedido.onblocked = function () { resolve(null); };
        });
        return bancoPromessa;
    }

    function pedidoParaPromessa(pedido) {
        return new Promise(function (resolve, reject) {
            pedido.onsuccess = function () { resolve(pedido.result); };
            pedido.onerror = function () { reject(pedido.error); };
        });
    }

    function transacaoConcluida(tx) {
        return new Promise(function (resolve, reject) {
            tx.oncomplete = function () { resolve(true); };
            tx.onerror = function () { reject(tx.error); };
            tx.onabort = function () { reject(tx.error || new Error('transação abortada')); };
        });
    }

    async function guardarSessao(sessao) {
        var db = await abrirBanco();
        if (!db) return false;
        try {
            var tx = db.transaction('sessoes', 'readwrite');
            tx.objectStore('sessoes').put(Object.assign({}, sessao, { atualizadoEm: Date.now() }));
            return await transacaoConcluida(tx);
        } catch (e) {
            return false;
        }
    }

    async function lerSessao(uploadId) {
        var db = await abrirBanco();
        if (!db) return null;
        try {
            return (await pedidoParaPromessa(db.transaction('sessoes').objectStore('sessoes').get(uploadId))) || null;
        } catch (e) {
            return null;
        }
    }

    async function listarSessoes() {
        var db = await abrirBanco();
        if (!db) return [];
        try {
            return (await pedidoParaPromessa(db.transaction('sessoes').objectStore('sessoes').getAll())) || [];
        } catch (e) {
            return [];
        }
    }

    async function guardarParte(uploadId, indice, blob) {
        var db = await abrirBanco();
        if (!db) return false;
        try {
            var tx = db.transaction('partes', 'readwrite');
            tx.objectStore('partes').put({
                uploadId: uploadId, indice: indice, blob: blob, tamanho: blob.size,
                enviada: false, criadaEm: Date.now(),
            });
            return await transacaoConcluida(tx);
        } catch (e) {
            return false;
        }
    }

    async function lerParte(uploadId, indice) {
        var db = await abrirBanco();
        if (!db) return null;
        try {
            return (await pedidoParaPromessa(db.transaction('partes').objectStore('partes').get([uploadId, indice]))) || null;
        } catch (e) {
            return null;
        }
    }

    async function marcarEnviada(uploadId, indice) {
        var db = await abrirBanco();
        if (!db) return false;
        try {
            var tx = db.transaction('partes', 'readwrite');
            var loja = tx.objectStore('partes');
            var registro = await pedidoParaPromessa(loja.get([uploadId, indice]));
            if (registro) {
                // O registro fica (sem o áudio) para a conta de partes geradas.
                registro.enviada = true;
                registro.blob = null;
                loja.put(registro);
            }
            return await transacaoConcluida(tx);
        } catch (e) {
            return false;
        }
    }

    async function partesDaSessao(uploadId) {
        var db = await abrirBanco();
        if (!db) return [];
        try {
            var indice = db.transaction('partes').objectStore('partes').index('porSessao');
            var partes = (await pedidoParaPromessa(indice.getAll(uploadId))) || [];
            return partes.sort(function (a, b) { return a.indice - b.indice; });
        } catch (e) {
            return [];
        }
    }

    async function apagarSessao(uploadId) {
        var db = await abrirBanco();
        if (!db) return false;
        try {
            var tx = db.transaction(['sessoes', 'partes'], 'readwrite');
            tx.objectStore('sessoes').delete(uploadId);
            var loja = tx.objectStore('partes');
            var chaves = await pedidoParaPromessa(loja.index('porSessao').getAllKeys(uploadId));
            (chaves || []).forEach(function (chave) { loja.delete(chave); });
            return await transacaoConcluida(tx);
        } catch (e) {
            return false;
        }
    }

    // ------------------------------------------------------------------
    // Rede
    // ------------------------------------------------------------------
    async function enviarParte(url, csrf, sessao, indice, blob, extensao) {
        var dados = new FormData();
        dados.append('upload_id', sessao.uploadId);
        dados.append('chunk_index', String(indice));
        dados.append('total_chunks', '0');
        dados.append('origem', sessao.origem || 'gravador');
        // Título e evento vão junto: se a sessão no servidor ainda não existir
        // (o "iniciar" falhou ou a sessão vazia foi limpa), a parte a recria.
        if (sessao.titulo) dados.append('title', sessao.titulo);
        if (sessao.eventId) dados.append('event_id', String(sessao.eventId));
        dados.append('audio', blob, 'parte-' + indice + (extensao || '.webm'));

        var controle = typeof AbortController !== 'undefined' ? new AbortController() : null;
        var relogio = controle ? setTimeout(function () { controle.abort(); }, ENVIO_TIMEOUT_MS) : null;
        try {
            var resp = await fetch(url, {
                method: 'POST',
                body: dados,
                credentials: 'same-origin',
                redirect: 'manual',
                headers: { 'X-CSRFToken': csrfAtual(csrf), 'X-Requested-With': 'XMLHttpRequest' },
                signal: controle ? controle.signal : undefined,
            });
            var json = await lerJson(resp);
            if (resp.type === 'opaqueredirect' || resp.status === 401 || (json && json.sessao_expirada)) {
                return { ok: false, sessaoExpirada: true,
                         mensagem: (json && json.error) || 'Sua sessão no portal expirou. Entre de novo — a gravação continua guardada neste computador.' };
            }
            if (resp.status === 403) {
                if (json && json.error) return { ok: false, proibido: true, mensagem: json.error };
                // 403 sem JSON é o CSRF recusado (novo login em outra aba): tenta de novo depois.
                return { ok: false, sessaoExpirada: true, mensagem: 'O portal pediu para entrar de novo. A gravação continua guardada neste computador.' };
            }
            if (resp.ok && json && json.received === true) {
                return { ok: true, transcricaoId: json.transcription_id || null };
            }
            if (resp.ok && !json) {
                return { ok: false, sessaoExpirada: true,
                         mensagem: 'O portal respondeu com uma página em vez de confirmar a parte (sessão expirada?).' };
            }
            return { ok: false, mensagem: (json && json.error) || ('O servidor respondeu ' + resp.status + '.') };
        } catch (e) {
            return { ok: false, mensagem: e && e.name === 'AbortError' ? 'O envio demorou demais.' : 'Sem conexão com o portal.' };
        } finally {
            if (relogio) clearTimeout(relogio);
        }
    }

    async function postarFormulario(url, csrf, campos) {
        var dados = new FormData();
        Object.keys(campos).forEach(function (chave) {
            var valor = campos[chave];
            if (valor === null || valor === undefined || valor === '') return;
            dados.append(chave, String(valor));
        });
        try {
            var resp = await fetch(url, {
                method: 'POST', body: dados, credentials: 'same-origin', redirect: 'manual',
                headers: { 'X-CSRFToken': csrfAtual(csrf), 'X-Requested-With': 'XMLHttpRequest' },
            });
            var json = await lerJson(resp);
            if (resp.type === 'opaqueredirect' || resp.status === 401 || (json && json.sessao_expirada)) {
                return { ok: false, sessaoExpirada: true, status: resp.status,
                         mensagem: (json && json.error) || 'Sua sessão no portal expirou. Entre de novo e tente outra vez.' };
            }
            if (!json) {
                return { ok: false, status: resp.status, mensagem: 'O portal respondeu ' + resp.status + ' sem os dados esperados.' };
            }
            if (!resp.ok) return { ok: false, status: resp.status, mensagem: json.error || ('O portal respondeu ' + resp.status + '.'), dados: json };
            return { ok: true, status: resp.status, dados: json };
        } catch (e) {
            return { ok: false, status: 0, mensagem: 'Sem conexão com o portal.' };
        }
    }

    // ------------------------------------------------------------------
    // Canal entre janelas
    // ------------------------------------------------------------------
    var canalUnico = null;

    function Canal() {
        var self = this;
        this.ouvintes = [];
        if (typeof BroadcastChannel !== 'undefined') {
            try {
                this.bc = new BroadcastChannel(CANAL);
                this.bc.onmessage = function (ev) { self._entregar(ev.data); };
            } catch (e) {
                this.bc = null;
            }
        }
        if (!this.bc) {
            global.addEventListener('storage', function (ev) {
                if (ev.key !== CHAVE_CANAL_LOCAL || !ev.newValue) return;
                try { self._entregar(JSON.parse(ev.newValue)); } catch (e) { /* mensagem quebrada */ }
            });
        }
    }

    Canal.prototype.enviar = function (mensagem) {
        if (this.bc) {
            try { this.bc.postMessage(mensagem); } catch (e) { /* janela fechando */ }
            return;
        }
        try {
            localStorage.setItem(CHAVE_CANAL_LOCAL, JSON.stringify(Object.assign({ _n: Date.now() + Math.random() }, mensagem)));
        } catch (e) { /* sem storage */ }
    };

    Canal.prototype._entregar = function (mensagem) {
        if (!mensagem || typeof mensagem !== 'object') return;
        this.ouvintes.slice().forEach(function (ouvinte) {
            try { ouvinte(mensagem); } catch (e) { /* um ouvinte não derruba os outros */ }
        });
    };

    Canal.prototype.ouvir = function (callback) {
        var self = this;
        this.ouvintes.push(callback);
        return function () { self.ouvintes = self.ouvintes.filter(function (o) { return o !== callback; }); };
    };

    function canal() {
        if (!canalUnico) canalUnico = new Canal();
        return canalUnico;
    }

    // ------------------------------------------------------------------
    // O gravador
    // ------------------------------------------------------------------
    function RCGravador(opcoes) {
        opcoes = opcoes || {};
        this.csrf = opcoes.csrf || '';
        this.urls = opcoes.urls || {};
        this.sessao = Object.assign({ uploadId: '', titulo: '', eventId: '', origem: 'gravador', papeis: [] }, opcoes.sessao || {});
        if (!this.sessao.uploadId) this.sessao.uploadId = novoUploadId();
        this.fonte = opcoes.fonte || 'mic';
        this.timesliceMs = opcoes.timesliceMs || TIMESLICE_PADRAO;
        this.onEstado = opcoes.onEstado || function () {};
        this.onErro = opcoes.onErro || function () {};
        this.onComando = opcoes.onComando || null;

        this._fase = 'ocioso';
        this._streams = [];
        this._contexto = null;
        this._recorder = null;
        this._iniciadoEm = 0;
        this._paradoEm = 0;
        this._indice = 0;
        this._fila = new Map();
        this._enviando = false;
        this._pausado = false;
        this._ultimoErro = '';
        this._semLocal = false;
        this._gravacoesLocais = Promise.resolve();
        this._transcricaoId = null;
        this._redirect = '';
        this._mime = '';
        this._extensao = '.webm';
        this._avisouSegundoPlano = false;
        this._wakeLock = null;
        this._desligarCanal = null;
        this._ligarCanal();
    }

    RCGravador.prototype.gravando = function () {
        return !!(this._recorder && this._recorder.state === 'recording');
    };

    RCGravador.prototype.segundos = function () {
        if (!this._iniciadoEm) return 0;
        return Math.round(((this._paradoEm || Date.now()) - this._iniciadoEm) / 1000);
    };

    RCGravador.prototype._pendentes = function () {
        var n = 0;
        this._fila.forEach(function (registro) { if (registro.status !== 'enviada') n += 1; });
        return n;
    };

    RCGravador.prototype.estado = function () {
        var geradas = this._indice, enviadas = 0, pendentes = 0, falhas = 0, locais = 0;
        this._fila.forEach(function (r) {
            if (r.status === 'enviada') {
                enviadas += 1;
            } else {
                pendentes += 1;
                if (r.status === 'falhou') falhas += 1;
                if (r.local) locais += 1;
            }
        });
        return {
            fase: this._fase,
            segundos: this.segundos(),
            tempo: formatarTempo(this.segundos()),
            partes: { geradas: geradas, enviadas: enviadas, pendentes: pendentes, locais: locais, falhas: falhas },
            ultimoErroEnvio: this._ultimoErro,
            semArmazenamentoLocal: this._semLocal,
            online: typeof navigator.onLine === 'boolean' ? navigator.onLine : true,
            pausado: this._pausado,
            transcricaoId: this._transcricaoId,
            redirect: this._redirect,
            uploadId: this.sessao.uploadId,
            titulo: this.sessao.titulo,
            origem: this.sessao.origem,
            eventId: this.sessao.eventId,
            iniciadoEm: this._iniciadoEm,
            mime: this._mime,
            fonte: this.fonte,
        };
    };

    RCGravador.prototype._emitir = function () {
        try { this.onEstado(this.estado()); } catch (e) { /* a tela não derruba a gravação */ }
    };

    RCGravador.prototype._publicar = function (tipo, extra) {
        canal().enviar(Object.assign({ tipo: tipo, uploadId: this.sessao.uploadId, estado: this.estado() }, extra || {}));
    };

    RCGravador.prototype._mudar = function (fase) {
        this._fase = fase;
        this._emitir();
        this._publicar('estado');
    };

    RCGravador.prototype._ligarCanal = function () {
        var self = this;
        this._desligarCanal = canal().ouvir(function (msg) {
            if (msg.uploadId !== self.sessao.uploadId && msg.uploadId !== '*') return;
            if (msg.tipo === 'ping') {
                if (self._fase !== 'ocioso' && self._fase !== 'concluido') self._publicar('pong');
            } else if (msg.tipo === 'comando') {
                if (self.onComando) {
                    self.onComando(msg.comando, msg.dados || {});
                } else if (msg.comando === 'parar_e_processar') {
                    self.finalizar(Object.assign({ forcar: false }, msg.dados || {})).catch(function (e) {
                        self.onErro(e.message || String(e), e);
                    });
                } else if (msg.comando === 'focar') {
                    try { global.focus(); } catch (e) { /* o navegador pode não deixar */ }
                }
            }
        });
    };

    RCGravador.prototype._montarStream = async function () {
        var md = navigator.mediaDevices;
        if (!md || !md.getUserMedia || typeof MediaRecorder === 'undefined') {
            throw erroAmigavel('Este navegador não grava áudio. Use o Chrome ou o Edge atualizado.');
        }
        var comMicrofone = { audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } };

        if (this.fonte === 'mic') {
            var somenteMic = await md.getUserMedia(comMicrofone);
            this._streams.push(somenteMic);
            this._faixaMic = somenteMic.getAudioTracks()[0] || null;
            return somenteMic;
        }

        if (!md.getDisplayMedia) {
            throw erroAmigavel('Este navegador não captura o áudio do computador. Use o Chrome ou o Edge atualizado — ou grave só o microfone.');
        }
        var tela;
        try {
            tela = await md.getDisplayMedia({
                video: { width: { ideal: 320 }, height: { ideal: 180 }, frameRate: { ideal: 1, max: 5 } },
                audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
                systemAudio: 'include',
                selfBrowserSurface: 'include',
                surfaceSwitching: 'include',
                preferCurrentTab: this.fonte === 'aba-mic',
            });
        } catch (e) {
            if (e && e.name === 'NotAllowedError') {
                throw erroAmigavel('Compartilhamento cancelado. Para gravar o áudio da reunião, escolha a aba ou janela e marque "Compartilhar áudio".');
            }
            throw e;
        }
        this._streams.push(tela);
        var faixas = tela.getAudioTracks();
        if (!faixas.length) {
            this._pararStreams();
            throw erroAmigavel('Você compartilhou sem o áudio. Tente de novo e marque "Compartilhar áudio" (de preferência compartilhando a aba).');
        }
        // A faixa de vídeo fica viva, mas desligada: encerrá-la derruba a captura
        // em alguns navegadores, e vídeo aqui só gastaria banda.
        tela.getVideoTracks().forEach(function (faixa) { faixa.enabled = false; });
        this._faixaSistema = faixas[0];
        if (this.fonte === 'system') return new MediaStream(faixas);

        var mic;
        try {
            mic = await md.getUserMedia(comMicrofone);
        } catch (e) {
            this._pararStreams();
            throw erroAmigavel('Não foi possível usar o microfone: ' + ((e && e.message) || e));
        }
        this._streams.push(mic);
        this._faixaMic = mic.getAudioTracks()[0] || null;
        var Ctx = global.AudioContext || global.webkitAudioContext;
        if (!Ctx) {
            this._pararStreams();
            throw erroAmigavel('Este navegador não consegue juntar o microfone com o áudio do computador.');
        }
        this._contexto = new Ctx();
        var destino = this._contexto.createMediaStreamDestination();
        this._contexto.createMediaStreamSource(mic).connect(destino);
        this._contexto.createMediaStreamSource(new MediaStream(faixas)).connect(destino);
        return destino.stream;
    };

    RCGravador.prototype._pararStreams = function () {
        this._streams.forEach(function (s) { s.getTracks().forEach(function (t) { try { t.stop(); } catch (e) {} }); });
        this._streams = [];
        if (this._contexto) {
            try { this._contexto.close(); } catch (e) { /* já fechado */ }
            this._contexto = null;
        }
    };

    RCGravador.prototype.iniciar = async function () {
        if (this._fase === 'gravando' || this._fase === 'pedindo-permissao') return false;
        this._mudar('pedindo-permissao');
        var stream;
        try {
            stream = await this._montarStream();
        } catch (e) {
            this._pararStreams();
            this._mudar('ocioso');
            this.onErro(mensagemDeMidia(e), e);
            return false;
        }

        this._mime = escolherMime();
        try {
            this._recorder = this._mime ? new MediaRecorder(stream, { mimeType: this._mime }) : new MediaRecorder(stream);
        } catch (e) {
            this._recorder = new MediaRecorder(stream);
        }
        this._mime = this._recorder.mimeType || this._mime || 'audio/webm';
        this._extensao = extensaoDoMime(this._mime);
        this._iniciadoEm = Date.now();
        this._paradoEm = 0;
        this._indice = 0;
        this._fila = new Map();

        var self = this;
        this._recorder.ondataavailable = function (ev) { self._aoReceberDados(ev); };
        this._recorder.onerror = function (ev) {
            self.onErro('A gravação foi interrompida pelo navegador. O que foi gravado até aqui está salvo.', ev && ev.error);
            self.parar();
        };
        [this._faixaMic, this._faixaSistema].forEach(function (faixa) {
            if (faixa) faixa.addEventListener('ended', function () { self._aoFaixaAcabar(faixa); });
        });

        await this._salvarSessaoLocal();
        this._abrirSessaoNoServidor();
        this._recorder.start(this.timesliceMs);
        this._mudar('gravando');
        this._pedirWakeLock();
        this._pedirPermissaoDeAviso();
        this._ligarEventosDePagina();
        clearInterval(this._tique);
        this._tique = setInterval(function () { self._emitir(); }, 1000);
        clearInterval(this._batimento);
        this._batimento = setInterval(function () {
            self._publicar('estado');
            self._salvarSessaoLocal();
        }, 5000);
        return true;
    };

    RCGravador.prototype._aoFaixaAcabar = function (faixa) {
        if (!this.gravando()) return;
        if (faixa === this._faixaSistema) {
            this.onErro('O compartilhamento do áudio foi encerrado. A gravação parou e o que foi gravado está salvo.');
            this.parar();
        } else if (this.fonte === 'both' || this.fonte === 'aba-mic') {
            this.onErro('O microfone desconectou. A gravação continua só com o áudio do computador.');
        } else {
            this.onErro('O microfone desconectou. A gravação parou e o que foi gravado está salvo.');
            this.parar();
        }
    };

    RCGravador.prototype._aoReceberDados = function (ev) {
        if (!ev || !ev.data || !ev.data.size) return;
        var self = this;
        var indice = this._indice;
        this._indice += 1;
        var registro = { blob: ev.data, status: 'pendente', tentativas: 0, local: false };
        this._fila.set(indice, registro);
        // A parte vai para o IndexedDB antes de subir: se a aba fechar agora, fica.
        this._gravacoesLocais = this._gravacoesLocais.then(function () {
            return guardarParte(self.sessao.uploadId, indice, ev.data).then(function (salvou) {
                if (salvou) {
                    registro.local = true;
                    // Em disco, não precisa ficar na memória: oito horas offline
                    // não viram oito horas de áudio na RAM.
                    if (registro.status !== 'enviando') registro.blob = null;
                } else {
                    self._semLocal = true;
                }
            });
        });
        this._salvarSessaoLocal();
        this._emitir();
        this._acordarEnvio();
    };

    RCGravador.prototype._proximaPendente = function () {
        var escolhido = null;
        this._fila.forEach(function (registro, indice) {
            if ((registro.status === 'pendente' || registro.status === 'falhou')
                    && (escolhido === null || indice < escolhido)) {
                escolhido = indice;
            }
        });
        return escolhido;
    };

    RCGravador.prototype._acordarEnvio = function () {
        if (this._enviando || this._pausado) return;
        this._enviando = true;
        var self = this;
        (async function () {
            try {
                while (!self._pausado) {
                    var indice = self._proximaPendente();
                    if (indice === null) break;
                    await self._enviarParte(indice, self._fila.get(indice));
                }
            } finally {
                self._enviando = false;
                self._emitir();
            }
        })();
    };

    RCGravador.prototype._enviarParte = async function (indice, registro) {
        await this._gravacoesLocais;
        registro.status = 'enviando';
        registro.tentativas += 1;
        this._emitir();

        var blob = registro.blob;
        if (!blob) {
            var guardada = await lerParte(this.sessao.uploadId, indice);
            blob = guardada && guardada.blob;
        }
        if (!blob) {
            // Sumiu da memória e do disco (limpeza do navegador): não há o que enviar.
            registro.status = 'enviada';
            this._ultimoErro = 'Uma parte da gravação foi apagada pelo navegador antes de subir.';
            this._emitir();
            return;
        }

        var r = await enviarParte(this.urls.chunk, this.csrf, this.sessao, indice, blob, this._extensao);
        if (r.ok) {
            registro.status = 'enviada';
            registro.blob = null;
            this._ultimoErro = '';
            if (r.transcricaoId) this._transcricaoId = r.transcricaoId;
            await marcarEnviada(this.sessao.uploadId, indice);
            this._salvarSessaoLocal();
            this._emitir();
            return;
        }

        registro.status = 'falhou';
        if (registro.local) registro.blob = null;
        this._ultimoErro = r.mensagem;
        if (r.sessaoExpirada || r.proibido) {
            this._pausado = true;
            this.onErro(r.mensagem, r);
            this._emitir();
            this._agendarRetomada(r.proibido ? 0 : 60000);
            return;
        }
        this._emitir();
        await this._esperarRedeOu(backoff(registro.tentativas));
    };

    RCGravador.prototype._esperarRedeOu = function (ms) {
        return new Promise(function (resolve) {
            var feito = false;
            function fim() {
                if (feito) return;
                feito = true;
                global.removeEventListener('online', fim);
                resolve();
            }
            global.addEventListener('online', fim);
            setTimeout(fim, ms);
        });
    };

    RCGravador.prototype._agendarRetomada = function (ms) {
        if (!ms) return;
        var self = this;
        clearTimeout(this._retomada);
        this._retomada = setTimeout(function () {
            if (!self._pausado) return;
            self._pausado = false;
            self._acordarEnvio();
        }, ms);
    };

    RCGravador.prototype._abrirSessaoNoServidor = async function () {
        if (!this.urls.iniciar) return;
        var self = this;
        var r = await postarFormulario(this.urls.iniciar, this.csrf, {
            upload_id: this.sessao.uploadId,
            title: this.sessao.titulo,
            origem: this.sessao.origem,
            event_id: this.sessao.eventId,
            participant_roles: JSON.stringify(this.sessao.papeis || []),
        });
        if (r.ok && r.dados && r.dados.id) {
            this._transcricaoId = r.dados.id;
            this._emitir();
            return;
        }
        if (r.status === 403) {
            this.onErro(r.mensagem, r);
            return;
        }
        // Sem rede ou sessão expirada agora: a primeira parte abre a sessão de
        // qualquer jeito, e tentamos de novo daqui a pouco.
        clearTimeout(this._reabrir);
        this._reabrir = setTimeout(function () {
            if (!self._transcricaoId && (self.gravando() || self._pendentes())) self._abrirSessaoNoServidor();
        }, 30000);
    };

    RCGravador.prototype._salvarSessaoLocal = function (extra) {
        var sessao = Object.assign({
            uploadId: this.sessao.uploadId,
            titulo: this.sessao.titulo,
            origem: this.sessao.origem,
            eventId: this.sessao.eventId,
            papeis: this.sessao.papeis || [],
            fonte: this.fonte,
            mime: this._mime,
            extensao: this._extensao,
            iniciadoEm: this._iniciadoEm,
            segundos: this.segundos(),
            partesGeradas: this._indice,
            transcricaoId: this._transcricaoId,
            gravando: this.gravando(),
            pagina: global.location ? global.location.pathname : '',
        }, extra || {});
        this._sessaoLocal = Object.assign(this._sessaoLocal || {}, sessao);
        return guardarSessao(this._sessaoLocal);
    };

    RCGravador.prototype._pedirWakeLock = async function () {
        if (!('wakeLock' in navigator) || document.visibilityState !== 'visible') return;
        try {
            this._wakeLock = await navigator.wakeLock.request('screen');
        } catch (e) { /* navegador sem suporte ou bateria fraca */ }
    };

    RCGravador.prototype._soltarWakeLock = function () {
        if (this._wakeLock) {
            try { this._wakeLock.release(); } catch (e) {}
            this._wakeLock = null;
        }
    };

    RCGravador.prototype._pedirPermissaoDeAviso = function () {
        if (!('Notification' in global) || Notification.permission !== 'default') return;
        try { Notification.requestPermission(); } catch (e) { /* sem suporte */ }
    };

    RCGravador.prototype._avisarSegundoPlano = function () {
        if (this._avisouSegundoPlano || !('Notification' in global) || Notification.permission !== 'granted') return;
        this._avisouSegundoPlano = true;
        try {
            new Notification('A gravação continua', {
                body: 'Pode usar outras abas e programas. Volte ao portal para encerrar.',
                icon: '/static/images/logo3.png',
                tag: 'rc-gravador-' + this.sessao.uploadId,
            });
        } catch (e) { /* alguns navegadores só notificam pelo service worker */ }
    };

    RCGravador.prototype._ligarEventosDePagina = function () {
        if (this._eventosLigados) return;
        this._eventosLigados = true;
        var self = this;

        function pedirDados() {
            if (self._recorder && self._recorder.state === 'recording') {
                try { self._recorder.requestData(); } catch (e) {}
            }
        }

        document.addEventListener('visibilitychange', function () {
            if (document.visibilityState === 'hidden') {
                if (self.gravando()) {
                    // Manda o que está no buffer para o disco agora: se o navegador
                    // descartar a aba em segundo plano, perde-se no máximo segundos.
                    pedirDados();
                    self._avisarSegundoPlano();
                }
            } else {
                if (self.gravando()) {
                    self._pedirWakeLock();
                    if (self._contexto && self._contexto.state === 'suspended') {
                        self._contexto.resume().catch(function () {});
                    }
                }
                self._pausado = false;
                self._acordarEnvio();
                self._emitir();
            }
        });
        document.addEventListener('freeze', pedirDados);
        global.addEventListener('pagehide', function () {
            pedirDados();
            self._salvarSessaoLocal();
        });
        global.addEventListener('beforeunload', function (ev) {
            if (self.gravando() || self._pendentes() > 0) {
                pedirDados();
                ev.preventDefault();
                ev.returnValue = '';
                return '';
            }
            return undefined;
        });
        function voltou() {
            self._pausado = false;
            self._acordarEnvio();
            self._emitir();
        }
        global.addEventListener('online', voltou);
        global.addEventListener('focus', voltou);
        global.addEventListener('offline', function () { self._emitir(); });
    };

    RCGravador.prototype.parar = function () {
        var self = this;
        clearInterval(this._tique);
        if (!this._recorder || this._recorder.state === 'inactive') {
            this._pararStreams();
            this._soltarWakeLock();
            return Promise.resolve();
        }
        this._mudar('parando');
        return new Promise(function (resolve) {
            var feito = false;
            function fim() {
                if (feito) return;
                feito = true;
                self._paradoEm = self._paradoEm || Date.now();
                self._pararStreams();
                self._soltarWakeLock();
                resolve();
            }
            // O último "dataavailable" chega antes do "stop".
            self._recorder.addEventListener('stop', function () { setTimeout(fim, 50); }, { once: true });
            try {
                self._recorder.stop();
            } catch (e) {
                fim();
            }
            // Nunca prende a tela esperando o navegador.
            setTimeout(fim, 4000);
        }).then(function () {
            return self._gravacoesLocais;
        }).then(function () {
            clearInterval(self._batimento);
            self._salvarSessaoLocal({ gravando: false, paradaEm: self._paradoEm });
            self._mudar(self._pendentes() > 0 ? 'enviando' : 'ocioso');
            self._acordarEnvio();
        });
    };

    RCGravador.prototype.finalizar = async function (opcoes) {
        opcoes = opcoes || {};
        if (this._recorder && this._recorder.state !== 'inactive') await this.parar();
        if (opcoes.titulo) this.sessao.titulo = opcoes.titulo;
        if (opcoes.papeis) this.sessao.papeis = opcoes.papeis;
        await this._salvarSessaoLocal({ gravando: false });

        this._mudar('enviando');
        this._pausado = false;
        this._acordarEnvio();
        var prazo = Date.now() + (opcoes.esperarPendentesMs == null ? 5 * 60 * 1000 : opcoes.esperarPendentesMs);
        while (this._pendentes() > 0 && Date.now() < prazo && !this._pausado) {
            this._acordarEnvio();
            await esperar(500);
        }
        var pendentes = this._pendentes();
        if (pendentes > 0 && !opcoes.forcar) {
            this._emitir();
            return { pendentes: pendentes, sessaoExpirada: this._pausado, mensagem: this._ultimoErro };
        }
        if (!this._indice) {
            this._mudar('ocioso');
            throw erroAmigavel('Nada foi gravado.');
        }

        this._mudar('finalizando');
        var r = await postarFormulario(this.urls.finalize, this.csrf, {
            upload_id: this.sessao.uploadId,
            total_chunks: this._indice,
            title: this.sessao.titulo,
            duration_seconds: opcoes.duracaoSegundos || this.segundos(),
            participant_roles: JSON.stringify(this.sessao.papeis || []),
            event_id: this.sessao.eventId,
            original_audio_name: 'gravacao' + this._extensao,
        });
        if (!r.ok) {
            this._mudar('erro');
            var erro = new Error(r.mensagem);
            erro.sessaoExpirada = !!r.sessaoExpirada;
            throw erro;
        }
        this._transcricaoId = r.dados.id;
        this._redirect = r.dados.redirect || '';
        if (pendentes === 0) {
            await apagarSessao(this.sessao.uploadId);
        } else {
            // Processou sem algumas partes: elas continuam aqui e aparecem nas
            // pendências para enviar depois (o portal refaz com o áudio completo).
            await this._salvarSessaoLocal({ finalizada: true, transcricaoId: r.dados.id, redirect: this._redirect });
        }
        this._mudar('concluido');
        this._publicar('concluido', { transcricaoId: r.dados.id, redirect: this._redirect });
        return r.dados;
    };

    RCGravador.prototype.descartar = async function () {
        if (this._recorder && this._recorder.state !== 'inactive') await this.parar();
        this._fila = new Map();
        await apagarSessao(this.sessao.uploadId);
        this._mudar('ocioso');
    };

    RCGravador.prototype.abrirMiniPainel = async function (elemento) {
        if (!('documentPictureInPicture' in global) || !elemento || !elemento.parentNode) return false;
        try {
            var pip = await global.documentPictureInPicture.requestWindow({ width: 340, height: 200 });
            Array.prototype.forEach.call(document.styleSheets, function (folha) {
                try {
                    var css = Array.prototype.map.call(folha.cssRules, function (r) { return r.cssText; }).join('\n');
                    var estilo = pip.document.createElement('style');
                    estilo.textContent = css;
                    pip.document.head.appendChild(estilo);
                } catch (e) {
                    // Folha de outro domínio (Font Awesome): vai como link.
                    if (folha.href) {
                        var link = pip.document.createElement('link');
                        link.rel = 'stylesheet';
                        link.href = folha.href;
                        pip.document.head.appendChild(link);
                    }
                }
            });
            if (document.documentElement.classList.contains('dark')) pip.document.documentElement.classList.add('dark');
            pip.document.body.style.margin = '0';
            var marcador = document.createComment('rc-gravador-mini-painel');
            elemento.parentNode.insertBefore(marcador, elemento);
            pip.document.body.appendChild(elemento);
            pip.addEventListener('pagehide', function () {
                if (marcador.parentNode) {
                    marcador.parentNode.insertBefore(elemento, marcador);
                    marcador.parentNode.removeChild(marcador);
                }
            });
            return true;
        } catch (e) {
            return false;
        }
    };

    // ------------------------------------------------------------------
    // Estáticos: pendências deste computador, retomada, comandos
    // ------------------------------------------------------------------
    RCGravador.pendentesLocais = async function () {
        var sessoes = await listarSessoes();
        var resultado = [];
        for (var i = 0; i < sessoes.length; i++) {
            var sessao = sessoes[i];
            var partes = await partesDaSessao(sessao.uploadId);
            var enviadas = partes.filter(function (p) { return p.enviada; }).length;
            var locais = partes.filter(function (p) { return !p.enviada && p.blob; }).length;
            var geradas = partes.length ? partes[partes.length - 1].indice + 1 : (sessao.partesGeradas || 0);
            if (sessao.finalizada && locais === 0) {
                // Tudo enviado e processado: nada a oferecer, só arruma a casa.
                await apagarSessao(sessao.uploadId);
                continue;
            }
            if (!geradas && !sessao.gravando && Date.now() - (sessao.atualizadoEm || 0) > 60 * 60 * 1000) {
                await apagarSessao(sessao.uploadId);
                continue;
            }
            resultado.push({
                uploadId: sessao.uploadId,
                titulo: sessao.titulo || '',
                origem: sessao.origem || 'gravador',
                eventId: sessao.eventId || '',
                papeis: sessao.papeis || [],
                extensao: sessao.extensao || '.webm',
                iniciadoEm: sessao.iniciadoEm || 0,
                atualizadoEm: sessao.atualizadoEm || 0,
                segundos: sessao.segundos || 0,
                partesGeradas: geradas,
                partesEnviadas: enviadas,
                partesLocais: locais,
                finalizada: !!sessao.finalizada,
                transcricaoId: sessao.transcricaoId || null,
                redirect: sessao.redirect || '',
                pagina: sessao.pagina || '',
            });
        }
        resultado.sort(function (a, b) { return (b.iniciadoEm || 0) - (a.iniciadoEm || 0); });
        return resultado;
    };

    RCGravador.retomar = async function (opcoes) {
        var sessao = await lerSessao(opcoes.uploadId);
        if (!sessao) return { enviadas: 0, pendentes: 0 };
        var partes = await partesDaSessao(opcoes.uploadId);
        var faltando = partes.filter(function (p) { return !p.enviada && p.blob; });
        var enviadas = 0;
        for (var i = 0; i < faltando.length; i++) {
            var parte = faltando[i];
            var tentativas = 0;
            for (;;) {
                var r = await enviarParte(opcoes.urls.chunk, opcoes.csrf, sessao, parte.indice, parte.blob, sessao.extensao);
                if (r.ok) {
                    await marcarEnviada(opcoes.uploadId, parte.indice);
                    enviadas += 1;
                    if (opcoes.onProgresso) opcoes.onProgresso({ enviadas: enviadas, total: faltando.length });
                    break;
                }
                if (r.sessaoExpirada || r.proibido) {
                    var erro = new Error(r.mensagem);
                    erro.sessaoExpirada = !!r.sessaoExpirada;
                    erro.proibido = !!r.proibido;
                    throw erro;
                }
                tentativas += 1;
                if (tentativas >= 6) throw new Error(r.mensagem || 'Não foi possível enviar agora. Tente de novo em instantes.');
                await esperar(backoff(tentativas));
            }
        }
        return { enviadas: enviadas, pendentes: faltando.length - enviadas };
    };

    RCGravador.finalizarSessao = async function (opcoes) {
        var sessao = (await lerSessao(opcoes.uploadId)) || { uploadId: opcoes.uploadId };
        var partes = await partesDaSessao(opcoes.uploadId);
        var geradas = partes.length ? partes[partes.length - 1].indice + 1 : (sessao.partesGeradas || 0);
        var r = await postarFormulario(opcoes.urls.finalize, opcoes.csrf, {
            upload_id: opcoes.uploadId,
            total_chunks: geradas,
            title: opcoes.titulo || sessao.titulo || '',
            duration_seconds: opcoes.duracao || sessao.segundos || 0,
            participant_roles: JSON.stringify(opcoes.papeis || sessao.papeis || []),
            event_id: opcoes.eventId || sessao.eventId || '',
            original_audio_name: 'gravacao' + (sessao.extensao || '.webm'),
        });
        if (!r.ok) {
            var erro = new Error(r.mensagem);
            erro.sessaoExpirada = !!r.sessaoExpirada;
            erro.status = r.status;
            throw erro;
        }
        var faltam = partes.filter(function (p) { return !p.enviada; }).length;
        if (!faltam) {
            await apagarSessao(opcoes.uploadId);
        } else {
            await guardarSessao(Object.assign(sessao, { finalizada: true, transcricaoId: r.dados.id, redirect: r.dados.redirect }));
        }
        return r.dados;
    };

    RCGravador.descartarLocal = function (uploadId) {
        return apagarSessao(uploadId);
    };

    RCGravador.sessaoAtiva = function (uploadId, esperaMs) {
        return new Promise(function (resolve) {
            var respondeu = false;
            var desligar = canal().ouvir(function (msg) {
                if (msg.tipo === 'pong' && msg.uploadId === uploadId) {
                    respondeu = true;
                    desligar();
                    resolve(msg.estado || true);
                }
            });
            canal().enviar({ tipo: 'ping', uploadId: uploadId });
            setTimeout(function () {
                if (!respondeu) {
                    desligar();
                    resolve(false);
                }
            }, esperaMs || 1500);
        });
    };

    RCGravador.enviarComando = function (uploadId, comando, dados) {
        canal().enviar({ tipo: 'comando', uploadId: uploadId, comando: comando, dados: dados || {} });
    };

    RCGravador.ouvir = function (callback) {
        return canal().ouvir(callback);
    };

    RCGravador.suporte = function () {
        var md = navigator.mediaDevices;
        return {
            mediaRecorder: typeof MediaRecorder !== 'undefined',
            microfone: !!(md && md.getUserMedia),
            audioDoComputador: !!(md && md.getDisplayMedia),
            indexedDB: typeof indexedDB !== 'undefined',
            miniPainel: 'documentPictureInPicture' in global,
            canal: typeof BroadcastChannel !== 'undefined',
            wakeLock: 'wakeLock' in navigator,
        };
    };

    RCGravador.formatarTempo = formatarTempo;
    RCGravador.novoUploadId = novoUploadId;

    // Só para os testes automáticos (Node): as peças internas, sem navegador.
    RCGravador._interno = {
        enviarParte: enviarParte,
        postarFormulario: postarFormulario,
        backoff: backoff,
        extensaoDoMime: extensaoDoMime,
        guardarParte: guardarParte,
        partesDaSessao: partesDaSessao,
        apagarSessao: apagarSessao,
        guardarSessao: guardarSessao,
        lerSessao: lerSessao,
        reiniciarBanco: function () { bancoPromessa = null; },
        reiniciarCanal: function () { canalUnico = null; },
    };

    global.RCGravador = RCGravador;
})(typeof window !== 'undefined' ? window : globalThis);
