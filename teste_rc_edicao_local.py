"""O módulo da edição no computador (static/js/rc-edicao-local.js), testado sem navegador.

O JavaScript roda no Node (22+) com o fetch, o seletor de pasta e o arquivo do
computador simulados. Prova o que decide se uma edição chegou ao Drive: só envia
quando o arquivo parou de mudar (programa ainda gravando não vira versão pela
metade), manda o próprio arquivo com o CSRF do cookie, mostra conflito e erro para
a pessoa, renova a edição vencida mantendo a base ("anterior"), para de insistir
sem permissão, espera depois de falha passageira, encerra enviando o que faltou —
e, no Office, chama o programa pelo protocolo com o aviso certo.

O seletor de pasta e o protocolo do Office de verdade só no navegador.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

BASE = os.path.dirname(os.path.abspath(__file__))
MODULO = os.path.join(BASE, 'static', 'js', 'rc-edicao-local.js')
ok = fail = 0


def t(nome, cond, extra=''):
    global ok, fail
    if cond:
        ok += 1
        print(f'  OK   {nome}')
    else:
        fail += 1
        print(f'  FALHA {nome} {extra}')


ROTEIRO = r'''
const resultados = [];
const confere = (nome, cond, extra) => resultados.push({ nome, ok: !!cond, extra: extra === undefined ? '' : String(extra) });
const dormir = (ms) => new Promise((r) => setTimeout(r, ms));

// Um navegador mínimo: cookie do CSRF e link de download que registra o clique.
const cliques = [];
globalThis.document = {
    cookie: 'outro=1; csrftoken=token-do-cookie',
    addEventListener() {},
    removeEventListener() {},
    createElement(tag) {
        const el = { tag, href: '', download: '', click() { cliques.push({ href: el.href, download: el.download }); }, remove() {} };
        return el;
    },
    body: { appendChild() {} },
};
globalThis.addEventListener = () => {};
globalThis.removeEventListener = () => {};
globalThis.location = { href: '' };
globalThis.isSecureContext = true;
let respostaConfirm = true;
globalThis.confirm = () => respostaConfirm;

// fetch por endereço: cada endereço tem a sua fila de respostas.
const pedidos = [];
const rotas = {};
globalThis.fetch = async (url, opcoes = {}) => {
    pedidos.push({ url, opcoes });
    const fila = rotas[url];
    const r = Array.isArray(fila) ? fila.shift() : fila;
    if (!r || r.erroDeRede) throw new TypeError('Failed to fetch');
    return r;
};
function resposta(status, corpo, tipo = 'application/json', extra = {}) {
    return Object.assign({
        status, ok: status >= 200 && status < 300, type: 'basic',
        headers: { get: (h) => (h.toLowerCase() === 'content-type' ? tipo : null) },
        json: async () => corpo,
        blob: async () => new Blob([typeof corpo === 'string' ? corpo : JSON.stringify(corpo)]),
    }, extra);
}
const ultimoPedido = (url) => [...pedidos].reverse().find((p) => p.url === url);
const quantosPedidos = (url) => pedidos.filter((p) => p.url === url).length;

// A cópia no computador: um arquivo que o "programa" altera.
function arquivoFalso(nome) {
    const estado = { conteudo: '', lastModified: 1000, removido: false, erroAoLer: '', permissao: 'granted' };
    return {
        estado,
        async createWritable() {
            const partes = [];
            return {
                async write(dado) { partes.push(typeof dado === 'string' ? dado : await dado.text()); },
                async close() { estado.conteudo = partes.join(''); estado.lastModified += 1; },
            };
        },
        async getFile() {
            if (estado.erroAoLer) {
                const e = new Error('simulado');
                e.name = estado.erroAoLer;
                throw e;
            }
            return new File([estado.conteudo], nome, { lastModified: estado.lastModified });
        },
        async queryPermission() { return estado.permissao; },
        async requestPermission() { return estado.permissao; },
        async remove() { estado.removido = true; },
    };
}
function programaSalva(handle, conteudo) {
    handle.estado.conteudo = conteudo;
    handle.estado.lastModified += 1000;
}

let seletorAtual = null;
const seletores = [];
globalThis.showSaveFilePicker = async (opcoes) => {
    seletores.push(opcoes);
    if (seletorAtual instanceof Error) throw seletorAtual;
    return seletorAtual;
};

const avisos = [];
const ui = { aviso(texto, tipo, acoes) { avisos.push({ texto, tipo, acoes: acoes || [] }); } };
const ultimo = () => avisos[avisos.length - 1] || { texto: '', tipo: '', acoes: [] };
const rotulos = (aviso) => aviso.acoes.map((a) => a.rotulo);

require(process.argv[2]);
const E = globalThis.RCEdicaoLocal;
const I = E._interno;

const URL_INICIAR = '/drive/file/F1/editar-no-computador/';
const sessaoArquivo = (token) => ({
    ok: true, token, modo: 'arquivo', nome: 'Contrato.pdf', somente_leitura: false,
    url_conteudo: '/drive/file/F1/content/?dl=1', url_status: `/drive/edicao/${token}/status/`,
    url_enviar: `/drive/edicao/${token}/enviar/`, url_encerrar: `/drive/edicao/${token}/encerrar/`,
});

(async () => {
    try {
        confere('expõe window.RCEdicaoLocal', typeof E === 'function');
        for (const nome of ['montar', 'suporteCopia']) confere('estático ' + nome, typeof E[nome] === 'function');
        for (const nome of ['abrir', 'verificarCopia', 'continuar', 'retomarGuardada', 'enviarManual', 'encerrar', 'temPendencia']) {
            confere('método ' + nome, typeof E.prototype[nome] === 'function');
        }

        // --- peças ---
        const seletor = I.opcoesDoSeletor('Relatório Mensal.pbix');
        confere('seletor sugere o nome do arquivo', seletor.suggestedName === 'Relatório Mensal.pbix');
        confere('seletor mantém a extensão', JSON.stringify(seletor.types[0].accept) === JSON.stringify({ 'application/octet-stream': ['.pbix'] }), JSON.stringify(seletor.types));
        confere('sem extensão, sem filtro de tipo', !I.opcoesDoSeletor('LEIAME').types);
        let r = await I.lerResposta(resposta(200, { ok: true, token: 'x' }));
        confere('JSON 200: sucesso', r.ok && r.token === 'x');
        r = await I.lerResposta(resposta(403, { error: 'Sem permissão para salvar.' }));
        confere('JSON com erro: falha com a mensagem do portal', !r.ok && r.erro === 'Sem permissão para salvar.' && !r.semJson);
        r = await I.lerResposta(resposta(200, null, 'text/html'));
        confere('página HTML (login) NÃO é sucesso', !r.ok && r.semJson && r.erro.includes('sessão'));
        r = await I.lerResposta(resposta(0, null, '', { type: 'opaqueredirect' }));
        confere('redirecionamento (sessão expirada) não é sucesso', !r.ok && r.semJson && r.erro.includes('sessão'));
        r = await I.lerResposta(resposta(502, null, 'text/html'));
        confere('erro 502 sem JSON diz o código', !r.ok && r.erro.includes('502'));

        // --- cópia de trabalho (PDF) ---
        const handle = arquivoFalso('Contrato.pdf');
        seletorAtual = handle;
        rotas[URL_INICIAR] = [resposta(200, sessaoArquivo('tok1'))];
        rotas['/drive/file/F1/content/?dl=1'] = [resposta(200, 'PDF-ORIGINAL', 'application/pdf')];
        const cfg = { fileId: 'F1', nome: 'Contrato.pdf', modo: 'arquivo', programa: '', podeSalvar: true, urlIniciar: URL_INICIAR };
        const ed = new E(cfg, ui);
        r = await ed.abrir();
        ed._pararDeOlhar();
        await dormir(10);
        confere('abre a cópia de trabalho', r === 'copia', r);
        confere('pede a pasta com o nome do arquivo', seletores.length === 1 && seletores[0].suggestedName === 'Contrato.pdf');
        const inicio = ultimoPedido(URL_INICIAR);
        confere('abre a edição no portal como cópia', inicio && inicio.opcoes.method === 'POST' && inicio.opcoes.body.get('modo') === 'arquivo');
        confere('com o CSRF do cookie', inicio && inicio.opcoes.headers['X-CSRFToken'] === 'token-do-cookie');
        confere('sem seguir redirecionamento às cegas', inicio && inicio.opcoes.redirect === 'manual');
        confere('grava o arquivo do Drive na cópia', handle.estado.conteudo === 'PDF-ORIGINAL', handle.estado.conteudo);
        confere('avisa onde está a cópia e para deixar a aba aberta',
                ultimo().tipo === 'ok' && ultimo().texto.includes('Contrato.pdf') && ultimo().texto.includes('deixe esta aba aberta'), ultimo().texto);
        confere('oferece enviar agora e encerrar', rotulos(ultimo()).join('|') === 'Enviar agora|Encerrar edição', rotulos(ultimo()));

        confere('sem mudança: não envia', await ed.verificarCopia() === 'igual');
        programaSalva(handle, 'PDF-EDITADO-PELA-METADE');
        confere('programa salvou: espera o arquivo parar de mudar', await ed.verificarCopia() === 'mudando');
        confere('enquanto espera, conta como pendência (aviso ao sair da página)', ed.temPendencia());
        programaSalva(handle, 'PDF-EDITADO');
        confere('ainda mudando: continua esperando', await ed.verificarCopia() === 'mudando');
        rotas['/drive/edicao/tok1/enviar/'] = [resposta(200, { ok: true, conflito: false })];
        r = await ed.verificarCopia();
        confere('parou de mudar: envia', r === 'enviado', r);
        const envio = ultimoPedido('/drive/edicao/tok1/enviar/');
        const enviado = envio && envio.opcoes.body.get('arquivo');
        confere('envia o arquivo final (não o do meio)', enviado && (await enviado.text()) === 'PDF-EDITADO');
        confere('com o nome do arquivo', enviado && enviado.name === 'Contrato.pdf', enviado && enviado.name);
        confere('com o CSRF do cookie no envio', envio && envio.opcoes.headers['X-CSRFToken'] === 'token-do-cookie');
        confere('avisa que salvou no Drive', ultimo().tipo === 'ok' && ultimo().texto.includes('Salvo no Drive'));
        confere('depois de enviar, não manda de novo',
                (await ed.verificarCopia()) === 'igual' && quantosPedidos('/drive/edicao/tok1/enviar/') === 1);

        // Conflito
        programaSalva(handle, 'V2');
        await ed.verificarCopia();
        rotas['/drive/edicao/tok1/enviar/'] = [resposta(200, { ok: true, conflito: true, nome: 'Contrato (conflito de edição — ZZ — 10-09-2026 10h00).pdf' })];
        r = await ed.verificarCopia();
        confere('conflito: a pessoa fica sabendo onde a versão dela foi parar',
                r === 'conflito' && ultimo().tipo === 'atencao' && ultimo().texto.includes('conflito de edição'), ultimo().texto);

        // Edição vencida: renova com a anterior e reenvia
        programaSalva(handle, 'V3');
        await ed.verificarCopia();
        rotas['/drive/edicao/tok1/enviar/'] = [resposta(410, { error: 'Esta edição venceu.', expirada: true })];
        rotas[URL_INICIAR] = [resposta(200, sessaoArquivo('tok2'))];
        rotas['/drive/edicao/tok2/enviar/'] = [resposta(200, { ok: true, conflito: false })];
        r = await ed.verificarCopia();
        const renovacao = ultimoPedido(URL_INICIAR);
        confere('vencida: renova a edição e envia', r === 'enviado', r);
        confere('renova dizendo qual edição continua (mantém a base do conflito)',
                renovacao.opcoes.body.get('anterior') === 'tok1' && renovacao.opcoes.body.get('modo') === 'arquivo');
        confere('passa a usar o token novo', ed.copia.token === 'tok2' && ed.copia.urlEnviar === '/drive/edicao/tok2/enviar/');
        const reenvio = ultimoPedido('/drive/edicao/tok2/enviar/');
        confere('reenvia o mesmo arquivo', reenvio && (await reenvio.opcoes.body.get('arquivo').text()) === 'V3');

        // Falha passageira: espera antes de tentar de novo
        programaSalva(handle, 'V4');
        await ed.verificarCopia();
        rotas['/drive/edicao/tok2/enviar/'] = [resposta(502, { error: 'O Google Drive não respondeu. Tente de novo em instantes.' })];
        r = await ed.verificarCopia();
        confere('falha passageira: avisa e a cópia continua', r === 'falhou' && ultimo().texto.includes('A cópia continua no seu computador'), ultimo().texto);
        confere('e espera antes de tentar de novo', (await ed.verificarCopia()) === 'esperando');
        rotas['/drive/edicao/tok2/enviar/'] = [resposta(200, { ok: true, conflito: false })];
        confere('"Enviar agora" não espera', (await ed.verificarCopia({ agora: true })) === 'enviado');

        // Sem rede
        programaSalva(handle, 'V5');
        await ed.verificarCopia();
        rotas['/drive/edicao/tok2/enviar/'] = [{ erroDeRede: true }];
        r = await ed.verificarCopia();
        confere('sem rede: falha passageira', r === 'falhou' && ultimo().texto.includes('Sem conexão'), ultimo().texto);
        ed._esperarAte = 0;

        // Recusado (limite, extensão): não insiste com o mesmo arquivo
        rotas['/drive/edicao/tok2/enviar/'] = [resposta(413, { error: 'O arquivo passa do limite de 100 MB.' })];
        r = await ed.verificarCopia({ agora: true });
        const antesDaRecusa = quantosPedidos('/drive/edicao/tok2/enviar/');
        confere('recusado pelo portal: mostra o motivo', r === 'recusado' && ultimo().tipo === 'erro' && ultimo().texto.includes('limite'), r);
        confere('não reenvia o mesmo arquivo recusado',
                (await ed.verificarCopia()) === 'recusado' && quantosPedidos('/drive/edicao/tok2/enviar/') === antesDaRecusa);

        // Sessão do portal expirada (HTML no lugar do JSON): não conta como salvo
        programaSalva(handle, 'V6');
        await ed.verificarCopia();
        rotas['/drive/edicao/tok2/enviar/'] = [resposta(200, null, 'text/html')];
        r = await ed.verificarCopia();
        confere('sessão do portal expirada: não conta como salvo e tenta depois', r === 'falhou' && ultimo().texto.includes('sessão'), r);
        ed._esperarAte = 0;

        // Permissão tirada: para de insistir
        rotas['/drive/edicao/tok2/enviar/'] = [resposta(403, { error: 'Você não tem permissão para salvar alterações neste arquivo.' })];
        r = await ed.verificarCopia({ agora: true });
        confere('sem permissão: para e avisa', r === 'proibido' && ultimo().tipo === 'erro' && ultimo().texto.includes('permissão'), ultimo().texto);

        // A cópia travada, sem permissão do navegador, apagada
        handle.estado.erroAoLer = 'NotReadableError';
        confere('programa com o arquivo travado: tenta na próxima olhada', (await ed.verificarCopia()) === 'ilegivel');
        handle.estado.erroAoLer = 'NotAllowedError';
        r = await ed.verificarCopia();
        confere('permissão do navegador perdida: pede para continuar', r === 'sem-permissao' && rotulos(ultimo()).includes('Continuar enviando as alterações'));
        handle.estado.erroAoLer = 'NotFoundError';
        r = await ed.verificarCopia();
        confere('cópia apagada ou renomeada: oferece enviar o arquivo', r === 'sumiu' && rotulos(ultimo()).includes('Enviar versão editada'));
        handle.estado.erroAoLer = '';

        // Continuar depois de recarregar (a permissão é pedida de novo)
        handle.estado.permissao = 'denied';
        confere('sem a permissão, não acompanha', (await ed.continuar()) === 'sem-permissao');
        handle.estado.permissao = 'granted';
        r = await ed.continuar();
        ed._pararDeOlhar();
        await dormir(10);
        confere('com a permissão, volta a acompanhar', r === 'olhando', r);

        // Encerrar envia o que faltou
        programaSalva(handle, 'V7-FINAL');
        rotas['/drive/edicao/tok2/enviar/'] = [resposta(200, { ok: true, conflito: false })];
        rotas['/drive/edicao/tok2/encerrar/'] = [resposta(200, { ok: true })];
        r = await ed.encerrar();
        const ultimoEnvio = ultimoPedido('/drive/edicao/tok2/enviar/');
        confere('encerrar envia antes a última versão salva', r === 'encerrado' && (await ultimoEnvio.opcoes.body.get('arquivo').text()) === 'V7-FINAL', r);
        confere('e fecha a edição no portal', quantosPedidos('/drive/edicao/tok2/encerrar/') === 1);
        confere('oferece apagar a cópia do computador', rotulos(ultimo()).includes('Apagar a cópia do computador'), rotulos(ultimo()));
        await ultimo().acoes[0].fazer();
        confere('apaga a cópia quando pedido', handle.estado.removido === true);
        confere('depois de encerrar, nada acompanhado', ed.copia === null && ed.sessao === null);

        // Cancelar o seletor não abre nada
        seletorAtual = Object.assign(new Error('cancelou'), { name: 'AbortError' });
        const antesDeCancelar = quantosPedidos(URL_INICIAR);
        const ed2 = new E(cfg, ui);
        confere('cancelar a escolha da pasta não abre edição', (await ed2.abrir()) === 'cancelado' && quantosPedidos(URL_INICIAR) === antesDeCancelar);

        // Sem File System Access: baixa e oferece enviar
        const seletorGuardado = globalThis.showSaveFilePicker;
        delete globalThis.showSaveFilePicker;
        confere('detecta navegador sem cópia acompanhada', E.suporteCopia() === false);
        rotas[URL_INICIAR] = [resposta(200, sessaoArquivo('tok3'))];
        r = await ed2.abrir();
        confere('sem o recurso: baixa o arquivo', r === 'download' && cliques.length === 1
                && cliques[0].href === '/drive/file/F1/content/?dl=1' && cliques[0].download === 'Contrato.pdf', JSON.stringify(cliques));
        confere('e oferece enviar a versão editada', rotulos(ultimo()).includes('Enviar versão editada'));
        rotas['/drive/edicao/tok3/enviar/'] = [resposta(200, { ok: true, conflito: false })];
        r = await ed2.enviarManual(new File(['EDITADO-A-MAO'], 'Contrato.pdf', { lastModified: 42 }));
        confere('envia a versão editada escolhida à mão', r === 'enviado'
                && (await ultimoPedido('/drive/edicao/tok3/enviar/').opcoes.body.get('arquivo').text()) === 'EDITADO-A-MAO', r);
        globalThis.showSaveFilePicker = seletorGuardado;

        // O portal recusou abrir (sessão expirada)
        rotas[URL_INICIAR] = [resposta(0, null, '', { type: 'opaqueredirect' })];
        seletorAtual = arquivoFalso('Contrato.pdf');
        const ed3 = new E(cfg, ui);
        confere('sessão expirada ao abrir: avisa', (await ed3.abrir()) === 'erro' && ultimo().tipo === 'erro' && ultimo().texto.includes('sessão'));

        // --- Office ---
        const cfgOffice = { fileId: 'F2', nome: 'Planilha.xlsx', modo: 'office', programa: 'Excel', podeSalvar: true, urlIniciar: '/drive/file/F2/editar-no-computador/' };
        const uri = 'ms-excel:ofe|u|https://portal.exemplo/drive/dav/tok9/Planilha.xlsx';
        rotas[cfgOffice.urlIniciar] = [resposta(200, {
            ok: true, token: 'tok9', modo: 'office', nome: 'Planilha.xlsx', programa: 'Excel', somente_leitura: false,
            uri_office: uri, url_dav: 'https://portal.exemplo/drive/dav/tok9/Planilha.xlsx',
            url_conteudo: '/drive/file/F2/content/?dl=1', url_status: '/drive/edicao/tok9/status/',
            url_enviar: '/drive/edicao/tok9/enviar/', url_encerrar: '/drive/edicao/tok9/encerrar/',
        })];
        const avisosAntes = avisos.length;
        const seletoresAntes = seletores.length;
        const office = new E(cfgOffice, ui);
        r = await office.abrir();
        clearTimeout(office._timerStatus);
        const primeiro = avisos[avisosAntes + 1];
        confere('Office: pede a edição como Office', ultimoPedido(cfgOffice.urlIniciar).opcoes.body.get('modo') === 'office');
        confere('Office: chama o programa pelo protocolo', globalThis.location.href === uri, globalThis.location.href);
        confere('Office: não pede pasta (o Office cuida da cópia temporária)', seletores.length === seletoresAntes);
        confere('Office: explica que o Ctrl+S salva no Drive', primeiro && primeiro.tipo === 'info' && primeiro.texto.includes('Ctrl+S'), primeiro && primeiro.texto);
        confere('Office: link para abrir de novo e encerrar', primeiro && rotulos(primeiro).join('|') === 'Abrir no Excel de novo|Encerrar edição'
                && primeiro.acoes[0].href === uri, primeiro && rotulos(primeiro));
        confere('Office sem sinal de que abriu: sugere conferir o Office e outro programa',
                r === 'office-sem-sinal' && ultimo().tipo === 'atencao' && rotulos(ultimo()).includes('Editar com outro programa'), r + ' ' + ultimo().texto);

        r = office._mostrarStatus({ ok: true, salvamentos: 0, aberto_no_programa: true, baixado: true, expirada: false, conflito: null });
        confere('status: aberto no programa', r === 'aberto' && ultimo().texto.includes('Aberto no Excel'), r);
        const salvo = { ok: true, salvamentos: 2, ultimo_salvamento_em: new Date().toISOString(), aberto_no_programa: true, baixado: true, expirada: false, conflito: null };
        r = office._mostrarStatus(salvo);
        confere('status: salvo, com quantas vezes', r === 'salvo' && ultimo().tipo === 'ok' && ultimo().texto.includes('(2 vezes)'), ultimo().texto);
        const quantosAvisos = avisos.length;
        confere('status igual não repete o aviso', office._mostrarStatus(salvo) === 'igual' && avisos.length === quantosAvisos);
        r = office._mostrarStatus({ ok: true, salvamentos: 3, aberto_no_programa: true, baixado: true, expirada: false,
                                    conflito: { nome: 'Planilha (conflito de edição — ZZ — 10-09-2026 10h00).xlsx', em: '2026-09-10T10:00:00Z' } });
        confere('status: conflito com o nome da cópia', r === 'conflito' && ultimo().texto.includes('Planilha (conflito de edição'));
        r = office._mostrarStatus({ ok: true, salvamentos: 3, expirada: true, conflito: null });
        confere('status: link vencido oferece enviar pelo portal', r === 'expirada' && rotulos(ultimo()).includes('Enviar versão editada'));

        respostaConfirm = false;
        confere('encerrar no Office pergunta antes (fechar o Excel)',
                (await office.encerrar()) === 'cancelado' && quantosPedidos('/drive/edicao/tok9/encerrar/') === 0);
        respostaConfirm = true;
        rotas['/drive/edicao/tok9/encerrar/'] = [resposta(200, { ok: true })];
        confere('encerrar no Office fecha a edição', (await office.encerrar()) === 'encerrado' && quantosPedidos('/drive/edicao/tok9/encerrar/') === 1);

        // Office em modo leitura (quem só pode baixar)
        rotas[cfgOffice.urlIniciar] = [resposta(200, {
            ok: true, token: 'tok10', modo: 'office', nome: 'Planilha.xlsx', programa: 'Excel', somente_leitura: true,
            uri_office: 'ms-excel:ofv|u|https://portal.exemplo/drive/dav/tok10/Planilha.xlsx',
            url_status: '/drive/edicao/tok10/status/', url_enviar: '/drive/edicao/tok10/enviar/', url_encerrar: '/drive/edicao/tok10/encerrar/',
        })];
        const leitura = new E(Object.assign({}, cfgOffice, { podeSalvar: false }), ui);
        const antesDaLeitura = avisos.length;
        await leitura.abrir();
        const avisoLeitura = avisos[antesDaLeitura + 1];
        confere('leitura: avisa o modo leitura', avisoLeitura && avisoLeitura.texto.includes('modo leitura'), avisoLeitura && avisoLeitura.texto);
        confere('leitura: sem encerrar e sem acompanhar salvamentos',
                avisoLeitura && !rotulos(avisoLeitura).includes('Encerrar edição') && leitura._timerStatus === null);
        confere('leitura: sem sugerir outro programa (não pode salvar)', !rotulos(ultimo()).includes('Editar com outro programa'));

        // "Não tem o Excel?": edita como cópia de trabalho
        seletorAtual = arquivoFalso('Planilha.xlsx');
        rotas[cfgOffice.urlIniciar] = [resposta(200, Object.assign(sessaoArquivo('tok11'), { nome: 'Planilha.xlsx', url_conteudo: '/drive/file/F2/content/?dl=1' }))];
        rotas['/drive/file/F2/content/?dl=1'] = [resposta(200, 'XLSX', 'application/octet-stream')];
        const outro = new E(cfgOffice, ui);
        r = await outro.abrir({ outroPrograma: true });
        outro._pararDeOlhar();
        confere('outro programa: abre como cópia de trabalho', r === 'copia' && ultimoPedido(cfgOffice.urlIniciar).opcoes.body.get('modo') === 'arquivo', r);
    } catch (e) {
        confere('roteiro sem exceção', false, e && e.stack);
    }
    console.log('RESULTADOS ' + JSON.stringify(resultados));
    process.exit(0);
})();
'''


def main():
    node = shutil.which('node')
    if not node:
        print('node não encontrado: teste pulado')
        return 0
    r = subprocess.run([node, '--check', MODULO], capture_output=True, text=True)
    t('o módulo tem sintaxe válida (node --check)', r.returncode == 0, r.stderr[-800:])

    with tempfile.TemporaryDirectory() as pasta:
        roteiro = os.path.join(pasta, 'roteiro.js')
        with open(roteiro, 'w', encoding='utf-8') as fh:
            fh.write(ROTEIRO)
        r = subprocess.run([node, roteiro, MODULO], capture_output=True, text=True, timeout=120)
    linha = next((l for l in r.stdout.splitlines() if l.startswith('RESULTADOS ')), '')
    if not linha:
        t('roteiro terminou', False, (r.stdout + r.stderr)[-2000:])
    else:
        for item in json.loads(linha[len('RESULTADOS '):]):
            t(item['nome'], item['ok'], item['extra'])

    print(f'\n{ok} OK / {fail} falhas')
    return 1 if fail else 0


if __name__ == '__main__':
    sys.exit(main())
