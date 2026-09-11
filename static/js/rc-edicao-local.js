/*
 * Editar no computador — arquivos do Drive do portal (drive/edicao_local.py).
 *
 * Word, Excel e PowerPoint: abre o próprio programa pelo protocolo do Office
 * (ms-word:ofe|u|…) apontando para o endereço WebDAV do portal; cada "Salvar"
 * no programa sobe como nova versão. A página só acompanha: mostra quando
 * salvou e se houve conflito.
 *
 * PDF, Power BI e o resto: grava uma cópia de trabalho numa pasta que a pessoa
 * escolhe (File System Access — Chrome e Edge), olha essa cópia a cada poucos
 * segundos e, quando o programa salva e o arquivo para de mudar, envia a nova
 * versão. A cópia fica lembrada no IndexedDB: voltando à página, dá para
 * continuar de onde parou. Sem esse recurso no navegador, baixa o arquivo e
 * oferece "Enviar versão editada".
 */
(function (global) {
    'use strict';

    var BANCO = 'rc-edicao-local';
    var LOJA = 'copias';
    var OLHAR_MS = 3000;
    var STATUS_MS = 15000;
    var ESPERA_APOS_FALHA_MS = 30000;
    var ESTILOS = {
        info: 'bg-blue-50 border border-blue-100 text-blue-800',
        ok: 'bg-green-50 border border-green-100 text-green-800',
        atencao: 'bg-amber-50 border border-amber-100 text-amber-800',
        erro: 'bg-red-50 border border-red-100 text-red-700',
    };

    // ------------------------------------------------------------------
    // Utilitários
    // ------------------------------------------------------------------
    function csrf() {
        var cookies = (global.document && global.document.cookie) || '';
        var achado = cookies.match(/(?:^|;\s*)csrftoken=([^;]+)/);
        return achado ? decodeURIComponent(achado[1]) : '';
    }

    function hora(quando) {
        var d = quando ? new Date(quando) : new Date();
        return ('0' + d.getHours()).slice(-2) + ':' + ('0' + d.getMinutes()).slice(-2);
    }

    function marcaDe(arquivo) {
        return { lastModified: arquivo.lastModified, size: arquivo.size };
    }

    function mesmaMarca(a, b) {
        return !!(a && b) && a.lastModified === b.lastModified && a.size === b.size;
    }

    function extensaoDe(nome) {
        var partes = String(nome || '').split('.');
        return partes.length > 1 ? partes.pop().toLowerCase() : '';
    }

    function opcoesDoSeletor(nome) {
        var opcoes = { suggestedName: nome, id: 'rc-edicao-local', startIn: 'documents' };
        var ext = extensaoDe(nome);
        if (/^[a-z0-9]{1,15}$/.test(ext)) {
            opcoes.types = [{ description: 'Arquivo .' + ext, accept: { 'application/octet-stream': ['.' + ext] } }];
        }
        return opcoes;
    }

    function suporteCopia() {
        return typeof global.showSaveFilePicker === 'function' && global.isSecureContext !== false;
    }

    // Só vale como resposta do portal se vier JSON: a página de login (sessão
    // expirada) ou um redirecionamento não contam como sucesso.
    async function lerResposta(r) {
        var tipo = (r.headers && r.headers.get('content-type')) || '';
        var dados = null;
        if (tipo.indexOf('application/json') !== -1) {
            try {
                dados = await r.json();
            } catch (e) {
                dados = null;
            }
        }
        if (!dados || typeof dados !== 'object') {
            var sessao = r.type === 'opaqueredirect' || !r.status || r.status === 200 || r.status === 403;
            return {
                ok: false,
                status: r.status || 0,
                semJson: true,
                erro: sessao
                    ? 'Sua sessão no portal expirou ou o acesso foi negado. Entre de novo no portal e tente outra vez.'
                    : 'O portal respondeu ' + r.status + '. Tente de novo em instantes.',
            };
        }
        dados.status = r.status;
        dados.erro = dados.error || '';
        dados.ok = !!r.ok && !dados.error;
        return dados;
    }

    async function pedir(url, opcoes) {
        opcoes = opcoes || {};
        var metodo = opcoes.method || 'GET';
        var cabecalhos = { 'X-Requested-With': 'XMLHttpRequest' };
        if (metodo !== 'GET') cabecalhos['X-CSRFToken'] = csrf();
        var r;
        try {
            r = await fetch(url, {
                method: metodo,
                body: opcoes.body,
                headers: cabecalhos,
                credentials: 'same-origin',
                redirect: 'manual',
                cache: 'no-store',
            });
        } catch (e) {
            return { ok: false, status: 0, rede: true, erro: 'Sem conexão com o portal agora.' };
        }
        return lerResposta(r);
    }

    function formulario(campos) {
        var corpo = new FormData();
        Object.keys(campos || {}).forEach(function (chave) {
            var valor = campos[chave];
            if (valor === undefined || valor === null || valor === '') return;
            if (Array.isArray(valor)) {
                corpo.append(chave, valor[0], valor[1]);
            } else {
                corpo.append(chave, valor);
            }
        });
        return corpo;
    }

    // ------------------------------------------------------------------
    // IndexedDB: a cópia de trabalho (com o acesso ao arquivo) sobrevive ao
    // recarregar. Guardar é conforto — se o navegador recusar, segue sem.
    // ------------------------------------------------------------------
    var bancoPromessa = null;

    function banco() {
        if (typeof indexedDB === 'undefined') return Promise.reject(new Error('Sem IndexedDB.'));
        if (!bancoPromessa) {
            bancoPromessa = new Promise(function (ok, falhou) {
                var pedido = indexedDB.open(BANCO, 1);
                pedido.onupgradeneeded = function () {
                    if (!pedido.result.objectStoreNames.contains(LOJA)) {
                        pedido.result.createObjectStore(LOJA, { keyPath: 'fileId' });
                    }
                };
                pedido.onsuccess = function () { ok(pedido.result); };
                pedido.onerror = function () {
                    bancoPromessa = null;
                    falhou(pedido.error);
                };
            });
        }
        return bancoPromessa;
    }

    function naLoja(modo, operacao) {
        return banco().then(function (db) {
            return new Promise(function (ok, falhou) {
                var tx = db.transaction(LOJA, modo);
                var pedido = operacao(tx.objectStore(LOJA));
                tx.oncomplete = function () { ok(pedido ? pedido.result : undefined); };
                tx.onerror = tx.onabort = function () { falhou(tx.error); };
            });
        });
    }

    function guardarCopia(copia) {
        return naLoja('readwrite', function (loja) { return loja.put(copia); }).catch(function () { return null; });
    }

    function lerCopia(fileId) {
        return naLoja('readonly', function (loja) { return loja.get(fileId); }).catch(function () { return null; });
    }

    function esquecerCopia(fileId) {
        return naLoja('readwrite', function (loja) { return loja.delete(fileId); }).catch(function () { return null; });
    }

    // Abrir o Office pelo protocolo não avisa se deu certo; a janela perder o foco
    // (o navegador pergunta "Abrir o Word?", o Word aparece) é o melhor sinal.
    function lancarPrograma(uri, esperaMs) {
        return new Promise(function (resolve) {
            var saiu = false;
            function marcar() { saiu = true; }
            global.addEventListener('blur', marcar);
            global.document.addEventListener('visibilitychange', marcar);
            try {
                global.location.href = uri;
            } catch (e) {
                /* o navegador recusou o protocolo */
            }
            setTimeout(function () {
                global.removeEventListener('blur', marcar);
                global.document.removeEventListener('visibilitychange', marcar);
                resolve(saiu);
            }, esperaMs || 2500);
        });
    }

    // ------------------------------------------------------------------
    // A edição de um arquivo
    // ------------------------------------------------------------------
    /**
     * config: {fileId, nome, modo: 'office'|'arquivo', programa, podeSalvar, urlIniciar}
     * ui: {aviso(texto, tipo, acoes)} — tipo info|ok|atencao|erro; cada ação é
     *     {rotulo, fazer} (botão), {rotulo, href} (link) ou {rotulo, escolherArquivo, fazer(arquivo)}.
     */
    function RCEdicaoLocal(config, ui) {
        this.cfg = config;
        this.ui = ui;
        this.sessao = null;
        this.copia = null;
        this._olhando = false;
        this._timerOlhar = null;
        this._timerStatus = null;
        this._candidata = null;
        this._esperarAte = 0;
        this._enviando = false;
        this._ultimoStatus = '';
    }

    RCEdicaoLocal.prototype._acaoEncerrar = function () {
        var self = this;
        return { rotulo: 'Encerrar edição', fazer: function () { return self.encerrar(); } };
    };

    RCEdicaoLocal.prototype._acaoEnviarManual = function () {
        var self = this;
        return {
            rotulo: 'Enviar versão editada',
            escolherArquivo: true,
            fazer: function (arquivo) { return self.enviarManual(arquivo); },
        };
    };

    RCEdicaoLocal.prototype._acoesDaCopia = function () {
        var self = this;
        if (!this.copia || !this.copia.handle) return [this._acaoEnviarManual(), this._acaoEncerrar()];
        return [
            { rotulo: 'Enviar agora', fazer: function () { return self.verificarCopia({ agora: true }); } },
            this._acaoEncerrar(),
        ];
    };

    RCEdicaoLocal.prototype._novaCopia = function (resposta, handle, arquivo) {
        return {
            fileId: this.cfg.fileId,
            nome: arquivo ? arquivo.name : (resposta.nome || this.cfg.nome),
            handle: handle || null,
            token: resposta.token,
            urlEnviar: resposta.url_enviar,
            urlStatus: resposta.url_status,
            urlEncerrar: resposta.url_encerrar,
            enviado: arquivo ? marcaDe(arquivo) : null,
            recusado: null,
            salvamentos: 0,
            criadaEm: Date.now(),
        };
    };

    RCEdicaoLocal.prototype.temPendencia = function () {
        return this._enviando || !!this._candidata;
    };

    RCEdicaoLocal.prototype.abrir = async function (opcoes) {
        opcoes = opcoes || {};
        var cfg = this.cfg;
        var comoArquivo = !!opcoes.outroPrograma || cfg.modo !== 'office';
        var handle = null;
        if (comoArquivo && cfg.podeSalvar && suporteCopia()) {
            // O seletor de pasta exige o clique da pessoa: vem antes de qualquer espera.
            try {
                handle = await global.showSaveFilePicker(opcoesDoSeletor(cfg.nome));
            } catch (e) {
                if (e && e.name === 'AbortError') return 'cancelado';
                handle = null;
            }
        }
        this.ui.aviso('Preparando a edição…', 'info');
        var r = await pedir(cfg.urlIniciar, {
            method: 'POST',
            body: formulario({ modo: comoArquivo ? 'arquivo' : 'office' }),
        });
        if (!r.ok) {
            this.ui.aviso(r.erro || 'Não foi possível abrir para edição agora.', 'erro');
            return 'erro';
        }
        this._pararDeOlhar();
        clearTimeout(this._timerStatus);
        this._ultimoStatus = '';
        this.sessao = r;
        if (r.modo === 'office') return this._abrirNoOffice(r);
        if (handle) return this._criarCopia(r, handle);
        return this._baixarParaEditar(r);
    };

    RCEdicaoLocal.prototype._abrirNoOffice = async function (r) {
        var self = this;
        var programa = r.programa || 'Office';
        var acoes = [{ rotulo: 'Abrir no ' + programa + ' de novo', href: r.uri_office }];
        if (!r.somente_leitura) acoes.push(this._acaoEncerrar());
        this.ui.aviso((r.somente_leitura
            ? 'Abrindo no ' + programa + ' em modo leitura.'
            : 'Abrindo no ' + programa + '. Cada vez que você salvar no ' + programa + ' (Ctrl+S), a nova versão vai para o Drive.')
            + ' Se o navegador perguntar, permita abrir o ' + programa + '.', 'info', acoes);
        this.copia = null;
        if (!r.somente_leitura) this._acompanharStatus();
        var navegador = (global.navigator && global.navigator.userAgent) || '';
        // No Firefox, protocolo sem programa instalado troca a página por um erro:
        // lá a pessoa clica no link do aviso.
        if (/firefox/i.test(navegador)) return 'office-link';
        var abriu = await lancarPrograma(r.uri_office);
        if (!abriu && this.sessao === r && !this._ultimoStatus) {
            var alternativa = this.cfg.podeSalvar
                ? [{ rotulo: 'Editar com outro programa', fazer: function () { return self.abrir({ outroPrograma: true }); } }]
                : [];
            this.ui.aviso('Se o ' + programa + ' não abriu, confira se o Microsoft Office está instalado neste computador.'
                + (this.cfg.podeSalvar ? ' Sem o Office, use "Editar com outro programa".' : ''), 'atencao',
            acoes.concat(alternativa));
        }
        return abriu ? 'office' : 'office-sem-sinal';
    };

    RCEdicaoLocal.prototype._acompanharStatus = function () {
        var self = this;
        var sessao = this.sessao;
        clearTimeout(this._timerStatus);
        async function passo() {
            if (self.sessao !== sessao) return;
            var s = await pedir(sessao.url_status);
            if (self.sessao !== sessao) return;
            if (s.ok) {
                self._mostrarStatus(s);
                if (s.expirada) return;
            }
            self._timerStatus = setTimeout(passo, STATUS_MS);
        }
        this._timerStatus = setTimeout(passo, 5000);
    };

    RCEdicaoLocal.prototype._mostrarStatus = function (s) {
        var programa = (this.sessao && this.sessao.programa) || 'Office';
        var chave = [s.expirada, s.salvamentos, s.conflito && s.conflito.em, s.aberto_no_programa, s.baixado].join('|');
        if (chave === this._ultimoStatus) return 'igual';
        this._ultimoStatus = chave;
        if (s.expirada) {
            this.ui.aviso('Esta edição foi encerrada ou o link venceu. Se o arquivo ainda está aberto no ' + programa
                + ', use "Salvar como" para guardar no computador e envie aqui.', 'atencao', [this._acaoEnviarManual()]);
            return 'expirada';
        }
        if (s.conflito) {
            this.ui.aviso('Outra pessoa alterou este arquivo no Drive enquanto você editava. Para não apagar o trabalho '
                + 'dela, suas alterações foram salvas como "' + s.conflito.nome + '", na mesma pasta.', 'atencao',
            [this._acaoEncerrar()]);
            return 'conflito';
        }
        if (s.salvamentos) {
            this.ui.aviso('Salvo no Drive às ' + hora(s.ultimo_salvamento_em) + ' (' + s.salvamentos
                + (s.salvamentos === 1 ? ' vez' : ' vezes') + '). Pode continuar editando no ' + programa + '.', 'ok',
            [this._acaoEncerrar()]);
            return 'salvo';
        }
        if (s.aberto_no_programa || s.baixado) {
            this.ui.aviso('Aberto no ' + programa + '. Cada vez que você salvar (Ctrl+S), a nova versão vai para o Drive '
                + '— a anterior fica no histórico de versões.', 'info', [this._acaoEncerrar()]);
            return 'aberto';
        }
        this._ultimoStatus = '';
        return 'aguardando';
    };

    RCEdicaoLocal.prototype._criarCopia = async function (r, handle) {
        this.ui.aviso('Baixando a cópia de trabalho…', 'info');
        var resposta = null;
        try {
            resposta = await fetch(r.url_conteudo, { credentials: 'same-origin', cache: 'no-store', redirect: 'manual' });
        } catch (e) {
            resposta = null;
        }
        if (!resposta || !resposta.ok) {
            this.ui.aviso('Não foi possível baixar o arquivo agora. Tente de novo.', 'erro');
            return 'erro';
        }
        var arquivo;
        try {
            var conteudo = await resposta.blob();
            var escrita = await handle.createWritable();
            await escrita.write(conteudo);
            await escrita.close();
            arquivo = await handle.getFile();
        } catch (e) {
            this.ui.aviso('Não foi possível gravar a cópia nessa pasta (' + ((e && e.message) || e) + '). Tente outra pasta.', 'erro');
            return 'erro';
        }
        this.copia = this._novaCopia(r, handle, arquivo);
        await guardarCopia(this.copia);
        this.ui.aviso('Cópia salva no seu computador: "' + arquivo.name + '". Abra esse arquivo no programa que você usa '
            + '(clique duas vezes nele, na pasta que você escolheu) e edite. Cada vez que você salvar, esta página envia '
            + 'a nova versão para o Drive — deixe esta aba aberta até terminar.', 'ok', this._acoesDaCopia());
        this.olhar();
        return 'copia';
    };

    RCEdicaoLocal.prototype.olhar = function () {
        if (this._olhando || !this.copia || !this.copia.handle) return;
        this._olhando = true;
        var self = this;
        (async function laco() {
            if (!self._olhando) return;
            await self.verificarCopia();
            if (self._olhando) self._timerOlhar = setTimeout(laco, OLHAR_MS);
        })();
    };

    RCEdicaoLocal.prototype._pararDeOlhar = function () {
        this._olhando = false;
        clearTimeout(this._timerOlhar);
        this._candidata = null;
    };

    /** Uma olhada na cópia: envia se o programa salvou. Devolve o que aconteceu. */
    RCEdicaoLocal.prototype.verificarCopia = async function (opcoes) {
        opcoes = opcoes || {};
        var copia = this.copia;
        if (!copia || !copia.handle) return 'sem-copia';
        if (this._enviando) return 'ocupado';
        if (!opcoes.agora && Date.now() < this._esperarAte) return 'esperando';
        var arquivo;
        try {
            arquivo = await copia.handle.getFile();
        } catch (e) {
            var motivo = e && e.name;
            if (motivo === 'NotFoundError') {
                this._pararDeOlhar();
                this.ui.aviso('A cópia do computador foi apagada, movida ou renomeada. Se você salvou com outro nome, '
                    + 'envie o arquivo por aqui.', 'atencao', [this._acaoEnviarManual(), this._acaoEncerrar()]);
                return 'sumiu';
            }
            if (motivo === 'NotAllowedError' || motivo === 'SecurityError') {
                this._pararDeOlhar();
                var self = this;
                this.ui.aviso('O navegador precisa da sua permissão de novo para ler a cópia do computador.', 'atencao', [
                    { rotulo: 'Continuar enviando as alterações', fazer: function () { return self.continuar(); } },
                    this._acaoEncerrar(),
                ]);
                return 'sem-permissao';
            }
            // O programa está gravando o arquivo agora: tenta na próxima olhada.
            return 'ilegivel';
        }
        var marca = marcaDe(arquivo);
        if (mesmaMarca(marca, copia.enviado)) {
            this._candidata = null;
            return 'igual';
        }
        if (mesmaMarca(marca, copia.recusado) && !opcoes.agora) return 'recusado';
        // Só envia quando o arquivo parou de mudar: programa gravando aos poucos
        // não vira versão pela metade.
        if (!opcoes.agora && !mesmaMarca(marca, this._candidata)) {
            this._candidata = marca;
            this.ui.aviso('Alteração encontrada na cópia do computador. Enviando assim que o programa terminar de salvar…',
                'info', this._acoesDaCopia());
            return 'mudando';
        }
        this._candidata = null;
        return this._enviar(arquivo, marca);
    };

    RCEdicaoLocal.prototype._enviar = async function (arquivo, marca, jaRenovou) {
        var copia = this.copia;
        this._enviando = true;
        this.ui.aviso('Enviando a versão editada para o Drive…', 'info');
        var r;
        try {
            r = await pedir(copia.urlEnviar, {
                method: 'POST',
                body: formulario({ arquivo: [arquivo, arquivo.name || copia.nome] }),
            });
        } finally {
            this._enviando = false;
        }
        if (this.copia !== copia) return 'encerrado';
        if (r.ok) {
            copia.enviado = marca;
            copia.recusado = null;
            copia.salvamentos = (copia.salvamentos || 0) + 1;
            this._esperarAte = 0;
            await guardarCopia(copia);
            if (r.conflito) {
                this.ui.aviso('Outra pessoa alterou este arquivo no Drive enquanto você editava. Para não apagar o trabalho '
                    + 'dela, sua versão foi salva como "' + r.nome + '", na mesma pasta — os próximos envios atualizam '
                    + 'essa cópia.', 'atencao', this._acoesDaCopia());
                return 'conflito';
            }
            this.ui.aviso('Salvo no Drive às ' + hora() + '.'
                + (copia.handle ? ' Pode continuar editando: cada vez que salvar, a nova versão sobe.' : ''),
            'ok', this._acoesDaCopia());
            return 'enviado';
        }
        if (r.status === 410 && !jaRenovou && await this._renovar()) {
            // A edição venceu com a cópia ainda aberta: renovada sem perder a base.
            return this._enviar(arquivo, marca, true);
        }
        if (r.status === 403 && !r.semJson) {
            this._pararDeOlhar();
            this.ui.aviso(r.erro + ' A cópia continua no seu computador.', 'erro', [this._acaoEncerrar()]);
            return 'proibido';
        }
        if (r.status === 400 || r.status === 413) {
            // Insistir com o mesmo arquivo não adianta: espera a pessoa salvar de novo.
            copia.recusado = marca;
            this.ui.aviso(r.erro || 'O portal recusou o arquivo.', 'erro', this._acoesDaCopia());
            return 'recusado';
        }
        this._esperarAte = Date.now() + ESPERA_APOS_FALHA_MS;
        this.ui.aviso((r.erro || 'Não foi possível enviar agora.') + ' A cópia continua no seu computador'
            + (copia.handle ? ' e o envio é tentado de novo em instantes.' : '.'), 'atencao', this._acoesDaCopia());
        return 'falhou';
    };

    RCEdicaoLocal.prototype._renovar = async function () {
        var copia = this.copia;
        if (!copia) return false;
        var r = await pedir(this.cfg.urlIniciar, {
            method: 'POST',
            body: formulario({ modo: 'arquivo', anterior: copia.token }),
        });
        if (!r.ok) return false;
        this.sessao = r;
        copia.token = r.token;
        copia.urlEnviar = r.url_enviar;
        copia.urlStatus = r.url_status;
        copia.urlEncerrar = r.url_encerrar;
        await guardarCopia(copia);
        return true;
    };

    RCEdicaoLocal.prototype.continuar = async function () {
        var copia = this.copia;
        if (!copia || !copia.handle) return 'sem-copia';
        var permissao = 'denied';
        try {
            permissao = await copia.handle.queryPermission({ mode: 'read' });
            if (permissao !== 'granted') permissao = await copia.handle.requestPermission({ mode: 'read' });
        } catch (e) {
            permissao = 'denied';
        }
        if (permissao !== 'granted') {
            this.ui.aviso('Sem permissão para ler a cópia do computador, o portal não consegue enviar as alterações. '
                + 'Você ainda pode enviar o arquivo editado por aqui.', 'erro', [this._acaoEnviarManual(), this._acaoEncerrar()]);
            return 'sem-permissao';
        }
        this.ui.aviso('Acompanhando a cópia "' + copia.nome + '" no seu computador. Cada vez que você salvar, a nova '
            + 'versão vai para o Drive.', 'ok', this._acoesDaCopia());
        this.olhar();
        return 'olhando';
    };

    /** Ao abrir a página: a cópia de trabalho deste arquivo que ficou neste computador. */
    RCEdicaoLocal.prototype.retomarGuardada = async function () {
        var copia = await lerCopia(this.cfg.fileId);
        if (!copia) return 'nada';
        this.copia = copia;
        if (!copia.handle) {
            this.ui.aviso('Você baixou este arquivo para editar. Quando terminar, envie o arquivo editado por aqui.',
                'info', [this._acaoEnviarManual(), this._acaoEncerrar()]);
            return 'download';
        }
        var permissao = 'prompt';
        try {
            permissao = await copia.handle.queryPermission({ mode: 'read' });
        } catch (e) {
            permissao = 'prompt';
        }
        if (permissao === 'granted') return this.continuar();
        var self = this;
        // Pedir a permissão de novo exige um clique da pessoa.
        this.ui.aviso('Há uma cópia de trabalho deste arquivo no seu computador ("' + copia.nome + '"). Continue para o '
            + 'portal enviar ao Drive as alterações que você salvar.', 'atencao', [
            { rotulo: 'Continuar enviando as alterações', fazer: function () { return self.continuar(); } },
            this._acaoEncerrar(),
        ]);
        return 'aguardando-permissao';
    };

    RCEdicaoLocal.prototype._baixarParaEditar = async function (r) {
        var link = global.document.createElement('a');
        link.href = r.url_conteudo;
        link.download = r.nome || '';
        global.document.body.appendChild(link);
        link.click();
        link.remove();
        if (r.somente_leitura) {
            this.ui.aviso('Arquivo baixado. Você pode abrir e alterar no computador, mas não tem permissão para salvar '
                + 'a nova versão no Drive.', 'info');
            return 'download';
        }
        this.copia = this._novaCopia(r, null, null);
        await guardarCopia(this.copia);
        this.ui.aviso('Arquivo baixado. Abra no seu programa, edite e salve. Quando terminar, envie o arquivo editado por '
            + 'aqui: ele vira a nova versão no Drive (a anterior fica no histórico).', 'ok',
        [this._acaoEnviarManual(), this._acaoEncerrar()]);
        return 'download';
    };

    RCEdicaoLocal.prototype.enviarManual = async function (arquivo) {
        if (!arquivo) return 'nada';
        if (!this.copia) {
            var base = this.sessao;
            if (!base) {
                // Nada aberto nesta página: abre uma edição só para receber a versão.
                var r = await pedir(this.cfg.urlIniciar, { method: 'POST', body: formulario({ modo: 'arquivo' }) });
                if (!r.ok) {
                    this.ui.aviso(r.erro || 'Não foi possível enviar agora.', 'erro', [this._acaoEnviarManual()]);
                    return 'erro';
                }
                this.sessao = base = r;
            }
            this.copia = this._novaCopia(base, null, null);
        }
        return this._enviar(arquivo, marcaDe(arquivo));
    };

    RCEdicaoLocal.prototype.encerrar = async function () {
        var sessao = this.sessao;
        var copia = this.copia;
        var programa = (sessao && sessao.programa) || 'Office';
        if (sessao && sessao.modo === 'office' && !sessao.somente_leitura && !copia
                && !global.confirm('Feche o arquivo no ' + programa + ' antes: depois de encerrar, salvar no ' + programa
                    + ' não chega mais ao Drive. Encerrar agora?')) {
            return 'cancelado';
        }
        if (copia && copia.handle) {
            // Última olhada: o que foi salvo e ainda não subiu vai antes de encerrar.
            var ultimo = await this.verificarCopia({ agora: true });
            if (['falhou', 'proibido', 'recusado', 'ilegivel'].indexOf(ultimo) !== -1
                    && !global.confirm('A última versão salva no computador não chegou ao Drive. Encerrar mesmo assim? '
                        + 'A cópia continua no seu computador.')) {
                return 'cancelado';
            }
        }
        this._pararDeOlhar();
        clearTimeout(this._timerStatus);
        this.sessao = null;
        this.copia = null;
        var url = (copia && copia.urlEncerrar) || (sessao && sessao.url_encerrar);
        if (url) await pedir(url, { method: 'POST' });
        if (copia) await esquecerCopia(copia.fileId);
        var ui = this.ui;
        var acoes = [];
        if (copia && copia.handle && typeof copia.handle.remove === 'function') {
            acoes.push({
                rotulo: 'Apagar a cópia do computador',
                fazer: async function () {
                    try {
                        await copia.handle.remove();
                        ui.aviso('Cópia do computador apagada.', 'ok');
                    } catch (e) {
                        ui.aviso('Não foi possível apagar a cópia — feche o arquivo no programa e tente de novo.', 'atencao', acoes);
                    }
                },
            });
        }
        ui.aviso('Edição encerrada.' + (copia && copia.handle ? ' A cópia continua no seu computador, se quiser guardar.' : ''),
            'ok', acoes);
        return 'encerrado';
    };

    // ------------------------------------------------------------------
    // Na página: o cartão "Editar no computador"
    // ------------------------------------------------------------------
    function criarAcao(acao) {
        var doc = global.document;
        var el;
        if (acao.href) {
            el = doc.createElement('a');
            el.href = acao.href;
        } else if (acao.escolherArquivo) {
            el = doc.createElement('label');
            var entrada = doc.createElement('input');
            entrada.type = 'file';
            entrada.className = 'hidden';
            entrada.addEventListener('change', function () {
                var arquivo = entrada.files && entrada.files[0];
                entrada.value = '';
                if (arquivo) acao.fazer(arquivo);
            });
            el.appendChild(entrada);
        } else {
            el = doc.createElement('button');
            el.type = 'button';
            el.addEventListener('click', function () { acao.fazer(); });
        }
        el.className = 'underline font-semibold cursor-pointer';
        el.appendChild(doc.createTextNode(acao.rotulo));
        return el;
    }

    RCEdicaoLocal.montar = function (elemento) {
        if (!elemento) return null;
        var dados = elemento.dataset;
        var caixa = elemento.querySelector('[data-papel="status"]');
        var ui = {
            aviso: function (texto, tipo, acoes) {
                caixa.className = 'mt-3 p-3 rounded-xl text-sm ' + (ESTILOS[tipo] || ESTILOS.info);
                caixa.textContent = '';
                var paragrafo = global.document.createElement('p');
                paragrafo.textContent = texto;
                caixa.appendChild(paragrafo);
                if (acoes && acoes.length) {
                    var linha = global.document.createElement('div');
                    linha.className = 'mt-2 flex flex-wrap gap-x-3 gap-y-1';
                    acoes.forEach(function (acao) { linha.appendChild(criarAcao(acao)); });
                    caixa.appendChild(linha);
                }
                caixa.hidden = false;
            },
        };
        var edicao = new RCEdicaoLocal({
            fileId: dados.fileId,
            nome: dados.nome,
            modo: dados.modo,
            programa: dados.programa,
            podeSalvar: dados.podeSalvar === '1',
            urlIniciar: dados.urlIniciar,
        }, ui);
        elemento.querySelectorAll('[data-acao="abrir"]').forEach(function (botao) {
            botao.addEventListener('click', function () { edicao.abrir(); });
        });
        elemento.querySelectorAll('[data-acao="outro-programa"]').forEach(function (botao) {
            botao.addEventListener('click', function () { edicao.abrir({ outroPrograma: true }); });
        });
        global.addEventListener('beforeunload', function (ev) {
            if (edicao.temPendencia()) {
                ev.preventDefault();
                ev.returnValue = '';
            }
        });
        edicao.retomarGuardada();
        return edicao;
    };

    RCEdicaoLocal.suporteCopia = suporteCopia;

    // Só para os testes automáticos (Node): as peças internas, sem navegador.
    RCEdicaoLocal._interno = {
        lerResposta: lerResposta,
        pedir: pedir,
        formulario: formulario,
        opcoesDoSeletor: opcoesDoSeletor,
        marcaDe: marcaDe,
        mesmaMarca: mesmaMarca,
        guardarCopia: guardarCopia,
        lerCopia: lerCopia,
        esquecerCopia: esquecerCopia,
        reiniciarBanco: function () { bancoPromessa = null; },
        tempos: { OLHAR_MS: OLHAR_MS, STATUS_MS: STATUS_MS, ESPERA_APOS_FALHA_MS: ESPERA_APOS_FALHA_MS },
    };

    global.RCEdicaoLocal = RCEdicaoLocal;
})(typeof window !== 'undefined' ? window : globalThis);
