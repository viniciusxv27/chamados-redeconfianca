/*
 * Usar localmente — cópia de trabalho de uma pasta (ou arquivo) do Drive no
 * computador da pessoa, e a devolução no "Finalizar uso" (drive/uso_local.py).
 *
 * O caminho: o portal diz o que tem dentro (manifesto), a pessoa escolhe onde
 * criar a pasta temporária (o seletor abre na Downloads, que é o que o
 * navegador permite sugerir) e a página grava os arquivos lá. A partir daí a
 * pessoa trabalha com os programas dela, sem o portal no meio.
 *
 * No "Finalizar uso" a página relê a pasta, compara com o que gravou e manda de
 * volta: o que mudou vira nova versão, o que nasceu lá vira arquivo novo (com a
 * subpasta criada no Drive, se for o caso). Arquivo apagado na cópia NÃO é
 * apagado do Drive — some da máquina da pessoa, e o portal só conta quantos
 * foram. Só depois de tudo enviado a pasta temporária é removida; com qualquer
 * falha ela fica onde está, com o aviso do que não subiu.
 *
 * O acesso à pasta (FileSystemDirectoryHandle) fica no IndexedDB: voltando ao
 * portal, o menu do item mostra "Finalizar uso" em vez de "Usar localmente" —
 * inclusive dias depois, enquanto a permissão do navegador continuar de pé.
 *
 * Precisa de File System Access (Chrome e Edge, em HTTPS). Sem isso, o menu
 * explica e oferece o download de sempre.
 */
