"""O módulo do gravador (static/js/rc-gravador.js), testado sem navegador.

O JavaScript roda no Node (22+) com o fetch simulado e sem IndexedDB: prova o
que decide se um pedaço de áudio foi mesmo salvo — sucesso só com JSON e
"received"; sessão expirada, página de login no lugar do JSON e CSRF recusado
pausam o envio em vez de contar a parte como enviada —, a ordem e a nova
tentativa do envio, o "encerrar" que não finaliza com partes pendentes sem a
pessoa confirmar e a conversa entre janelas pelo BroadcastChannel.

A gravação de verdade (microfone, IndexedDB) é conferida no navegador.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

BASE = os.path.dirname(os.path.abspath(__file__))
MODULO = os.path.join(BASE, 'static', 'js', 'rc-gravador.js')
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

// Um navegador mínimo: cookie do CSRF, eventos de página inertes, sem IndexedDB.
globalThis.document = {
    cookie: 'outro=1; csrftoken=token-do-cookie',
    visibilityState: 'visible',
    addEventListener() {},
    documentElement: { classList: { contains: () => false } },
    styleSheets: [],
};
globalThis.addEventListener = () => {};
globalThis.removeEventListener = () => {};

// As esperas do módulo (nova tentativa) viram quase instantâneas.
const setTimeoutOriginal = globalThis.setTimeout;
// Só as esperas de nova tentativa (até 30 s); a retomada de 60 s depois de
// sessão expirada fica como é, para o teste ver a fila parada.
globalThis.setTimeout = (fn, ms, ...args) => setTimeoutOriginal(fn, (ms || 0) <= 30000 ? Math.min(ms || 0, 15) : ms, ...args);

const respostas = [];
const pedidos = [];
globalThis.fetch = async (url, opcoes) => {
    pedidos.push({ url, opcoes });
    const r = respostas.shift();
    if (!r) throw new TypeError('fetch failed');
    if (r.erroDeRede) throw new TypeError('Failed to fetch');
    return r;
};
function resposta(status, corpo, tipo = 'application/json', extra = {}) {
    return Object.assign({
        status, ok: status >= 200 && status < 300, type: 'basic',
        headers: { get: (h) => (h.toLowerCase() === 'content-type' ? tipo : null) },
        json: async () => corpo,
    }, extra);
}
const recebida = (i, id = 42) => resposta(200, { received: true, chunk_index: i, transcription_id: id });

require(process.argv[2]);
const G = globalThis.RCGravador;
const I = G._interno;

(async () => {
    confere('expõe window.RCGravador', typeof G === 'function');
    for (const nome of ['pendentesLocais', 'retomar', 'finalizarSessao', 'descartarLocal', 'sessaoAtiva',
                        'enviarComando', 'ouvir', 'suporte', 'formatarTempo', 'novoUploadId']) {
        confere('estático ' + nome, typeof G[nome] === 'function');
    }
    for (const nome of ['iniciar', 'parar', 'finalizar', 'estado', 'abrirMiniPainel', 'descartar']) {
        confere('método ' + nome, typeof G.prototype[nome] === 'function');
    }
    confere('tempo mm:ss', G.formatarTempo(65) === '01:05', G.formatarTempo(65));
    confere('tempo h:mm:ss', G.formatarTempo(3725) === '1:02:05', G.formatarTempo(3725));
    confere('upload_id aceito pelo servidor', /^[a-zA-Z0-9_-]{8,80}$/.test(G.novoUploadId()));
    confere('mp4 do Safari vira .mp4', I.extensaoDoMime('audio/mp4') === '.mp4');
    confere('ogg vira .ogg', I.extensaoDoMime('audio/ogg;codecs=opus') === '.ogg');
    confere('webm vira .webm', I.extensaoDoMime('audio/webm;codecs=opus') === '.webm');
    confere('espera cresce e para em 30 s', I.backoff(1) === 2400 && I.backoff(30) === 30000, I.backoff(1));

    const sessao = { uploadId: 'zz-sessao-0001', titulo: 'ZZ Título', eventId: 9, origem: 'reuniao' };
    const blob = new Blob(['audio']);

    respostas.push(recebida(3, 77));
    let r = await I.enviarParte('/chunk/', 'token-da-pagina', sessao, 3, blob, '.webm');
    const corpo = pedidos[pedidos.length - 1].opcoes.body;
    confere('parte confirmada com JSON e received: salva', r.ok && r.transcricaoId === 77, JSON.stringify(r));
    confere('manda o CSRF atual do cookie (muda depois de entrar de novo)',
            pedidos[pedidos.length - 1].opcoes.headers['X-CSRFToken'] === 'token-do-cookie');
    confere('não segue redirecionamento (login) às cegas', pedidos[pedidos.length - 1].opcoes.redirect === 'manual');
    confere('manda índice, sessão, origem, título e evento',
            corpo.get('upload_id') === 'zz-sessao-0001' && corpo.get('chunk_index') === '3' && corpo.get('origem') === 'reuniao'
            && corpo.get('title') === 'ZZ Título' && corpo.get('event_id') === '9');
    confere('com a extensão certa no nome do arquivo', corpo.get('audio').name === 'parte-3.webm', corpo.get('audio').name);

    respostas.push(resposta(401, { error: 'Sua sessão expirou', sessao_expirada: true }));
    r = await I.enviarParte('/chunk/', '', sessao, 0, blob, '.webm');
    confere('401: sessão expirada, não salva', !r.ok && r.sessaoExpirada, JSON.stringify(r));

    respostas.push(resposta(200, null, 'text/html'));
    r = await I.enviarParte('/chunk/', '', sessao, 0, blob, '.webm');
    confere('200 com página HTML (login) NÃO conta como salva', !r.ok && r.sessaoExpirada, JSON.stringify(r));

    respostas.push(resposta(0, null, '', { type: 'opaqueredirect' }));
    r = await I.enviarParte('/chunk/', '', sessao, 0, blob, '.webm');
    confere('redirecionamento: sessão expirada', !r.ok && r.sessaoExpirada);

    respostas.push(resposta(403, { error: 'Esta gravação é de outra pessoa.' }));
    r = await I.enviarParte('/chunk/', '', sessao, 0, blob, '.webm');
    confere('403 com motivo: proibido (para de insistir)', !r.ok && r.proibido && r.mensagem.includes('outra pessoa'));

    respostas.push(resposta(403, null, 'text/html'));
    r = await I.enviarParte('/chunk/', '', sessao, 0, blob, '.webm');
    confere('403 sem JSON (CSRF depois de novo login): pausa e tenta depois', !r.ok && r.sessaoExpirada && !r.proibido);

    respostas.push(resposta(503, { error: 'Falha ao salvar a parte no armazenamento.' }));
    r = await I.enviarParte('/chunk/', '', sessao, 0, blob, '.webm');
    confere('503: falha passageira com a mensagem do servidor', !r.ok && !r.sessaoExpirada && r.mensagem.includes('armazenamento'));

    respostas.push({ erroDeRede: true });
    r = await I.enviarParte('/chunk/', '', sessao, 0, blob, '.webm');
    confere('sem rede: falha passageira', !r.ok && !r.sessaoExpirada && r.mensagem.includes('conexão'));

    respostas.push(resposta(202, { id: 5, redirect: '/agenda/transcricoes/5/' }));
    r = await I.postarFormulario('/finalize/', '', { upload_id: 'x', vazio: '', nulo: null });
    const campos = pedidos[pedidos.length - 1].opcoes.body;
    confere('finalizar: devolve os dados', r.ok && r.dados.id === 5);
    confere('e não manda campo vazio', !campos.has('vazio') && !campos.has('nulo') && campos.get('upload_id') === 'x');

    confere('sem IndexedDB, guardar parte avisa que não guardou', (await I.guardarParte('x', 0, blob)) === false);
    confere('e não há pendências locais', (await G.pendentesLocais()).length === 0);

    // --- a fila de envio de uma gravação ---
    const erros = [];
    let ultimo = null;
    const g = new G({
        urls: { chunk: '/chunk/', finalize: '/finalize/' },
        sessao: { uploadId: 'zz-fila-0001', titulo: 'ZZ Fila' },
        onEstado: (e) => { ultimo = e; },
        onErro: (m) => erros.push(m),
    });
    pedidos.length = 0;
    respostas.push({ erroDeRede: true }, recebida(0), recebida(1), recebida(2));
    g._aoReceberDados({ data: new Blob(['a']) });
    g._aoReceberDados({ data: new Blob(['b']) });
    g._aoReceberDados({ data: new Blob(['c']) });
    for (let i = 0; i < 100 && g._pendentes(); i++) await dormir(10);
    const ordem = pedidos.map((p) => p.opcoes.body.get('chunk_index')).join(',');
    confere('envia em ordem e tenta de novo a que falhou', ordem === '0,0,1,2', ordem);
    confere('todas confirmadas', ultimo.partes.enviadas === 3 && ultimo.partes.pendentes === 0, JSON.stringify(ultimo.partes));
    confere('avisa que não há cópia local (sem IndexedDB)', ultimo.semArmazenamentoLocal === true);

    const g2 = new G({ urls: { chunk: '/chunk/', finalize: '/finalize/' }, sessao: { uploadId: 'zz-fila-0002' },
                       onErro: (m) => erros.push(m) });
    pedidos.length = 0;
    respostas.push(resposta(401, { error: 'Sua sessão no portal expirou.', sessao_expirada: true }));
    g2._aoReceberDados({ data: new Blob(['a']) });
    g2._aoReceberDados({ data: new Blob(['b']) });
    for (let i = 0; i < 30; i++) await dormir(5);
    confere('sessão expirada pausa a fila (não queima as outras partes)', g2._pausado && pedidos.length === 1, pedidos.length);
    confere('e avisa a pessoa', erros.some((m) => m.includes('expirou')));
    confere('nada é dado como enviado', g2.estado().partes.enviadas === 0 && g2.estado().partes.pendentes === 2);
    respostas.push(recebida(0), recebida(1));
    g2._pausado = false;
    g2._acordarEnvio();
    for (let i = 0; i < 100 && g2._pendentes(); i++) await dormir(10);
    confere('ao voltar, envia tudo', g2.estado().partes.enviadas === 2, JSON.stringify(g2.estado().partes));

    const g3 = new G({ urls: { chunk: '/chunk/', finalize: '/finalize/' }, sessao: { uploadId: 'zz-fila-0003', titulo: 'ZZ' } });
    g3._aoReceberDados({ data: new Blob(['a']) });
    let fim = await g3.finalizar({ esperarPendentesMs: 30 });
    confere('encerrar com parte que não subiu: devolve as pendências, sem finalizar', fim && fim.pendentes === 1, JSON.stringify(fim));
    // A rede das partes continua fora; só o "finalizar" responde.
    const fetchDaFila = globalThis.fetch;
    globalThis.fetch = async (url, opcoes) => {
        pedidos.push({ url, opcoes });
        if (url === '/finalize/') return resposta(202, { id: 88, redirect: '/agenda/transcricoes/88/', missing_chunks: 1, parts_received: 0 });
        throw new TypeError('Failed to fetch');
    };
    pedidos.length = 0;
    const ouvinte = new BroadcastChannel('rc-gravador');
    const recebidas = [];
    ouvinte.onmessage = (ev) => recebidas.push(ev.data);
    g3._pausado = true;   // simula a fila parada para o "forçar"
    fim = await g3.finalizar({ forcar: true, esperarPendentesMs: 0 });
    await dormir(20);
    confere('forçando, finaliza com o que chegou', fim.id === 88 && g3.estado().fase === 'concluido', JSON.stringify(fim));
    const finalizou = pedidos.find((p) => p.url === '/finalize/');
    confere('conta as partes geradas para o servidor apontar o buraco', finalizou && finalizou.opcoes.body.get('total_chunks') === '1');
    confere('as outras janelas ficam sabendo', recebidas.some((m) => m.tipo === 'concluido' && m.transcricaoId === 88));
    globalThis.fetch = fetchDaFila;

    // --- conversa entre janelas ---
    const g4 = new G({ urls: {}, sessao: { uploadId: 'zz-janela-0001' }, onComando: (c, d) => comandos.push([c, d]) });
    const comandos = [];
    g4._fase = 'gravando';
    recebidas.length = 0;
    ouvinte.postMessage({ tipo: 'ping', uploadId: 'zz-janela-0001' });
    await dormir(30);
    confere('a janela que grava responde ao "ping"', recebidas.some((m) => m.tipo === 'pong' && m.uploadId === 'zz-janela-0001'));
    ouvinte.postMessage({ tipo: 'comando', uploadId: 'zz-janela-0001', comando: 'parar_e_processar', dados: { titulo: 'Novo' } });
    ouvinte.postMessage({ tipo: 'comando', uploadId: 'outra-sessao-01', comando: 'parar_e_processar' });
    await dormir(30);
    confere('recebe o comando da própria sessão', comandos.length === 1 && comandos[0][0] === 'parar_e_processar' && comandos[0][1].titulo === 'Novo',
            JSON.stringify(comandos));

    const outraJanela = new BroadcastChannel('rc-gravador');
    outraJanela.onmessage = (ev) => {
        if (ev.data.tipo === 'ping' && ev.data.uploadId === 'zz-remota-0001') {
            outraJanela.postMessage({ tipo: 'pong', uploadId: 'zz-remota-0001', estado: { tempo: '12:00' } });
        }
    };
    const ativa = await G.sessaoAtiva('zz-remota-0001', 200);
    confere('sessaoAtiva: acha a gravação aberta em outra janela', ativa && ativa.tempo === '12:00', JSON.stringify(ativa));
    confere('sessaoAtiva: gravação que ninguém responde', (await G.sessaoAtiva('zz-ninguem-0001', 60)) === false);
    recebidas.length = 0;
    G.enviarComando('zz-remota-0001', 'focar');
    await dormir(30);
    confere('enviarComando chega nas outras janelas', recebidas.some((m) => m.tipo === 'comando' && m.comando === 'focar'));

    ouvinte.close();
    outraJanela.close();
    console.log(JSON.stringify(resultados));
    process.exit(0);
})().catch((e) => {
    console.log(JSON.stringify(resultados.concat([{ nome: 'o roteiro rodou até o fim', ok: false, extra: e.stack }])));
    process.exit(0);
});
'''

print('== O ARQUIVO ==')
node = shutil.which('node')
t('Node disponível', bool(node))
t('o módulo existe', os.path.exists(MODULO))
if node and os.path.exists(MODULO):
    r = subprocess.run([node, '--check', MODULO], capture_output=True, text=True)
    t('sintaxe válida (node --check)', r.returncode == 0, r.stderr[:500])
    versao = subprocess.run([node, '--version'], capture_output=True, text=True).stdout.strip()
    t(f'Node 22+ (tem BroadcastChannel, FormData e Blob) — {versao}', int(versao.lstrip('v').split('.')[0]) >= 22)

    codigo = open(MODULO, encoding='utf-8').read()
    t('não usa módulo ES (carrega como <script> comum)', 'export ' not in codigo and 'import ' not in codigo)
    t('lê o CSRF do cookie a cada envio', 'csrfAtual(' in codigo)
    t('guarda cada parte no IndexedDB antes de subir', 'guardarParte(self.sessao.uploadId' in codigo)

    print('\n== O COMPORTAMENTO (no Node) ==')
    with tempfile.TemporaryDirectory() as pasta:
        roteiro = os.path.join(pasta, 'roteiro.js')
        with open(roteiro, 'w', encoding='utf-8') as fh:
            fh.write(ROTEIRO)
        r = subprocess.run([node, roteiro, MODULO], capture_output=True, text=True, timeout=120)
    linhas = [l for l in r.stdout.splitlines() if l.startswith('[')]
    try:
        resultados = json.loads(linhas[-1])
    except (IndexError, ValueError):
        resultados = [{'nome': 'o roteiro devolveu resultados', 'ok': False, 'extra': (r.stdout + r.stderr)[-1500:]}]
    for item in resultados:
        t(item['nome'], item['ok'], item.get('extra', ''))

print(f'\n{ok} OK / {fail} falhas')
sys.exit(1 if fail else 0)