(function (global) {
    'use strict';

    var BANCO = 'rc-uso-local';
    var LOJA = 'usos';
    var PREFIXO_PASTA = 'Portal - ';
    var ESTILOS = {
        info: 'bg-blue-50 border-blue-100 text-blue-800',
        ok: 'bg-green-50 border-green-100 text-green-800',
        atencao: 'bg-amber-50 border-amber-100 text-amber-800',
        erro: 'bg-red-50 border-red-100 text-red-700',
    };

    // ------------------------------------------------------------------
    // Conversa com o portal
    // ------------------------------------------------------------------
    function csrf() {
        var achado = ((global.document && global.document.cookie) || '')
            .match(/(?:^|;\s*)csrftoken=([^;]+)/);
        return achado ? decodeURIComponent(achado[1]) : '';
    }

    async function pedir(url, opcoes) {
        opcoes = opcoes || {};
        var metodo = opcoes.method || 'GET';
        var cabecalhos = { 'X-Requested-With': 'XMLHttpRequest' };
        if (metodo !== 'GET') cabecalhos['X-CSRFToken'] = csrf();
        var r;
        try {
            r = await fetch(url, {
                method: metodo, body: opcoes.body, headers: cabecalhos,
                credentials: 'same-origin', cache: 'no-store',
            });
        } catch (e) {
            return { ok: false, erro: 'Sem conexão com o portal agora.' };
        }
        var tipo = (r.headers && r.headers.get('content-type')) || '';
        var dados = null;
        if (tipo.indexOf('application/json') !== -1) {
            try { dados = await r.json(); } catch (e) { dados = null; }
        }
        if (!dados || typeof dados !== 'object') {
            // Página de login (sessão vencida) ou erro do servidor: nunca sucesso.
            return { ok: false, erro: r.status === 200
                ? 'Sua sessão no portal expirou. Entre de novo e tente outra vez.'
                : 'O portal respondeu ' + r.status + '. Tente de novo em instantes.' };
        }
        dados.ok = !!r.ok && !dados.erro && dados.success !== false;
        if (!dados.ok && !dados.erro) {
            dados.erro = dados.msg || dados.message || dados.mensagem
                || (dados.erros && dados.erros[0]) || 'Não deu certo.';
        }
        return dados;
    }

    async function baixar(url) {
        var r = await fetch(url, { credentials: 'same-origin', cache: 'no-store' });
        if (!r.ok) throw new Error('não foi possível baixar (' + r.status + ')');
        return await r.blob();
    }

    // ------------------------------------------------------------------
    // IndexedDB: o acesso à pasta sobrevive ao recarregar a página
    // ------------------------------------------------------------------
    var bancoPromessa = null;

    function banco() {
        if (typeof indexedDB === 'undefined') return Promise.reject(new Error('Sem IndexedDB.'));
        if (!bancoPromessa) {
            bancoPromessa = new Promise(function (ok, falhou) {
                var pedido = indexedDB.open(BANCO, 1);
                pedido.onupgradeneeded = function () {
                    if (!pedido.result.objectStoreNames.contains(LOJA)) {
                        pedido.result.createObjectStore(LOJA, { keyPath: 'id' });
                    }
                };
                pedido.onsuccess = function () { ok(pedido.result); };
                pedido.onerror = function () { bancoPromessa = null; falhou(pedido.error); };
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

    // Os acessos às pastas desta sessão ficam aqui: enquanto a página está
    // aberta é este objeto que vale, e o IndexedDB serve para o dia seguinte.
    var vivos = {};

    function guardar(uso) {
        vivos[uso.id] = uso;
        return naLoja('readwrite', function (l) { return l.put(uso); }).catch(function () { return null; });
    }

    function ler(id) {
        if (vivos[id]) return Promise.resolve(vivos[id]);
        return naLoja('readonly', function (l) { return l.get(id); }).catch(function () { return null; });
    }

    function esquecer(id) {
        delete vivos[id];
        return naLoja('readwrite', function (l) { return l.delete(id); }).catch(function () { return null; });
    }
    function todos() { return naLoja('readonly', function (l) { return l.getAll(); }).catch(function () { return []; }); }

    // ------------------------------------------------------------------
    // A pasta no computador
    // ------------------------------------------------------------------
    function suportado() {
        return typeof global.showDirectoryPicker === 'function' && global.isSecureContext !== false;
    }

    function limparNome(nome) {
        // O que o sistema de arquivos não aceita no nome de uma pasta.
        return String(nome || 'Drive').replace(/[\\/:*?"<>|]/g, '-').replace(/\s+/g, ' ').trim().slice(0, 60)
            || 'Drive';
    }

    async function nomeLivre(base, desejado) {
        for (var i = 0; i < 50; i++) {
            var tentativa = i ? desejado + ' (' + (i + 1) + ')' : desejado;
            try {
                await base.getDirectoryHandle(tentativa);      // já existe: tenta o próximo
            } catch (e) {
                return tentativa;
            }
        }
        return desejado + ' ' + Date.now();
    }

    async function permissao(handle, escrita) {
        if (!handle || !handle.queryPermission) return true;
        var opcoes = { mode: escrita ? 'readwrite' : 'read' };
        if ((await handle.queryPermission(opcoes)) === 'granted') return true;
        return (await handle.requestPermission(opcoes)) === 'granted';
    }

    async function pastaDe(raiz, partes, criar) {
        var atual = raiz;
        for (var i = 0; i < partes.length; i++) {
            atual = await atual.getDirectoryHandle(partes[i], { create: !!criar });
        }
        return atual;
    }

    async function gravar(raiz, caminho, blob) {
        var partes = caminho.split('/');
        var nome = partes.pop();
        var pasta = await pastaDe(raiz, partes, true);
        var arquivo = await pasta.getFileHandle(nome, { create: true });
        var escrita = await arquivo.createWritable();
        await escrita.write(blob);
        await escrita.close();
        var agora = await arquivo.getFile();
        return { tamanho: agora.size, modificado: agora.lastModified };
    }

    async function varrer(pasta, prefixo, saida) {
        for await (var entrada of pasta.values()) {
            var caminho = prefixo + entrada.name;
            if (entrada.kind === 'directory') {
                saida.pastas.push(caminho);
                await varrer(entrada, caminho + '/', saida);
            } else {
                var arquivo = await entrada.getFile();
                saida.arquivos[caminho] = { arquivo: arquivo, tamanho: arquivo.size,
                                            modificado: arquivo.lastModified };
            }
        }
        return saida;
    }

    // ------------------------------------------------------------------
    // Aviso na tela (a mesma linguagem do "editar no computador")
    // ------------------------------------------------------------------
    var caixa = null;

    function avisar(texto, tipo, fixo) {
        if (!global.document) return;
        if (!caixa) {
            caixa = global.document.createElement('div');
            caixa.id = 'rc-uso-local-aviso';
            caixa.className = 'fixed bottom-4 right-4 z-50 max-w-sm text-sm rounded-lg border px-4 py-3 shadow-lg';
            global.document.body.appendChild(caixa);
        }
        caixa.className = 'fixed bottom-4 right-4 z-50 max-w-sm text-sm rounded-lg border px-4 py-3 shadow-lg '
            + (ESTILOS[tipo] || ESTILOS.info);
        caixa.textContent = texto;
        if (caixa.esconder) clearTimeout(caixa.esconder);
        if (!fixo) {
            caixa.esconder = setTimeout(function () {
                if (caixa) { caixa.remove(); caixa = null; }
            }, 9000);
        }
    }

    function tamanhoHumano(bytes) {
        var n = Number(bytes) || 0;
        var unidades = ['B', 'KB', 'MB', 'GB'];
        var i = 0;
        while (n >= 1024 && i < unidades.length - 1) { n /= 1024; i++; }
        return (i ? n.toFixed(1) : n) + ' ' + unidades[i];
    }

    // ------------------------------------------------------------------
    // Começar o uso local
    // ------------------------------------------------------------------
    async function usar(id, nome) {
        if (!suportado()) {
            avisar('Para usar localmente, abra o portal no Chrome ou no Edge do computador. '
                   + 'Neste navegador só dá para baixar o arquivo.', 'atencao');
            return;
        }
        avisar('Preparando a cópia de "' + nome + '"…', 'info', true);
        var manifesto = await pedir('/drive/file/' + encodeURIComponent(id) + '/uso-local/');
        if (!manifesto.ok) { avisar(manifesto.erro, 'erro'); return; }
        if (manifesto.excedeu) {
            avisar('Esta pasta passa do limite de ' + manifesto.limites.arquivos + ' arquivos ou '
                   + tamanhoHumano(manifesto.limites.bytes) + '. Abra uma subpasta e use localmente só ela.',
                   'atencao');
            return;
        }
        if (!manifesto.arquivos.length) {
            avisar(manifesto.aviso || 'Não há arquivo para copiar aqui.', 'atencao');
            return;
        }

        var base;
        try {
            base = await global.showDirectoryPicker({ id: 'rc-uso-local', mode: 'readwrite',
                                                      startIn: 'downloads' });
        } catch (e) {
            avisar('Uso local cancelado.', 'info');
            return;
        }
        if (!(await permissao(base, true))) {
            avisar('Sem permissão para gravar nessa pasta.', 'erro');
            return;
        }

        var pastaLocal = await nomeLivre(base, PREFIXO_PASTA + limparNome(manifesto.nome));
        var raiz = await base.getDirectoryHandle(pastaLocal, { create: true });

        var arquivos = [];
        var falhas = [];
        for (var i = 0; i < manifesto.arquivos.length; i++) {
            var meta = manifesto.arquivos[i];
            avisar('Copiando ' + (i + 1) + ' de ' + manifesto.arquivos.length + ': ' + meta.nome, 'info', true);
            try {
                var marca = await gravar(raiz, meta.caminho, await baixar(meta.url));
                arquivos.push({ id: meta.id, caminho: meta.caminho, pasta_id: meta.pasta_id,
                                tamanho: marca.tamanho, modificado: marca.modificado });
            } catch (e) {
                falhas.push(meta.caminho + ': ' + (e && e.message ? e.message : e));
            }
        }

        await guardar({
            id: id, nome: manifesto.nome, pastaLocal: pastaLocal, base: base, raiz: raiz,
            sectorId: manifesto.sector_id, pastaId: manifesto.pasta_id,
            podeEnviar: !!manifesto.pode_enviar, podeCriar: !!manifesto.pode_criar,
            arquivos: arquivos, pastas: manifesto.pastas || [], criadoEm: Date.now(),
        });
        marcarMenus();

        var recado = 'Pasta "' + pastaLocal + '" criada com ' + arquivos.length + ' arquivo(s). '
            + 'Trabalhe nela e, ao terminar, volte aqui e escolha "Finalizar uso".';
        if (manifesto.ignorados.length) {
            recado += ' ' + manifesto.ignorados.length + ' arquivo(s) do Google ficaram de fora '
                + '(eles se editam no próprio Google).';
        }
        if (!manifesto.pode_enviar) {
            recado += ' Atenção: você não tem permissão para enviar alterações deste item.';
        }
        avisar(recado + (falhas.length ? ' ' + falhas.length + ' não copiaram.' : ''),
               falhas.length ? 'atencao' : 'ok', true);
    }

    // ------------------------------------------------------------------
    // Finalizar o uso: devolver o que mudou e apagar a cópia
    // ------------------------------------------------------------------
    async function mandarVersao(arquivoId, arquivo) {
        var corpo = new FormData();
        corpo.append('arquivo', arquivo, arquivo.name);
        return await pedir('/drive/file/' + encodeURIComponent(arquivoId) + '/substituir/',
                           { method: 'POST', body: corpo });
    }

    async function mandarNovo(sectorId, pastaId, arquivo) {
        var corpo = new FormData();
        corpo.append('folder_id', pastaId);
        corpo.append('arquivos', arquivo, arquivo.name);
        return await pedir('/drive/s/' + sectorId + '/upload/', { method: 'POST', body: corpo });
    }

    async function criarPasta(sectorId, paiId, nome) {
        var corpo = new FormData();
        corpo.append('folder_id', paiId);
        corpo.append('nome', nome);
        var r = await pedir('/drive/s/' + sectorId + '/mkdir/', { method: 'POST', body: corpo });
        return r.ok ? r : null;
    }

    async function finalizar(id, nome) {
        var uso = await ler(id);
        if (!uso) {
            avisar('Não há uso local desta pasta neste navegador.', 'atencao');
            marcarMenus();
            return;
        }
        if (!(await permissao(uso.raiz, true))) {
            avisar('Preciso da permissão de acesso à pasta "' + uso.pastaLocal + '" para finalizar.', 'erro');
            return;
        }

        avisar('Conferindo a pasta "' + uso.pastaLocal + '"…', 'info', true);
        var local;
        try {
            local = await varrer(uso.raiz, '', { arquivos: {}, pastas: [] });
        } catch (e) {
            avisar('Não consegui ler a pasta "' + uso.pastaLocal + '". Ela ainda existe?', 'erro');
            return;
        }

        var conhecidos = {};
        (uso.arquivos || []).forEach(function (a) { conhecidos[a.caminho] = a; });
        var mudados = [];
        var apagados = 0;
        Object.keys(conhecidos).forEach(function (caminho) {
            var achado = local.arquivos[caminho];
            if (!achado) { apagados++; return; }
            if (achado.tamanho !== conhecidos[caminho].tamanho
                || achado.modificado !== conhecidos[caminho].modificado) {
                mudados.push({ meta: conhecidos[caminho], arquivo: achado.arquivo });
            }
        });
        var novos = Object.keys(local.arquivos).filter(function (c) { return !conhecidos[c]; });

        if (!mudados.length && !novos.length) {
            if (!global.confirm('Nada mudou na pasta "' + uso.pastaLocal + '".\n\n'
                                + 'Finalizar o uso e apagar a pasta do computador?')) return;
        } else if (!global.confirm('Finalizar o uso de "' + (nome || uso.nome) + '"?\n\n'
                                   + mudados.length + ' arquivo(s) alterado(s) viram nova versão\n'
                                   + novos.length + ' arquivo(s) novo(s) sobem para o Drive\n'
                                   + (apagados ? apagados + ' apagado(s) na cópia continuam no Drive\n' : '')
                                   + '\nDepois de enviar, a pasta "' + uso.pastaLocal
                                   + '" é apagada do computador.')) {
            return;
        }

        var falhas = [];
        var versoes = 0;
        for (var i = 0; i < mudados.length; i++) {
            avisar('Enviando alteração ' + (i + 1) + ' de ' + mudados.length + '…', 'info', true);
            var r = await mandarVersao(mudados[i].meta.id, mudados[i].arquivo);
            if (r.ok) versoes++; else falhas.push(mudados[i].meta.caminho + ': ' + r.erro);
        }

        // Arquivo novo precisa de uma pasta no Drive: as que já existem vêm do
        // manifesto; as que a pessoa criou no computador são criadas agora.
        var pastasDrive = { '': uso.pastaId };
        (uso.pastas || []).forEach(function (p) { pastasDrive[p.caminho] = p.id; });

        async function pastaDoDrive(caminho) {
            if (pastasDrive[caminho] !== undefined) return pastasDrive[caminho];
            var partes = caminho.split('/');
            var nomePasta = partes.pop();
            var paiId = await pastaDoDrive(partes.join('/'));
            if (!paiId) return null;
            var criada = await criarPasta(uso.sectorId, paiId, nomePasta);
            pastasDrive[caminho] = (criada && criada.file_id) || null;
            return pastasDrive[caminho];
        }

        var enviados = 0;
        for (var n = 0; n < novos.length; n++) {
            var caminhoNovo = novos[n];
            var partes = caminhoNovo.split('/');
            partes.pop();
            avisar('Enviando arquivo novo ' + (n + 1) + ' de ' + novos.length + '…', 'info', true);
            var destino = await pastaDoDrive(partes.join('/'));
            if (!destino) { falhas.push(caminhoNovo + ': não consegui criar a pasta no Drive'); continue; }
            var envio = await mandarNovo(uso.sectorId, destino, local.arquivos[caminhoNovo].arquivo);
            if (envio.ok) enviados++; else falhas.push(caminhoNovo + ': ' + envio.erro);
        }

        var corpo = new FormData();
        corpo.append('nome', uso.nome || '');
        corpo.append('versoes', versoes);
        corpo.append('novos', enviados);
        corpo.append('apagados', apagados);
        corpo.append('falhas', falhas.length);
        await pedir('/drive/file/' + encodeURIComponent(id) + '/uso-local/finalizar/',
                    { method: 'POST', body: corpo });

        if (falhas.length) {
            avisar('Enviei ' + versoes + ' versão(ões) e ' + enviados + ' arquivo(s) novo(s), mas '
                   + falhas.length + ' não subiram: ' + falhas[0]
                   + '. A pasta "' + uso.pastaLocal + '" continua no computador para você tentar de novo.',
                   'erro', true);
            return;
        }

        try {
            await uso.base.removeEntry(uso.pastaLocal, { recursive: true });
        } catch (e) {
            avisar('Tudo enviado, mas não consegui apagar a pasta "' + uso.pastaLocal
                   + '" — apague à mão se quiser.', 'atencao', true);
            await esquecer(id);
            marcarMenus();
            return;
        }
        await esquecer(id);
        marcarMenus();
        avisar('Uso local finalizado: ' + versoes + ' nova(s) versão(ões), ' + enviados
               + ' arquivo(s) novo(s)' + (apagados ? ', ' + apagados + ' apagado(s) só na sua cópia' : '')
               + '. A pasta "' + uso.pastaLocal + '" foi removida do computador.', 'ok', true);
    }

    // ------------------------------------------------------------------
    // O menu do item: "Usar localmente" ou "Finalizar uso"
    // ------------------------------------------------------------------
    async function marcarMenus() {
        if (!global.document) return;
        var emUso = {};
        (await todos()).forEach(function (uso) { emUso[uso.id] = uso; });
        global.document.querySelectorAll('[data-uso-local]').forEach(function (botao) {
            var id = botao.getAttribute('data-uso-local');
            var papel = botao.getAttribute('data-uso-papel');     // 'usar' | 'finalizar'
            var ligado = papel === 'finalizar' ? !!emUso[id] : !emUso[id];
            botao.classList.toggle('hidden', !ligado);
        });
    }

    global.RCUsoLocal = {
        usar: usar,
        finalizar: finalizar,
        marcarMenus: marcarMenus,
        suportado: suportado,
        _interno: { limparNome: limparNome, tamanhoHumano: tamanhoHumano, ler: ler, guardar: guardar },
    };

    if (global.document) {
        if (global.document.readyState === 'loading') {
            global.document.addEventListener('DOMContentLoaded', marcarMenus);
        } else {
            marcarMenus();
        }
    }
})(typeof window !== 'undefined' ? window : this);
