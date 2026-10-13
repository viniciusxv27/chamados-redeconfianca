/* E-mail institucional (/email/) — o cliente que roda na página.
 *
 * Pastas, lista, leitura e envio conversam com /email/api/… (webmail/views.py).
 * O e-mail aberto é desenhado num <iframe sandbox> sem scripts; o HTML já vem
 * limpo do servidor. Nada aqui guarda senha nem conteúdo no navegador.
 */
(function () {
    'use strict';

    const CFG = JSON.parse(document.getElementById('wm-config').textContent);
    const ASSINATURA_PADRAO = JSON.parse(document.getElementById('wm-assinatura-padrao').textContent) || '';
    let ASSINATURA = JSON.parse(document.getElementById('wm-assinatura').textContent) || '';
    const URL = CFG.urls;

    const app = document.getElementById('wmApp');
    const itensEl = document.getElementById('wmItens');
    const leituraEl = document.getElementById('wmLeitura');
    const buscaEl = document.getElementById('wmBusca');

    const E = {
        pastas: [], pasta: 'INBOX', pagina: 1, paginas: 1, total: 0, busca: '', filtro: '',
        lista: [], marcadas: new Set(), ultimaMarcada: null, aberta: null, foco: -1, msg: null,
        uidnext: 0, carregandoLista: 0, contatos: new Map(), resumo: null,
    };

    // ── Utilidades ────────────────────────────────────────────────────────
    const $ = (sel, raiz) => (raiz || document).querySelector(sel);
    const $$ = (sel, raiz) => Array.from((raiz || document).querySelectorAll(sel));
    function esc(s) {
        return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
    }
    function cria(html) {
        const t = document.createElement('template');
        t.innerHTML = html.trim();
        return t.content.firstElementChild;
    }
    const CORES = ['#F97316', '#EF4444', '#EC4899', '#A855F7', '#6366F1', '#3B82F6', '#0EA5E9', '#14B8A6', '#10B981', '#84CC16', '#EAB308', '#F43F5E'];
    function cor(chave) {
        let h = 0;
        for (const c of String(chave || '').toLowerCase()) h = (h * 31 + c.charCodeAt(0)) >>> 0;
        return CORES[h % CORES.length];
    }
    function iniciais(nome, email) {
        const base = (nome || '').replace(/[^\p{L}\s]/gu, ' ').trim() || (email || '?').split('@')[0].replace(/[._-]+/g, ' ');
        const partes = base.split(/\s+/).filter(Boolean);
        return ((partes[0] || '?')[0] + (partes.length > 1 ? partes[partes.length - 1][0] : '')).toUpperCase();
    }
    function avatar(pessoa, classe) {
        pessoa = pessoa || {};
        return `<span class="wm-avatar ${classe || ''}" style="background:${cor(pessoa.email || pessoa.nome)}">${esc(iniciais(pessoa.nome, pessoa.email))}</span>`;
    }
    function nomeDe(p) { return p ? (p.nome || p.email || '(sem remetente)') : '(sem remetente)'; }
    function eu(email) { return (email || '').toLowerCase() === CFG.email.toLowerCase(); }

    const MESES = ['jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez'];
    const DIAS = ['domingo', 'segunda', 'terça', 'quarta', 'quinta', 'sexta', 'sábado'];
    function mesmaData(a, b) { return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate(); }
    function dataCurta(iso) {
        if (!iso) return '';
        const d = new Date(iso), hoje = new Date();
        if (mesmaData(d, hoje)) return d.toLocaleTimeString('pt-BR', {hour: '2-digit', minute: '2-digit'});
        const ontem = new Date(hoje); ontem.setDate(hoje.getDate() - 1);
        if (mesmaData(d, ontem)) return 'ontem';
        if (d.getFullYear() === hoje.getFullYear()) return `${d.getDate()} ${MESES[d.getMonth()]}`;
        return d.toLocaleDateString('pt-BR', {day: '2-digit', month: '2-digit', year: '2-digit'});
    }
    function dataLonga(iso) {
        if (!iso) return '';
        const d = new Date(iso);
        return `${DIAS[d.getDay()]}, ${d.getDate()} de ${MESES[d.getMonth()]}. de ${d.getFullYear()}, ${d.toLocaleTimeString('pt-BR', {hour: '2-digit', minute: '2-digit'})}`;
    }
    function relativo(iso) {
        if (!iso) return '';
        const seg = (Date.now() - new Date(iso).getTime()) / 1000;
        if (seg < 60) return 'agora';
        if (seg < 3600) return `há ${Math.floor(seg / 60)} min`;
        if (seg < 86400) return `há ${Math.floor(seg / 3600)} h`;
        const dias = Math.floor(seg / 86400);
        return dias === 1 ? 'há 1 dia' : `há ${dias} dias`;
    }
    function grupoData(iso) {
        if (!iso) return 'Sem data';
        const d = new Date(iso), hoje = new Date();
        const inicioHoje = new Date(hoje.getFullYear(), hoje.getMonth(), hoje.getDate());
        const dias = Math.floor((inicioHoje - new Date(d.getFullYear(), d.getMonth(), d.getDate())) / 86400000);
        if (dias <= 0) return 'Hoje';
        if (dias === 1) return 'Ontem';
        if (dias < 7) return 'Nesta semana';
        if (d.getMonth() === hoje.getMonth() && d.getFullYear() === hoje.getFullYear()) return 'Neste mês';
        if (d.getFullYear() === hoje.getFullYear()) return MESES[d.getMonth()].replace(/^./, c => c.toUpperCase()) + '.';
        return String(d.getFullYear());
    }
    function tamanho(b) {
        if (!b) return '';
        if (b < 1024) return b + ' B';
        if (b < 1048576) return Math.round(b / 1024) + ' KB';
        if (b < 1073741824) return (b / 1048576).toFixed(1).replace('.', ',') + ' MB';
        return (b / 1073741824).toFixed(1).replace('.', ',') + ' GB';
    }
    function debounce(fn, ms) {
        let t;
        return function () { clearTimeout(t); const a = arguments; t = setTimeout(() => fn.apply(this, a), ms); };
    }

    // ── Avisos ────────────────────────────────────────────────────────────
    function aviso(texto, opcoes) {
        opcoes = opcoes || {};
        const caixa = document.getElementById('wmAvisos');
        const el = cria(`<div class="wm-aviso ${opcoes.erro ? 'wm-erro' : ''}"><i class="fas ${opcoes.erro ? 'fa-circle-exclamation' : (opcoes.icone || 'fa-circle-check')}"></i><span></span></div>`);
        el.querySelector('span').textContent = texto;
        if (opcoes.acao) {
            const b = cria('<button type="button"></button>');
            b.textContent = opcoes.acao.rotulo;
            b.addEventListener('click', () => { opcoes.acao.fn(); el.remove(); });
            el.appendChild(b);
        }
        caixa.appendChild(el);
        setTimeout(() => { el.style.transition = 'opacity .3s'; el.style.opacity = '0'; setTimeout(() => el.remove(), 320); }, opcoes.erro ? 6000 : 3800);
    }

    // ── API ───────────────────────────────────────────────────────────────
    async function api(url, opcoes) {
        opcoes = opcoes || {};
        let destino = url;
        if (opcoes.params) destino += '?' + new URLSearchParams(opcoes.params).toString();
        const init = {method: opcoes.method || 'GET', headers: {'X-Requested-With': 'XMLHttpRequest'}, credentials: 'same-origin'};
        if (init.method !== 'GET') init.headers['X-CSRFToken'] = CFG.csrf;
        if (opcoes.json) { init.body = JSON.stringify(opcoes.json); init.headers['Content-Type'] = 'application/json'; }
        if (opcoes.form) init.body = opcoes.form;
        let r;
        try {
            r = await fetch(destino, init);
        } catch (e) {
            throw new Error('Sem conexão com o portal. Confira a internet.');
        }
        let dados = {};
        try { dados = await r.json(); } catch (e) { dados = {}; }
        if (r.status === 401 && dados.reconectar) {
            window.location.href = URL.caixa;
            throw new Error(dados.erro || 'Entre de novo.');
        }
        if (!r.ok || dados.ok === false) throw new Error(dados.erro || `Erro ${r.status}`);
        return dados;
    }

    // ── Altura: o app ocupa a tela do portal sem rolar a página ───────────
    function ajustarAltura() {
        const topo = app.getBoundingClientRect().top + window.scrollY;
        const folga = window.innerWidth < 640 ? 8 : 20;
        app.style.height = Math.max(520, window.innerHeight - topo - folga) + 'px';
    }
    window.addEventListener('resize', debounce(ajustarAltura, 80));

    // ── Pastas ────────────────────────────────────────────────────────────
    const ICONE_PAPEL = {entrada: 'fa-inbox', enviados: 'fa-paper-plane', rascunhos: 'fa-file-signature', lixeira: 'fa-trash-can',
        spam: 'fa-shield-virus', arquivo: 'fa-box-archive'};
    function pastaPorPapel(papel) { return E.pastas.find(p => p.papel === papel); }
    function pastaAtual() { return E.pastas.find(p => p.id === E.pasta) || {id: E.pasta, nome: E.pasta, papel: E.pasta === 'INBOX' ? 'entrada' : ''}; }

    async function carregarPastas() {
        try {
            const d = await api(URL.pastas);
            E.pastas = d.pastas;
            desenharPastas();
            atualizarTitulo();
        } catch (e) {
            aviso(e.message, {erro: true});
        }
    }
    const recarregarPastas = debounce(carregarPastas, 600);

    function botaoPasta(p) {
        const contador = p.papel === 'rascunhos' || p.papel === 'enviados' || p.papel === 'lixeira'
            ? (p.papel === 'rascunhos' && p.total ? `<span class="wm-contador wm-neutro">${p.total}</span>` : '')
            : (p.nao_lidas ? `<span class="wm-contador">${p.nao_lidas > 999 ? '999+' : p.nao_lidas}</span>` : '');
        const recuo = p.papel ? '' : `style="padding-left:${0.7 + Math.min(p.nivel, 4) * 0.85}rem"`;
        return `<button type="button" class="wm-pasta ${p.id === E.pasta ? 'wm-ativa' : ''}" data-pasta="${esc(p.id)}" ${recuo} title="${esc(p.caminho || p.nome)}">
            <i class="fas ${ICONE_PAPEL[p.papel] || 'fa-folder'}"></i><span class="wm-nome">${esc(p.nome)}</span>${contador}</button>`;
    }
    function desenharPastas() {
        const principais = E.pastas.filter(p => p.papel);
        const minhas = E.pastas.filter(p => !p.papel);
        document.getElementById('wmPastasPrincipais').innerHTML = principais.map(botaoPasta).join('');
        document.getElementById('wmPastasMinhas').innerHTML = minhas.map(botaoPasta).join('');
        document.getElementById('wmTituloMinhas').hidden = !minhas.length;
        document.getElementById('wmNovaPastaSolta').hidden = !!minhas.length;
        document.getElementById('wmNomePasta').textContent = E.busca ? `Busca: “${E.busca}”` : pastaAtual().nome;
        buscaEl.placeholder = `Buscar em ${pastaAtual().nome}`;
    }
    function atualizarTitulo() {
        const entrada = pastaPorPapel('entrada');
        const n = entrada ? entrada.nao_lidas : 0;
        document.title = (n ? `(${n}) ` : '') + 'E-mail — ' + CFG.email;
        if (window.rcEmailBadge) window.rcEmailBadge(n);
    }

    function irParaPasta(id, opcoes) {
        opcoes = opcoes || {};
        if (E.pasta === id && !E.busca && !opcoes.forcar) {
            fecharPastasMovel();
            return;
        }
        E.pasta = id; E.pagina = 1; E.marcadas.clear(); E.foco = -1;
        if (!opcoes.manterBusca) { E.busca = ''; buscaEl.value = ''; }
        desenharPastas();
        fecharPastasMovel();
        carregarLista();
        historico(false);
    }

    // ── Lista ─────────────────────────────────────────────────────────────
    function esqueletoLista() {
        itensEl.innerHTML = Array.from({length: 8}, () => `<div class="wm-item" style="cursor:default">
            <span class="wm-esqueleto" style="width:2.35rem;height:2.35rem;border-radius:999px;flex-shrink:0"></span>
            <div class="flex-1 space-y-2 py-1"><div class="wm-esqueleto h-3 w-2/5"></div><div class="wm-esqueleto h-3 w-4/5"></div><div class="wm-esqueleto h-2.5 w-3/5"></div></div></div>`).join('');
    }

    async function carregarLista(opcoes) {
        opcoes = opcoes || {};
        const pedido = ++E.carregandoLista;
        if (!opcoes.silencioso) esqueletoLista();
        try {
            const d = await api(URL.lista, {params: {pasta: E.pasta, pagina: E.pagina, busca: E.busca, filtro: E.filtro}});
            if (pedido !== E.carregandoLista) return;
            E.lista = d.mensagens; E.total = d.total; E.paginas = d.paginas;
            if (E.msg) { const aberta = E.lista.find(m => m.uid === E.msg.uid); if (aberta) aberta.lida = true; }
            E.lista.forEach(m => lembrarContato(m.de));
            const visiveis = new Set(E.lista.map(m => m.uid));
            E.marcadas.forEach(u => { if (!visiveis.has(u)) E.marcadas.delete(u); });
            desenharLista();
        } catch (e) {
            if (pedido !== E.carregandoLista) return;
            itensEl.innerHTML = `<div class="wm-vazio"><div class="wm-vazio-icone"><i class="fas fa-plug"></i></div>
                <p class="font-semibold" style="color:var(--wm-texto)">Não deu para abrir a pasta</p><p class="text-sm mt-1">${esc(e.message)}</p>
                <button type="button" class="wm-botao mt-4" data-wm="atualizar"><i class="fas fa-rotate-right"></i>Tentar de novo</button></div>`;
        }
    }

    function linhaPessoas(m) {
        const papel = pastaAtual().papel;
        if (papel === 'enviados' || papel === 'rascunhos') {
            const para = (m.para || []).map(nomeDe).join(', ');
            return (papel === 'rascunhos' ? '<span style="color:#DC2626;font-weight:600">Rascunho</span> · ' : 'Para: ') + esc(para || '(sem destinatário)');
        }
        return esc(nomeDe(m.de));
    }

    function itemHtml(m) {
        const pessoa = (pastaAtual().papel === 'enviados' || pastaAtual().papel === 'rascunhos') ? (m.para || [])[0] : m.de;
        const classes = ['wm-item', m.lida ? '' : 'wm-nao-lida', E.aberta === m.uid ? 'wm-aberto' : '', E.marcadas.has(m.uid) ? 'wm-marcado' : ''].join(' ');
        return `<div class="${classes}" data-uid="${m.uid}" draggable="true" role="option" aria-selected="${E.aberta === m.uid}">
            <div class="wm-avatar-cx" data-acao="marcar" title="Selecionar">${avatar(pessoa)}<span class="wm-check"><i class="fas fa-check"></i></span></div>
            <div class="wm-item-miolo">
                <div class="wm-item-linha">
                    ${m.lida ? '' : '<span class="wm-ponto"></span>'}
                    <span class="wm-item-de">${linhaPessoas(m)}</span>
                    <span class="wm-item-icones">${m.respondida ? '<i class="fas fa-reply" title="Respondida"></i>' : ''}${m.anexo ? '<i class="fas fa-paperclip" title="Tem anexo"></i>' : ''}</span>
                    <span class="wm-item-data" title="${esc(dataLonga(m.data))}">${esc(dataCurta(m.data))}</span>
                </div>
                <div class="wm-item-linha">
                    <span class="wm-item-assunto flex-1">${esc(m.assunto)}</span>
                    <button type="button" class="wm-estrela ${m.sinalizada ? 'wm-ligada' : ''}" data-acao="estrela" title="${m.sinalizada ? 'Tirar de Importante' : 'Marcar como Importante'}"><i class="${m.sinalizada ? 'fas' : 'far'} fa-star text-[12px]"></i></button>
                </div>
                ${m.previa ? `<div class="wm-item-previa">${esc(m.previa)}</div>` : ''}
            </div>
            <div class="wm-acoes-rapidas">
                <button type="button" data-rapida="arquivo" title="Arquivar"><i class="fas fa-box-archive"></i></button>
                <button type="button" data-rapida="lixeira" title="Excluir"><i class="far fa-trash-can"></i></button>
                <button type="button" data-rapida="${m.lida ? 'nao_lida' : 'lida'}" title="${m.lida ? 'Marcar como não lida' : 'Marcar como lida'}"><i class="far ${m.lida ? 'fa-envelope' : 'fa-envelope-open'}"></i></button>
            </div>
        </div>`;
    }

    function desenharLista() {
        document.getElementById('wmNomePasta').textContent = E.busca ? `Busca: “${E.busca}”` : pastaAtual().nome;
        const ini = E.total ? (E.pagina - 1) * 50 + 1 : 0;
        document.getElementById('wmFaixa').textContent = E.total ? `${ini}–${ini + E.lista.length - 1} de ${E.total}` : '';
        $('[data-wm="pagina-anterior"]').disabled = E.pagina <= 1;
        $('[data-wm="pagina-seguinte"]').disabled = E.pagina >= E.paginas;
        if (!E.lista.length) {
            const papel = pastaAtual().papel;
            const [icone, titulo, texto] = E.busca ? ['fa-magnifying-glass', 'Nada encontrado', 'Tente outras palavras ou procure em outra pasta.']
                : E.filtro ? ['fa-filter', 'Nada com esse filtro', 'Volte para “Todas” para ver a pasta inteira.']
                : papel === 'entrada' ? ['fa-mug-hot', 'Caixa de entrada vazia', 'Nada por aqui. Aproveite o café!']
                : papel === 'lixeira' ? ['fa-trash-can', 'Lixeira vazia', 'O que você excluir fica aqui.']
                : papel === 'rascunhos' ? ['fa-file-signature', 'Nenhum rascunho', 'O que você começar a escrever e não enviar aparece aqui.']
                : ['fa-folder-open', 'Pasta vazia', 'Arraste mensagens para cá para organizar.'];
            itensEl.innerHTML = `<div class="wm-vazio"><div class="wm-vazio-icone"><i class="fas ${icone}"></i></div>
                <p class="font-semibold" style="color:var(--wm-texto)">${titulo}</p><p class="text-sm mt-1">${texto}</p></div>`;
            atualizarLote();
            return;
        }
        let html = '', grupo = null;
        for (const m of E.lista) {
            const g = grupoData(m.data);
            if (g !== grupo) { html += `<div class="wm-grupo-data">${esc(g)}</div>`; grupo = g; }
            html += itemHtml(m);
        }
        itensEl.innerHTML = html;
        atualizarLote();
        marcarFoco();
    }

    function itemEl(uid) { return itensEl.querySelector(`.wm-item[data-uid="${uid}"]`); }
    function msgDaLista(uid) { return E.lista.find(m => m.uid === uid); }
    function redesenharItem(uid) {
        const m = msgDaLista(uid), el = itemEl(uid);
        if (m && el) el.replaceWith(cria(itemHtml(m)));
    }

    function atualizarLote() {
        const n = E.marcadas.size;
        app.classList.toggle('wm-modo-selecao', n > 0);
        document.getElementById('wmTituloLista').classList.toggle('hidden', n > 0);
        const lote = document.getElementById('wmAcoesLote');
        lote.classList.toggle('hidden', n === 0);
        lote.classList.toggle('flex', n > 0);
        document.getElementById('wmPaginacao').classList.toggle('hidden', n > 0);
        document.getElementById('wmQtdSelecionadas').textContent = n === 1 ? '1 selecionada' : `${n} selecionadas`;
        const todas = E.lista.length && E.lista.every(m => E.marcadas.has(m.uid));
        document.getElementById('wmIconeTodas').className = todas ? 'fas fa-square-check text-orange-500' : (n ? 'fas fa-square-minus text-orange-500' : 'far fa-square');
        $$('.wm-item', itensEl).forEach(el => el.classList.toggle('wm-marcado', E.marcadas.has(Number(el.dataset.uid))));
    }

    function marcar(uid, comShift) {
        if (comShift && E.ultimaMarcada != null) {
            const a = E.lista.findIndex(m => m.uid === E.ultimaMarcada), b = E.lista.findIndex(m => m.uid === uid);
            if (a >= 0 && b >= 0) {
                for (let i = Math.min(a, b); i <= Math.max(a, b); i++) E.marcadas.add(E.lista[i].uid);
                atualizarLote();
                return;
            }
        }
        if (E.marcadas.has(uid)) E.marcadas.delete(uid); else E.marcadas.add(uid);
        E.ultimaMarcada = uid;
        atualizarLote();
    }

    function marcarFoco() {
        $$('.wm-item.wm-foco', itensEl).forEach(el => el.classList.remove('wm-foco'));
        const m = E.lista[E.foco];
        if (!m) return;
        const el = itemEl(m.uid);
        if (!el) return;
        el.classList.add('wm-foco');
        // Rola só a lista (scrollIntoView rolaria a página e esconderia o topo).
        const caixa = itensEl.getBoundingClientRect(), r = el.getBoundingClientRect();
        if (r.top < caixa.top + 28) itensEl.scrollTop -= caixa.top + 28 - r.top;
        else if (r.bottom > caixa.bottom) itensEl.scrollTop += r.bottom - caixa.bottom;
    }

    // ── Leitura ───────────────────────────────────────────────────────────
    function esqueletoLeitura() {
        leituraEl.innerHTML = `<div class="wm-leitura-barra"></div><div class="wm-leitura-rolagem"><div class="wm-msg">
            <div class="wm-esqueleto h-6 w-3/4"></div>
            <div class="flex gap-3 mt-6"><span class="wm-esqueleto" style="width:2.75rem;height:2.75rem;border-radius:999px"></span>
            <div class="flex-1 space-y-2 py-1"><div class="wm-esqueleto h-3 w-1/3"></div><div class="wm-esqueleto h-3 w-1/4"></div></div></div>
            <div class="wm-esqueleto mt-8" style="height:260px;border-radius:1rem"></div></div></div>`;
    }

    async function abrirMensagem(uid, opcoes) {
        opcoes = opcoes || {};
        const m = msgDaLista(uid);
        if (m && m.rascunho && pastaAtual().papel === 'rascunhos' && !opcoes.ler) {
            return abrirRascunho(uid);
        }
        E.aberta = uid;
        E.foco = E.lista.findIndex(x => x.uid === uid);
        $$('.wm-item.wm-aberto', itensEl).forEach(el => el.classList.remove('wm-aberto'));
        const el = itemEl(uid);
        if (el) el.classList.add('wm-aberto');
        marcarFoco();
        app.dataset.vista = 'leitura';
        if (!opcoes.semHistorico) historico(true);
        esqueletoLeitura();
        try {
            const d = await api(URL.mensagem, {params: {pasta: E.pasta, uid: uid, imagens: opcoes.imagens ? '1' : '0'}});
            if (E.aberta !== uid) return;
            E.msg = d.mensagem;
            lembrarContato(E.msg.de);
            // Quem diz se ela estava não lida é o servidor: com link direto, a
            // lista pode chegar depois (e já velha) ou nem ter a mensagem.
            const daLista = msgDaLista(uid);
            if (daLista && !daLista.lida) { daLista.lida = true; redesenharItem(uid); }
            if (d.mensagem.era_nao_lida) {
                const p = pastaAtual();
                if (p.nao_lidas > 0) { p.nao_lidas--; desenharPastas(); atualizarTitulo(); }
            }
            desenharLeitura(d.mensagem);
        } catch (e) {
            if (E.aberta !== uid) return;
            leituraEl.innerHTML = `<div class="wm-vazio"><div class="wm-vazio-icone"><i class="fas fa-envelope-open"></i></div>
                <p class="font-semibold" style="color:var(--wm-texto)">Não deu para abrir</p><p class="text-sm mt-1">${esc(e.message)}</p></div>`;
        }
    }

    function listaPessoas(pessoas) {
        return (pessoas || []).map(p => `<span title="${esc(p.email)}">${esc(eu(p.email) ? 'mim' : nomeDe(p))}</span>`).join(', ');
    }

    function iconeAnexo(mime, nome) {
        const ext = (nome.split('.').pop() || '').toLowerCase();
        if (mime.startsWith('image/')) return ['fa-file-image', '#0EA5E9', '#E0F2FE'];
        if (mime === 'application/pdf' || ext === 'pdf') return ['fa-file-pdf', '#DC2626', '#FEE2E2'];
        if (/sheet|excel|csv/.test(mime) || ['xls', 'xlsx', 'csv'].includes(ext)) return ['fa-file-excel', '#16A34A', '#DCFCE7'];
        if (/word|document/.test(mime) || ['doc', 'docx'].includes(ext)) return ['fa-file-word', '#2563EB', '#DBEAFE'];
        if (/presentation|powerpoint/.test(mime) || ['ppt', 'pptx'].includes(ext)) return ['fa-file-powerpoint', '#EA580C', '#FFEDD5'];
        if (/zip|compressed|rar|7z/.test(mime) || ['zip', 'rar', '7z'].includes(ext)) return ['fa-file-zipper', '#7C3AED', '#EDE9FE'];
        if (mime.startsWith('audio/')) return ['fa-file-audio', '#DB2777', '#FCE7F3'];
        if (mime.startsWith('video/')) return ['fa-file-video', '#9333EA', '#F3E8FF'];
        if (mime === 'message/rfc822') return ['fa-envelope', '#F97316', '#FFEDD5'];
        return ['fa-file-lines', '#4B5563', '#F3F4F6'];
    }
    function urlAnexo(msg, a, inline) {
        return URL.anexo + '?' + new URLSearchParams({pasta: msg.pasta, uid: msg.uid, secao: a.secao, inline: inline ? '1' : '0'});
    }

    function desenharLeitura(msg) {
        const papel = pastaAtual().papel;
        const idx = E.lista.findIndex(x => x.uid === msg.uid);
        const remetente = msg.de || {};
        const temCc = (msg.cc || []).length;
        leituraEl.innerHTML = `
        <div class="wm-leitura-barra">
            <button type="button" class="wm-icone-botao md:hidden" data-ler="voltar" title="Voltar"><i class="fas fa-arrow-left"></i></button>
            <button type="button" class="wm-icone-botao" data-ler="arquivo" title="Arquivar (E)"><i class="fas fa-box-archive"></i></button>
            ${papel === 'spam' ? '<button type="button" class="wm-icone-botao" data-ler="nao_spam" title="Não é spam"><i class="fas fa-inbox"></i></button>'
                : '<button type="button" class="wm-icone-botao" data-ler="spam" title="Marcar como spam"><i class="fas fa-shield-virus"></i></button>'}
            <button type="button" class="wm-icone-botao" data-ler="lixeira" title="${papel === 'lixeira' ? 'Excluir para sempre' : 'Excluir (#)'}"><i class="far fa-trash-can"></i></button>
            <span class="w-px h-5 mx-1" style="background:var(--wm-borda-forte)"></span>
            <button type="button" class="wm-icone-botao" data-ler="nao_lida" title="Marcar como não lida (U)"><i class="far fa-envelope"></i></button>
            <button type="button" class="wm-icone-botao ${msg.sinalizada ? 'wm-ativo' : ''}" data-ler="estrela" title="${msg.sinalizada ? 'Tirar de Importante (S)' : 'Marcar como Importante (S)'}"><i class="${msg.sinalizada ? 'fas text-amber-500' : 'far'} fa-star"></i></button>
            <button type="button" class="wm-icone-botao" data-ler="mover" title="Mover para…"><i class="fas fa-folder-tree"></i></button>
            <button type="button" class="wm-icone-botao" data-ler="mais" title="Mais"><i class="fas fa-ellipsis-vertical"></i></button>
            <div class="ml-auto flex items-center gap-0.5">
                <span class="text-[11px] mr-1 hidden sm:inline" style="color:var(--wm-texto-3)">${idx >= 0 ? `${(E.pagina - 1) * 50 + idx + 1} de ${E.total}` : ''}</span>
                <button type="button" class="wm-icone-botao" data-ler="anterior" title="Mais recente (K)" ${idx <= 0 ? 'disabled' : ''}><i class="fas fa-chevron-up text-xs"></i></button>
                <button type="button" class="wm-icone-botao" data-ler="seguinte" title="Mais antiga (J)" ${idx < 0 || idx >= E.lista.length - 1 ? 'disabled' : ''}><i class="fas fa-chevron-down text-xs"></i></button>
            </div>
        </div>
        <div class="wm-leitura-rolagem"><article class="wm-msg">
            <div class="flex items-start gap-3">
                <h2 class="flex-1">${esc(msg.assunto)}</h2>
                ${msg.sinalizada ? '<span class="wm-selo-importante" id="wmSeloImportante"><i class="fas fa-star"></i>Importante</span>' : ''}
                <span class="text-[11px] font-semibold px-2 py-1 rounded-lg whitespace-nowrap" style="background:var(--wm-fundo);color:var(--wm-texto-2)"><i class="fas ${ICONE_PAPEL[papel] || 'fa-folder'} mr-1"></i>${esc(pastaAtual().nome)}</span>
            </div>
            <div class="wm-remetente">
                ${avatar(remetente)}
                <div class="flex-1 min-w-0">
                    <div class="flex items-baseline gap-2 flex-wrap">
                        <span class="font-bold text-[15px]" style="color:var(--wm-texto)">${esc(nomeDe(remetente))}</span>
                        ${remetente.nome && remetente.email ? `<span class="text-[12px]" style="color:var(--wm-texto-3)">&lt;${esc(remetente.email)}&gt;</span>` : ''}
                    </div>
                    <div class="wm-detalhes mt-0.5">para ${listaPessoas(msg.para) || '—'}${temCc ? `, cc ${listaPessoas(msg.cc)}` : ''}
                        <button type="button" data-ler="detalhes" class="ml-1" title="Detalhes"><i class="fas fa-caret-down"></i></button></div>
                    <div class="wm-detalhes hidden mt-2 p-3 rounded-xl text-[12px] leading-6" id="wmDetalhes" style="background:var(--wm-fundo)">
                        <div><strong>De:</strong> ${esc(nomeDe(remetente))} &lt;${esc(remetente.email)}&gt;</div>
                        ${(msg.responder_para || []).length ? `<div><strong>Responder para:</strong> ${esc(msg.responder_para.map(p => p.email).join(', '))}</div>` : ''}
                        <div><strong>Para:</strong> ${esc((msg.para || []).map(p => p.nome ? `${p.nome} <${p.email}>` : p.email).join(', ') || '—')}</div>
                        ${temCc ? `<div><strong>Cc:</strong> ${esc(msg.cc.map(p => p.nome ? `${p.nome} <${p.email}>` : p.email).join(', '))}</div>` : ''}
                        <div><strong>Data:</strong> ${esc(dataLonga(msg.data))}</div>
                    </div>
                </div>
                <div class="text-right flex-shrink-0">
                    <p class="text-[12px] font-medium" style="color:var(--wm-texto-2)">${esc(dataCurta(msg.data))}</p>
                    <p class="text-[11px]" style="color:var(--wm-texto-3)">${esc(relativo(msg.data))}</p>
                </div>
            </div>
            ${msg.imagens_bloqueadas ? `<div class="wm-aviso-imagens"><i class="fas fa-shield-alt" style="color:var(--wm-laranja)"></i>
                <span class="flex-1 min-w-[180px]">Imagens externas bloqueadas — elas avisam ao remetente que você abriu o e-mail.</span>
                <button type="button" data-ler="imagens">Mostrar imagens</button></div>` : ''}
            <div class="wm-quadro"><iframe title="Conteúdo do e-mail" sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox allow-modals" referrerpolicy="no-referrer"></iframe></div>
            ${(msg.anexos || []).length ? `
            <div class="mt-5 flex items-center justify-between"><p class="text-[13px] font-bold" style="color:var(--wm-texto)"><i class="fas fa-paperclip mr-1.5" style="color:var(--wm-texto-3)"></i>${msg.anexos.length} anexo${msg.anexos.length > 1 ? 's' : ''}</p></div>
            <div class="wm-anexos">${msg.anexos.map(a => {
                const [ic, fg, bg] = iconeAnexo(a.mime, a.nome);
                const ver = a.mime.startsWith('image/') || a.mime === 'application/pdf';
                return `<div class="wm-anexo"><span class="wm-anexo-icone" style="background:${bg};color:${fg}"><i class="fas ${ic}"></i></span>
                    <div class="min-w-0 flex-1"><p class="text-[12.5px] font-semibold truncate" style="color:var(--wm-texto)" title="${esc(a.nome)}">${esc(a.nome)}</p>
                    <p class="text-[11px]" style="color:var(--wm-texto-3)">${esc(tamanho(a.tamanho) || a.mime)}</p></div>
                    ${ver ? `<a class="wm-icone-botao" href="${esc(urlAnexo(msg, a, true))}" target="_blank" rel="noopener" title="Abrir"><i class="far fa-eye"></i></a>` : ''}
                    <a class="wm-icone-botao" href="${esc(urlAnexo(msg, a, false))}" title="Baixar"><i class="fas fa-download"></i></a></div>`;
            }).join('')}</div>` : ''}
            <div class="wm-responder-rapido">
                <button type="button" class="wm-botao" data-ler="responder"><i class="fas fa-reply"></i>Responder</button>
                ${(msg.para || []).length + (msg.cc || []).length > 1 ? '<button type="button" class="wm-botao" data-ler="todos"><i class="fas fa-reply-all"></i>Responder a todos</button>' : ''}
                <button type="button" class="wm-botao" data-ler="encaminhar"><i class="fas fa-share"></i>Encaminhar</button>
            </div>
        </article></div>`;
        montarIframe($('iframe', leituraEl), msg.documento);
        $('.wm-leitura-rolagem', leituraEl).scrollTop = 0;
    }

    function montarIframe(iframe, documento) {
        iframe.addEventListener('load', () => {
            const doc = iframe.contentDocument;
            if (!doc) return;
            const ajustar = () => { iframe.style.height = Math.max(120, doc.documentElement.scrollHeight + 4) + 'px'; };
            ajustar();
            if (window.ResizeObserver) new ResizeObserver(ajustar).observe(doc.body);
            $$('img', doc).forEach(img => img.addEventListener('load', ajustar));
            // mailto: dentro do e-mail abre o Escrever do portal.
            doc.addEventListener('click', ev => {
                const a = ev.target.closest && ev.target.closest('a[href^="mailto:"]');
                if (!a) return;
                ev.preventDefault();
                const alvo = decodeURIComponent(a.getAttribute('href').slice(7).split('?')[0]);
                abrirCompor({para: alvo.split(',').map(e => ({email: e.trim(), nome: ''})).filter(p => p.email)});
            });
        }, {once: true});
        iframe.srcdoc = documento;
    }

    function fecharLeitura() {
        E.aberta = null; E.msg = null;
        $$('.wm-item.wm-aberto', itensEl).forEach(el => el.classList.remove('wm-aberto'));
        app.dataset.vista = 'lista';
        mostrarVisaoGeral();
    }

    // ── Visão geral ───────────────────────────────────────────────────────
    function saudacao() {
        const h = new Date().getHours();
        return h < 12 ? 'Bom dia' : h < 18 ? 'Boa tarde' : 'Boa noite';
    }
    async function mostrarVisaoGeral(forcar) {
        E.aberta = null;
        const hoje = new Date();
        leituraEl.innerHTML = `<div class="wm-leitura-rolagem"><div class="wm-geral">
            <p class="text-[12px] font-semibold uppercase tracking-wider" style="color:var(--wm-laranja)">${DIAS[hoje.getDay()]}, ${hoje.getDate()} de ${MESES[hoje.getMonth()]}.</p>
            <h2 class="text-2xl font-bold mt-1" style="color:var(--wm-texto)">${saudacao()}, ${esc(CFG.primeiroNome)}!</h2>
            <p class="text-sm mt-1" style="color:var(--wm-texto-3)" id="wmGeralFrase">Juntando o resumo da sua caixa…</p>
            <div class="wm-cards mt-5" id="wmGeralCards">${Array.from({length: 4}, () => '<div class="wm-card"><div class="wm-esqueleto h-9 w-9"></div><div class="wm-esqueleto h-7 w-16 mt-3"></div><div class="wm-esqueleto h-3 w-24 mt-2"></div></div>').join('')}</div>
            <div id="wmGeralResto"></div></div></div>`;
        try {
            if (!E.resumo || forcar) E.resumo = await api(URL.resumo);
            if (E.aberta !== null) return;
            desenharVisaoGeral(E.resumo);
        } catch (e) {
            const frase = document.getElementById('wmGeralFrase');
            if (frase) frase.textContent = 'Não deu para montar o resumo agora: ' + e.message;
            const cards = document.getElementById('wmGeralCards');
            if (cards) cards.innerHTML = '';
        }
    }
    function desenharVisaoGeral(r) {
        const frase = document.getElementById('wmGeralFrase');
        if (!frase) return;
        frase.textContent = r.nao_lidas
            ? `Você tem ${r.nao_lidas} mensage${r.nao_lidas > 1 ? 'ns' : 'm'} não lida${r.nao_lidas > 1 ? 's' : ''}${r.hoje ? ` e recebeu ${r.hoje} hoje` : ''}.`
            : (r.hoje ? `Tudo lido! Chegaram ${r.hoje} mensage${r.hoje > 1 ? 'ns' : 'm'} hoje.` : 'Tudo em dia na caixa de entrada.');
        const card = (num, rotulo, icone, fg, bg, acao) => `<button type="button" class="wm-card" data-geral="${acao}">
            <span class="wm-card-icone" style="background:${bg};color:${fg}"><i class="fas ${icone}"></i></span>
            <p class="wm-card-num">${num}</p><p class="wm-card-rotulo">${rotulo}</p></button>`;
        document.getElementById('wmGeralCards').innerHTML =
            card(r.nao_lidas, 'Não lidas', 'fa-envelope', '#EA580C', '#FFEDD5', 'nao_lidas')
            + card(r.hoje, 'Recebidas hoje', 'fa-sun', '#0284C7', '#E0F2FE', 'hoje')
            + card(r.sinalizadas, 'Importantes', 'fa-star', '#D97706', '#FEF3C7', 'importantes')
            + card(r.total, 'Na caixa de entrada', 'fa-inbox', '#7C3AED', '#EDE9FE', 'entrada');

        const max = Math.max(1, ...r.serie.map(s => s.total));
        const hojeIso = new Date().toISOString().slice(0, 10);
        const semana = r.serie.slice(-7).reduce((a, s) => a + s.total, 0);
        const colunas = r.serie.map((s, i) => {
            const d = new Date(s.dia + 'T12:00:00');
            return `<div class="wm-coluna-g ${s.dia === hojeIso ? 'wm-hoje' : ''}" title="${s.total} em ${d.getDate()}/${d.getMonth() + 1}">
                <em>${s.total}</em><span class="wm-b" style="height:${Math.max(2, s.total / max * 100)}%;animation-delay:${i * 25}ms"></span>
                <small>${DIAS[d.getDay()].slice(0, 1).toUpperCase()}</small></div>`;
        }).join('');
        const maxRem = Math.max(1, ...r.remetentes.map(x => x.total));
        const remetentes = r.remetentes.length ? r.remetentes.map(x => `
            <button type="button" class="flex items-center gap-3 w-full text-left p-1.5 rounded-xl hover:bg-orange-50/50" data-geral-busca="${esc(x.email)}">
                ${avatar(x, '')}<div class="flex-1 min-w-0"><div class="flex justify-between gap-2 text-[13px]"><span class="font-semibold truncate" style="color:var(--wm-texto)">${esc(x.nome || x.email)}</span>
                <span class="font-bold tabular-nums" style="color:var(--wm-texto-2)">${x.total}</span></div>
                <div class="wm-barra mt-1.5"><span style="width:${x.total / maxRem * 100}%"></span></div></div></button>`).join('')
            : '<p class="text-sm py-6 text-center" style="color:var(--wm-texto-3)">Ninguém escreveu nos últimos 30 dias.</p>';
        const recentes = r.recentes.length ? r.recentes.map(m => `
            <button type="button" class="flex items-start gap-3 w-full text-left p-2 rounded-xl hover:bg-orange-50/50" data-geral-abrir="${m.uid}">
                ${avatar(m.de, '')}<div class="flex-1 min-w-0"><div class="flex justify-between gap-2"><span class="text-[13px] font-bold truncate" style="color:var(--wm-texto)">${esc(nomeDe(m.de))}</span>
                <span class="text-[11px] whitespace-nowrap" style="color:var(--wm-laranja-escuro)">${esc(dataCurta(m.data))}</span></div>
                <p class="text-[12.5px] truncate" style="color:var(--wm-texto-2)">${esc(m.assunto)}</p></div></button>`).join('')
            : `<div class="text-center py-6"><i class="fas fa-circle-check text-3xl text-emerald-400"></i><p class="text-sm mt-2" style="color:var(--wm-texto-3)">Nenhuma mensagem esperando por você.</p></div>`;
        const pastas = E.pastas.filter(p => p.total).map(p => `<button type="button" class="wm-chip" data-geral-pasta="${esc(p.id)}">
            <i class="fas ${ICONE_PAPEL[p.papel] || 'fa-folder'} mr-1 opacity-70"></i>${esc(p.nome)} <span class="opacity-60 ml-1">${p.total}</span></button>`).join('');
        const importantes = (r.importantes || []).length ? (r.importantes || []).map(m => `
            <button type="button" class="wm-importante ${m.lida ? '' : 'wm-nao-lida'}" data-geral-abrir="${m.uid}">
                ${avatar(m.de, '')}
                <div class="flex-1 min-w-0">
                    <div class="flex items-baseline justify-between gap-2">
                        <span class="text-[13px] font-semibold truncate" style="color:var(--wm-texto)">${m.lida ? '' : '<span class="wm-ponto inline-block mr-1.5 align-middle"></span>'}${esc(nomeDe(m.de))}</span>
                        <span class="text-[11px] whitespace-nowrap" style="color:var(--wm-texto-3)">${esc(dataCurta(m.data))}</span>
                    </div>
                    <p class="text-[13px] font-semibold truncate mt-0.5" style="color:var(--wm-texto-2)">${esc(m.assunto)}${m.anexo ? ' <i class="fas fa-paperclip text-[10px] ml-1" style="color:var(--wm-texto-3)"></i>' : ''}</p>
                    ${m.previa ? `<p class="wm-importante-previa">${esc(m.previa)}</p>` : ''}
                </div>
                <i class="fas fa-star text-amber-400 text-sm mt-1"></i>
            </button>`).join('')
            : `<div class="text-center py-7 px-4"><span class="inline-flex w-11 h-11 rounded-2xl items-center justify-center text-lg" style="background:#FEF3C7;color:#D97706"><i class="far fa-star"></i></span>
                <p class="text-sm font-semibold mt-2" style="color:var(--wm-texto)">Nenhum e-mail marcado como Importante</p>
                <p class="text-[12px] mt-1" style="color:var(--wm-texto-3)">Toque na <i class="far fa-star"></i> de uma mensagem (ou aperte <span class="wm-kbd">S</span>) para ela aparecer aqui.</p></div>`;
        document.getElementById('wmGeralResto').innerHTML = `
            <section class="wm-painel mt-3 wm-painel-importantes">
                <div class="flex items-center justify-between gap-2">
                    <h3><i class="fas fa-star" style="color:#F59E0B"></i>Importantes
                        <span class="font-normal text-[11px]" style="color:var(--wm-texto-3)">${r.sinalizadas ? `${r.sinalizadas} na caixa de entrada${r.importantes.filter(m => !m.lida).length ? ` · ${r.importantes.filter(m => !m.lida).length} sem ler` : ''}` : ''}</span></h3>
                    ${r.sinalizadas > (r.importantes || []).length ? `<button type="button" class="text-[12px] font-semibold" style="color:var(--wm-laranja-escuro)" data-geral="importantes">ver todos (${r.sinalizadas})</button>` : ''}
                </div>
                <div class="wm-importantes mt-2">${importantes}</div>
            </section>
            <div class="wm-geral-linha">
                <section class="wm-painel wm-largo"><div class="flex items-center justify-between"><h3><i class="fas fa-chart-column" style="color:var(--wm-laranja)"></i>Chegadas nos últimos 14 dias</h3>
                    <span class="text-[12px]" style="color:var(--wm-texto-3)">${semana} nos últimos 7 dias</span></div>
                    <div class="wm-grafico">${colunas}</div></section>
                <section class="wm-painel wm-estreito"><h3><i class="fas fa-user-group" style="color:var(--wm-laranja)"></i>Quem mais te escreveu <span class="font-normal text-[11px]" style="color:var(--wm-texto-3)">30 dias</span></h3>
                    <div class="mt-2 space-y-0.5">${remetentes}</div></section>
            </div>
            <div class="wm-geral-linha">
                <section class="wm-painel wm-largo"><div class="flex items-center justify-between"><h3><i class="fas fa-bell" style="color:var(--wm-laranja)"></i>Esperando sua leitura</h3>
                    ${r.nao_lidas > r.recentes.length ? `<button type="button" class="text-[12px] font-semibold" style="color:var(--wm-laranja-escuro)" data-geral="nao_lidas">ver todas (${r.nao_lidas})</button>` : ''}</div>
                    <div class="mt-2 space-y-0.5">${recentes}</div></section>
                <section class="wm-painel wm-estreito"><h3><i class="fas fa-folder-tree" style="color:var(--wm-laranja)"></i>Pastas</h3>
                    <div class="flex flex-wrap gap-1.5 mt-3">${pastas || '<span class="text-sm" style="color:var(--wm-texto-3)">—</span>'}</div>
                    ${r.quota ? `<div class="mt-4"><div class="flex justify-between text-[12px] mb-1.5" style="color:var(--wm-texto-2)"><span>Espaço usado</span><span>${tamanho(r.quota.usado)} de ${tamanho(r.quota.limite)}</span></div>
                        <div class="wm-barra"><span style="width:${Math.min(100, r.quota.pct)}%"></span></div></div>` : ''}
                    <div class="mt-4 pt-3 border-t text-[12px] leading-6" style="border-color:var(--wm-borda);color:var(--wm-texto-3)">
                        <span class="wm-kbd">C</span> escrever · <span class="wm-kbd">/</span> buscar · <span class="wm-kbd">J</span><span class="wm-kbd">K</span> navegar · <span class="wm-kbd">?</span> todos os atalhos</div></section>
            </div>`;
        if (r.quota) {
            document.getElementById('wmQuota').hidden = false;
            document.getElementById('wmQuotaTexto').textContent = `${Math.round(r.quota.pct)}%`;
            document.getElementById('wmQuotaBarra').style.width = Math.min(100, r.quota.pct) + '%';
        }
    }

    // ── Ações ─────────────────────────────────────────────────────────────
    const SAI_DA_LISTA = new Set(['arquivo', 'lixeira', 'spam', 'nao_spam', 'mover', 'apagar']);
    const TEXTO_ACAO = {arquivo: 'Arquivada', lixeira: 'Movida para a lixeira', spam: 'Marcada como spam', nao_spam: 'Devolvida para a caixa de entrada',
        mover: 'Movida', apagar: 'Excluída para sempre', lida: 'Marcada como lida', nao_lida: 'Marcada como não lida',
        sinalizar: 'Marcada como Importante', tirar_sinal: 'Tirada de Importante'};

    async function executarAcao(acao, uids, destino) {
        uids = Array.from(uids);
        if (!uids.length) return;
        let qual = acao;
        if (acao === 'lixeira' && pastaAtual().papel === 'lixeira') {
            if (!confirm(uids.length > 1 ? `Excluir ${uids.length} mensagens para sempre?` : 'Excluir esta mensagem para sempre?')) return;
            qual = 'apagar';
        }
        const aberta = E.aberta;
        const proxima = (() => {
            if (!aberta || !uids.includes(aberta)) return null;
            const i = E.lista.findIndex(m => m.uid === aberta);
            const resto = E.lista.filter(m => !uids.includes(m.uid));
            return resto[Math.min(i, resto.length - 1)] || null;
        })();
        // Otimista: a tela muda na hora; se o servidor recusar, a lista volta.
        if (SAI_DA_LISTA.has(qual)) {
            E.lista = E.lista.filter(m => !uids.includes(m.uid));
            E.total = Math.max(0, E.total - uids.length);
            uids.forEach(u => E.marcadas.delete(u));
            desenharLista();
            if (aberta && uids.includes(aberta)) {
                if (proxima && window.innerWidth >= 768) abrirMensagem(proxima.uid);
                else fecharLeitura();
            }
        } else {
            uids.forEach(u => {
                const m = msgDaLista(u);
                if (!m) return;
                if (qual === 'lida') m.lida = true;
                if (qual === 'nao_lida') m.lida = false;
                if (qual === 'sinalizar') m.sinalizada = true;
                if (qual === 'tirar_sinal') m.sinalizada = false;
                redesenharItem(u);
            });
            atualizarLote();
        }
        try {
            await api(URL.acao, {method: 'POST', json: {pasta: E.pasta, uids: uids, acao: qual, destino: destino || ''}});
            if (TEXTO_ACAO[qual]) {
                const nome = destino ? (E.pastas.find(p => p.id === destino) || {}).nome : '';
                aviso((uids.length > 1 ? `${uids.length} mensagens: ` : '') + TEXTO_ACAO[qual].toLowerCase().replace(/^./, c => c.toUpperCase()) + (nome ? ` para ${nome}` : ''));
            }
            E.resumo = null;
            recarregarPastas();
            if (SAI_DA_LISTA.has(qual) && E.lista.length < 10 && E.total > E.lista.length) carregarLista({silencioso: true});
        } catch (e) {
            aviso(e.message, {erro: true});
            carregarLista({silencioso: true});
        }
    }

    // ── Menus ─────────────────────────────────────────────────────────────
    let menuAberto = null;
    function fecharMenu() { if (menuAberto) { menuAberto.remove(); menuAberto = null; } }
    function abrirMenu(ancora, opcoes) {
        fecharMenu();
        const menu = cria('<div class="wm-menu" role="menu"></div>');
        opcoes.forEach(o => {
            if (o === '-') { menu.appendChild(cria('<div class="my-1 border-t" style="border-color:var(--wm-borda)"></div>')); return; }
            const b = cria(`<button type="button" role="menuitem"><i class="${o.icone}"></i><span></span></button>`);
            b.querySelector('span').textContent = o.rotulo;
            b.addEventListener('click', () => { fecharMenu(); o.fn(); });
            menu.appendChild(b);
        });
        document.body.appendChild(menu);
        const r = ancora.getBoundingClientRect();
        const largura = menu.offsetWidth, altura = menu.offsetHeight;
        menu.style.left = Math.max(8, Math.min(r.left, window.innerWidth - largura - 8)) + 'px';
        menu.style.top = (r.bottom + altura + 8 > window.innerHeight ? Math.max(8, r.top - altura - 6) : r.bottom + 6) + 'px';
        menuAberto = menu;
    }
    function opcoesMover(uids) {
        return E.pastas.filter(p => p.id !== E.pasta).map(p => ({
            rotulo: p.caminho && !p.papel ? p.caminho : p.nome, icone: 'fas ' + (ICONE_PAPEL[p.papel] || 'fa-folder'),
            fn: () => executarAcao('mover', uids, p.id),
        }));
    }
    document.addEventListener('click', ev => {
        if (menuAberto && !menuAberto.contains(ev.target) && !ev.target.closest('[data-ler="mais"],[data-ler="mover"],[data-lote="mais"]')) fecharMenu();
    });

    // ── Modais ────────────────────────────────────────────────────────────
    function modal(html) {
        const fundo = cria(`<div class="wm-fundo-modal"><div class="wm-modal">${html}</div></div>`);
        fundo.addEventListener('click', ev => { if (ev.target === fundo || ev.target.closest('[data-fechar]')) fundo.remove(); });
        document.body.appendChild(fundo);
        return fundo;
    }
    function mostrarAtalhos() {
        const linha = (teclas, texto) => `<div class="flex items-center justify-between py-1.5 text-[13px]"><span style="color:var(--wm-texto-2)">${texto}</span><span class="flex gap-1">${teclas.map(t => `<span class="wm-kbd">${t}</span>`).join('')}</span></div>`;
        modal(`<div class="p-6"><div class="flex items-center justify-between"><h3 class="text-lg font-bold"><i class="far fa-keyboard mr-2" style="color:var(--wm-laranja)"></i>Atalhos do teclado</h3>
            <button type="button" class="wm-icone-botao" data-fechar><i class="fas fa-xmark"></i></button></div>
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-x-8 mt-4">
            <div>${linha(['C'], 'Escrever') + linha(['/'], 'Buscar') + linha(['J'], 'Mensagem mais antiga') + linha(['K'], 'Mensagem mais recente') + linha(['Enter'], 'Abrir') + linha(['Esc'], 'Fechar / voltar') + linha(['X'], 'Selecionar')}</div>
            <div>${linha(['R'], 'Responder') + linha(['A'], 'Responder a todos') + linha(['F'], 'Encaminhar') + linha(['E'], 'Arquivar') + linha(['#'], 'Excluir') + linha(['S'], 'Marcar como Importante') + linha(['U'], 'Marcar como não lida') + linha(['Ctrl', 'Enter'], 'Enviar (ao escrever)')}</div>
            </div></div>`);
    }

    function mostrarConfiguracoes() {
        const fundo = modal(`<form class="p-6" id="wmFormPrefs">
            <div class="flex items-center justify-between"><h3 class="text-lg font-bold"><i class="fas fa-gear mr-2" style="color:var(--wm-laranja)"></i>Configurações do e-mail</h3>
            <button type="button" class="wm-icone-botao" data-fechar><i class="fas fa-xmark"></i></button></div>
            <div class="mt-5"><label class="wm-rotulo">Conta</label>
                <div class="flex items-center gap-3 p-3 rounded-xl" style="background:var(--wm-fundo)"><span class="wm-avatar wm-avatar-eu">${esc(iniciais(CFG.nome, CFG.email))}</span>
                <div class="min-w-0"><p class="text-sm font-semibold truncate">${esc(CFG.email)}</p><p class="text-[12px]" style="color:var(--wm-texto-3)">Ligado ao seu usuário do portal · senha cifrada</p></div></div></div>
            <div class="mt-4"><label class="wm-rotulo" for="wmPrefNome">Nome que aparece para quem recebe</label>
                <input class="wm-campo" id="wmPrefNome" maxlength="150" value="${esc(CFG.nome === CFG.email ? '' : CFG.nome)}" placeholder="Seu nome"></div>
            <div class="mt-4"><div class="flex items-center justify-between"><label class="wm-rotulo">Assinatura</label>
                <button type="button" class="text-[12px] font-semibold" style="color:var(--wm-laranja-escuro)" id="wmPrefModelo">usar o modelo do portal</button></div>
                <div class="border rounded-xl overflow-hidden" style="border-color:var(--wm-borda-forte)">
                    <div class="wm-ferramentas" data-alvo="wmPrefAssinatura"><button type="button" data-cmd="bold" title="Negrito"><i class="fas fa-bold"></i></button>
                    <button type="button" data-cmd="italic" title="Itálico"><i class="fas fa-italic"></i></button><button type="button" data-cmd="createLink" title="Link"><i class="fas fa-link"></i></button></div>
                    <div class="wm-editor" id="wmPrefAssinatura" contenteditable="true" style="min-height:110px;max-height:220px" data-placeholder="Sua assinatura"></div></div>
                <p class="text-[11px] mt-1.5" style="color:var(--wm-texto-3)">Entra sozinha no fim de cada mensagem nova, resposta e encaminhamento.</p></div>
            <div class="flex items-center justify-between gap-3 mt-6 pt-5 border-t flex-wrap" style="border-color:var(--wm-borda)">
                <button type="button" id="wmDesconectar" class="text-[13px] font-semibold text-red-600 hover:underline"><i class="fas fa-power-off mr-1"></i>Desconectar e apagar a senha</button>
                <div class="flex gap-2"><button type="button" class="wm-botao" data-fechar>Cancelar</button>
                <button type="submit" class="wm-botao-primario"><i class="fas fa-check"></i>Salvar</button></div></div></form>`);
        const editor = $('#wmPrefAssinatura', fundo);
        editor.innerHTML = ASSINATURA || ASSINATURA_PADRAO;
        $('#wmPrefModelo', fundo).addEventListener('click', () => { editor.innerHTML = ASSINATURA_PADRAO; });
        $('#wmDesconectar', fundo).addEventListener('click', () => {
            if (!confirm('Desconectar o e-mail do portal? A senha guardada é apagada e você precisará entrar de novo para usar a caixa aqui. (Seus e-mails continuam no servidor.)')) return;
            const f = cria(`<form method="post" action="${esc(URL.desconectar)}"><input type="hidden" name="csrfmiddlewaretoken" value="${esc(CFG.csrf)}"></form>`);
            document.body.appendChild(f);
            f.submit();
        });
        $('#wmFormPrefs', fundo).addEventListener('submit', async ev => {
            ev.preventDefault();
            const form = new FormData();
            form.append('nome_exibicao', $('#wmPrefNome', fundo).value);
            form.append('assinatura', editor.innerHTML);
            try {
                const d = await api(URL.preferencias, {method: 'POST', form: form});
                ASSINATURA = d.assinatura;
                CFG.nome = d.nome_exibicao || CFG.email;
                fundo.remove();
                aviso('Configurações salvas');
            } catch (e) { aviso(e.message, {erro: true}); }
        });
    }

    async function novaPasta() {
        const nome = prompt('Nome da nova pasta:');
        if (!nome || !nome.trim()) return;
        const form = new FormData();
        form.append('nome', nome.trim());
        try {
            await api(URL.criarPasta, {method: 'POST', form: form});
            aviso(`Pasta “${nome.trim()}” criada`);
            carregarPastas();
        } catch (e) { aviso(e.message, {erro: true}); }
    }

    // ── Contatos (sugestões do campo Para) ────────────────────────────────
    function lembrarContato(p) {
        if (!p || !p.email || eu(p.email)) return;
        const k = p.email.toLowerCase();
        if (!E.contatos.has(k) || (!E.contatos.get(k).nome && p.nome)) E.contatos.set(k, {nome: p.nome || '', email: p.email});
    }

    function campoDestinos(caixa, iniciais) {
        const input = $('input', caixa);
        const pessoas = [];
        let sugestoes = null, foco = -1, lista = [];
        const valido = e => /^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$/.test(e);
        function desenhar() {
            $$('.wm-destino', caixa).forEach(el => el.remove());
            pessoas.forEach((p, i) => {
                const chip = cria(`<span class="wm-destino ${valido(p.email) ? '' : 'wm-invalido'}" title="${esc(p.email)}"><span class="truncate"></span><button type="button" aria-label="Remover"><i class="fas fa-xmark"></i></button></span>`);
                chip.querySelector('span').textContent = p.nome ? p.nome : p.email;
                chip.querySelector('button').addEventListener('click', () => { pessoas.splice(i, 1); desenhar(); input.focus(); });
                caixa.insertBefore(chip, input);
            });
        }
        function adicionar(texto, nome) {
            String(texto || '').split(/[,;\n]+/).map(t => t.trim()).filter(Boolean).forEach(t => {
                const m = t.match(/^(.*)<([^>]+)>$/);
                const email = (m ? m[2] : t).trim().replace(/^mailto:/i, '');
                const n = nome || (m ? m[1].trim().replace(/^"|"$/g, '') : '');
                if (!pessoas.some(p => p.email.toLowerCase() === email.toLowerCase())) pessoas.push({email: email, nome: n});
            });
            desenhar();
        }
        function fecharSugestoes() { if (sugestoes) { sugestoes.remove(); sugestoes = null; } foco = -1; }
        function mostrar(itens) {
            fecharSugestoes();
            lista = itens;
            if (!itens.length) return;
            sugestoes = cria('<div class="wm-sugestoes"></div>');
            itens.forEach((c, i) => {
                const b = cria(`<button type="button" class="wm-sugestao">${avatar(c)}<div class="min-w-0"><p class="text-[13px] font-semibold truncate"></p><p class="text-[11.5px] truncate" style="color:var(--wm-texto-3)"></p></div></button>`);
                b.querySelector('p').textContent = c.nome || c.email;
                b.querySelectorAll('p')[1].textContent = c.email + (c.setor ? ' · ' + c.setor : '');
                b.addEventListener('mousedown', ev => { ev.preventDefault(); escolher(i); });
                sugestoes.appendChild(b);
            });
            caixa.appendChild(sugestoes);
        }
        function escolher(i) {
            const c = lista[i];
            if (!c) return;
            adicionar(c.email, c.nome);
            input.value = '';
            fecharSugestoes();
        }
        const buscar = debounce(async termo => {
            const t = termo.toLowerCase();
            const locais = Array.from(E.contatos.values()).filter(c => c.email.toLowerCase().includes(t) || (c.nome || '').toLowerCase().includes(t)).slice(0, 5);
            let remotos = [];
            if (t.length >= 2) {
                try { remotos = (await api(URL.contatos, {params: {q: termo}})).contatos; } catch (e) { remotos = []; }
            }
            if (input.value.trim() !== termo) return;
            const vistos = new Set();
            mostrar([...remotos, ...locais].filter(c => { const k = c.email.toLowerCase(); if (vistos.has(k) || pessoas.some(p => p.email.toLowerCase() === k)) return false; vistos.add(k); return true; }).slice(0, 7));
        }, 180);
        input.addEventListener('input', () => {
            const v = input.value;
            if (/[,;]\s*$/.test(v)) { adicionar(v); input.value = ''; fecharSugestoes(); return; }
            if (v.trim()) buscar(v.trim()); else fecharSugestoes();
        });
        input.addEventListener('keydown', ev => {
            const botoes = sugestoes ? $$('.wm-sugestao', sugestoes) : [];
            if (ev.key === 'ArrowDown' && botoes.length) { ev.preventDefault(); foco = (foco + 1) % botoes.length; botoes.forEach((b, i) => b.classList.toggle('wm-foco', i === foco)); return; }
            if (ev.key === 'ArrowUp' && botoes.length) { ev.preventDefault(); foco = (foco - 1 + botoes.length) % botoes.length; botoes.forEach((b, i) => b.classList.toggle('wm-foco', i === foco)); return; }
            if ((ev.key === 'Enter' || ev.key === 'Tab') && foco >= 0 && sugestoes) { ev.preventDefault(); escolher(foco); return; }
            if ((ev.key === 'Enter' || ev.key === ',') && input.value.trim()) { ev.preventDefault(); adicionar(input.value); input.value = ''; fecharSugestoes(); return; }
            if (ev.key === 'Backspace' && !input.value && pessoas.length) { pessoas.pop(); desenhar(); }
            if (ev.key === 'Escape' && sugestoes) { ev.stopPropagation(); fecharSugestoes(); }
        });
        input.addEventListener('blur', () => { setTimeout(() => { if (input.value.trim()) { adicionar(input.value); input.value = ''; } fecharSugestoes(); }, 120); });
        input.addEventListener('paste', ev => {
            const texto = (ev.clipboardData || window.clipboardData).getData('text');
            if (/[,;\n]/.test(texto)) { ev.preventDefault(); adicionar(texto); }
        });
        caixa.addEventListener('click', ev => { if (ev.target === caixa) input.focus(); });
        (iniciais || []).forEach(p => adicionar(p.email, p.nome));
        return {
            valores: () => { if (input.value.trim()) { adicionar(input.value); input.value = ''; } return pessoas.slice(); },
            invalidos: () => pessoas.filter(p => !valido(p.email)),
            texto: () => pessoas.map(p => p.nome ? `"${p.nome.replace(/"/g, '')}" <${p.email}>` : p.email).join(', '),
            focar: () => input.focus(),
        };
    }

    // ── Escrever ──────────────────────────────────────────────────────────
    let compor = null;

    function blocoAssinatura() {
        const a = ASSINATURA || ASSINATURA_PADRAO;
        return a ? `<div class="wm-assinatura"><p>--</p>${a}</div>` : '';
    }
    function prefixo(assunto, p) {
        const re = new RegExp('^(' + p + '|re|res|enc|fwd|fw)\\s*:\\s*', 'i');
        return new RegExp('^' + p + '\\s*:', 'i').test(assunto || '') ? assunto : `${p}: ${(assunto || '').replace(re, '')}`;
    }

    function abrirCompor(opcoes) {
        opcoes = opcoes || {};
        if (compor) {
            if (compor.sujo && !confirm('Já tem uma mensagem sendo escrita. Descartar e começar outra?')) { compor.el.classList.remove('wm-minimizado'); return; }
            compor.fechar(true);
        }
        const el = cria(`<div class="wm-compor" role="dialog" aria-label="Escrever">
            <div class="wm-compor-cabeca" data-c="alternar"><p class="flex-1 text-[13.5px] font-semibold truncate" data-c="titulo">${esc(opcoes.titulo || 'Nova mensagem')}</p>
                <button type="button" data-c="minimizar" title="Minimizar"><i class="fas fa-minus"></i></button>
                <button type="button" data-c="cheio" title="Tela cheia" class="hidden md:inline-block"><i class="fas fa-up-right-and-down-left-from-center text-xs"></i></button>
                <button type="button" data-c="fechar" title="Fechar (salva o rascunho)"><i class="fas fa-xmark"></i></button></div>
            <div class="wm-compor-corpo relative">
                <div class="wm-linha-campo"><label>Para</label><div class="wm-destinos" data-c="para"><input type="text" autocomplete="off" aria-label="Para"></div>
                    <button type="button" class="text-[12px] font-semibold pt-1.5" style="color:var(--wm-texto-3)" data-c="mostrar-cc">Cc Cco</button></div>
                <div class="wm-linha-campo hidden" data-c="linha-cc"><label>Cc</label><div class="wm-destinos" data-c="cc"><input type="text" autocomplete="off" aria-label="Cc"></div></div>
                <div class="wm-linha-campo hidden" data-c="linha-cco"><label>Cco</label><div class="wm-destinos" data-c="cco"><input type="text" autocomplete="off" aria-label="Cco"></div></div>
                <div class="wm-linha-campo"><label>Assunto</label><input class="wm-simples" data-c="assunto" maxlength="250" aria-label="Assunto"></div>
                <div class="wm-ferramentas">
                    <button type="button" data-cmd="bold" title="Negrito"><i class="fas fa-bold"></i></button>
                    <button type="button" data-cmd="italic" title="Itálico"><i class="fas fa-italic"></i></button>
                    <button type="button" data-cmd="underline" title="Sublinhado"><i class="fas fa-underline"></i></button>
                    <span class="wm-sep"></span>
                    <button type="button" data-cmd="insertUnorderedList" title="Lista"><i class="fas fa-list-ul"></i></button>
                    <button type="button" data-cmd="insertOrderedList" title="Lista numerada"><i class="fas fa-list-ol"></i></button>
                    <button type="button" data-cmd="formatBlock" data-valor="blockquote" title="Citação"><i class="fas fa-quote-right"></i></button>
                    <span class="wm-sep"></span>
                    <button type="button" data-cmd="createLink" title="Link"><i class="fas fa-link"></i></button>
                    <button type="button" data-cmd="removeFormat" title="Limpar formatação"><i class="fas fa-text-slash"></i></button>
                </div>
                <div class="wm-editor" data-c="editor" contenteditable="true" data-placeholder="Escreva sua mensagem…"></div>
                <div class="wm-lista-anexos" data-c="anexos"></div>
                <div class="wm-compor-pe">
                    <button type="button" class="wm-botao-primario" data-c="enviar"><i class="fas fa-paper-plane"></i><span>Enviar</span></button>
                    <label class="wm-icone-botao cursor-pointer" title="Anexar arquivos"><i class="fas fa-paperclip"></i><input type="file" multiple hidden data-c="arquivo"></label>
                    <span class="text-[11px] ml-1 hidden sm:inline" style="color:var(--wm-texto-3)" data-c="estado"></span>
                    <button type="button" class="wm-icone-botao ml-auto" data-c="descartar" title="Descartar"><i class="far fa-trash-can"></i></button>
                </div>
                <div class="wm-soltar"><i class="fas fa-cloud-arrow-up text-3xl"></i>Solte para anexar</div>
            </div></div>`);
        document.body.appendChild(el);

        const c = {
            el: el, sujo: false, enviando: false, arquivos: [], encaminhados: (opcoes.anexosOriginais || []).slice(),
            rascunhoUid: opcoes.rascunhoUid || null, extras: opcoes.extras || {}, timer: null,
        };
        const q = s => $(`[data-c="${s}"]`, el);
        c.para = campoDestinos(q('para'), opcoes.para);
        c.cc = campoDestinos(q('cc'), opcoes.cc);
        c.cco = campoDestinos(q('cco'), opcoes.cco);
        if ((opcoes.cc || []).length || (opcoes.cco || []).length) { q('linha-cc').classList.remove('hidden'); q('linha-cco').classList.remove('hidden'); }
        q('assunto').value = opcoes.assunto || '';
        const editor = q('editor');
        editor.innerHTML = opcoes.corpo != null ? opcoes.corpo : '<p><br></p>' + blocoAssinatura();

        function desenharAnexos() {
            const caixaAnexos = q('anexos');
            caixaAnexos.innerHTML = '';
            const chip = (nome, tam, remover) => {
                const [ic, fg] = iconeAnexo('', nome);
                const elc = cria(`<span class="wm-anexo-chip"><i class="fas ${ic}" style="color:${fg}"></i><span class="wm-n"></span><span style="color:var(--wm-texto-3)">${esc(tamanho(tam))}</span><button type="button" class="wm-icone-botao" style="width:1.5rem;height:1.5rem"><i class="fas fa-xmark text-[10px]"></i></button></span>`);
                elc.querySelector('.wm-n').textContent = nome;
                elc.querySelector('button').addEventListener('click', remover);
                caixaAnexos.appendChild(elc);
            };
            c.encaminhados.forEach((a, i) => chip(a.nome, a.tamanho, () => { c.encaminhados.splice(i, 1); desenharAnexos(); sujar(); }));
            c.arquivos.forEach((f, i) => chip(f.name, f.size, () => { c.arquivos.splice(i, 1); desenharAnexos(); sujar(); }));
        }
        function anexar(arquivos) {
            const total = c.arquivos.reduce((a, f) => a + f.size, 0) + Array.from(arquivos).reduce((a, f) => a + f.size, 0);
            if (total > 25 * 1024 * 1024) { aviso('Os anexos passam de 25 MB. Para arquivos grandes, mande um link do Drive.', {erro: true}); return; }
            c.arquivos.push(...arquivos);
            desenharAnexos();
            sujar();
        }
        desenharAnexos();

        function estado(texto) { q('estado').textContent = texto || ''; }
        function sujar() {
            c.sujo = true;
            clearTimeout(c.timer);
            c.timer = setTimeout(() => salvarRascunho(false), 30000);
        }
        function temConteudo() {
            const texto = editor.innerText.replace(/\s+/g, '');
            const ass = (blocoAssinatura().replace(/<[^>]+>/g, '').replace(/\s+/g, ''));
            return c.para.valores().length || q('assunto').value.trim() || c.arquivos.length || (texto && texto !== ass && texto !== '--' + ass);
        }
        function montarForm() {
            const form = new FormData();
            form.append('para', c.para.texto());
            form.append('cc', c.cc.texto());
            form.append('cco', c.cco.texto());
            form.append('assunto', q('assunto').value);
            form.append('corpo', editor.innerHTML);
            c.arquivos.forEach(f => form.append('anexos', f, f.name));
            if (c.extras.encaminhar_uid) {
                form.append('encaminhar_pasta', c.extras.encaminhar_pasta);
                form.append('encaminhar_uid', c.extras.encaminhar_uid);
                if (c.encaminhados.length) c.encaminhados.forEach(a => form.append('encaminhar_anexo', a.nome));
                else form.append('encaminhar_anexo', '__nenhum__');
            }
            ['responde_a', 'referencias', 'respondida_pasta', 'respondida_uid'].forEach(k => { if (c.extras[k]) form.append(k, c.extras[k]); });
            if (c.rascunhoUid) form.append('rascunho_uid', c.rascunhoUid);
            return form;
        }
        async function salvarRascunho(avisar) {
            clearTimeout(c.timer);
            if (!c.sujo || c.enviando || !temConteudo()) return true;
            estado('Salvando rascunho…');
            try {
                const d = await api(URL.rascunho, {method: 'POST', form: montarForm()});
                c.rascunhoUid = d.uid || null;
                c.sujo = false;
                estado('Rascunho salvo ' + new Date().toLocaleTimeString('pt-BR', {hour: '2-digit', minute: '2-digit'}));
                if (avisar) aviso('Rascunho salvo', {icone: 'fa-file-signature'});
                if (pastaAtual().papel === 'rascunhos') carregarLista({silencioso: true});
                recarregarPastas();
                return true;
            } catch (e) {
                estado('');
                aviso('Não salvei o rascunho: ' + e.message, {erro: true});
                return false;
            }
        }
        async function enviar() {
            if (c.enviando) return;
            const destinos = [...c.para.valores(), ...c.cc.valores(), ...c.cco.valores()];
            if (!destinos.length) { aviso('Para quem vai? Coloque pelo menos um destinatário.', {erro: true}); c.para.focar(); return; }
            const ruins = [...c.para.invalidos(), ...c.cc.invalidos(), ...c.cco.invalidos()];
            if (ruins.length) { aviso('Confira o endereço: ' + ruins.map(p => p.email).join(', '), {erro: true}); return; }
            if (!q('assunto').value.trim() && !confirm('Enviar sem assunto?')) return;
            c.enviando = true;
            clearTimeout(c.timer);
            const botao = q('enviar');
            botao.disabled = true;
            botao.innerHTML = '<i class="fas fa-spinner wm-girando"></i><span>Enviando…</span>';
            try {
                const d = await api(URL.enviar, {method: 'POST', form: montarForm()});
                c.fechar(true);
                aviso((d.recusados || []).length ? 'Enviada, mas recusada para: ' + d.recusados.join(', ') : 'Mensagem enviada', {icone: 'fa-paper-plane', erro: (d.recusados || []).length > 0});
                E.resumo = null;
                const papel = pastaAtual().papel;
                if (papel === 'enviados' || papel === 'rascunhos') carregarLista({silencioso: true});
                if (c.extras.respondida_uid) { const m = msgDaLista(Number(c.extras.respondida_uid)); if (m) { m.respondida = true; redesenharItem(m.uid); } }
                recarregarPastas();
            } catch (e) {
                c.enviando = false;
                botao.disabled = false;
                botao.innerHTML = '<i class="fas fa-paper-plane"></i><span>Enviar</span>';
                aviso(e.message, {erro: true});
            }
        }
        c.fechar = function (semSalvar) {
            clearTimeout(c.timer);
            if (!semSalvar && c.sujo && temConteudo()) {
                salvarRascunho(true).then(ok => { if (ok) { el.remove(); compor = null; } });
                return;
            }
            el.remove();
            compor = null;
        };

        // Eventos do quadro
        el.addEventListener('click', async ev => {
            const alvo = ev.target.closest('[data-c],[data-cmd]');
            if (!alvo) return;
            if (alvo.dataset.cmd) {
                ev.preventDefault();
                let valor = alvo.dataset.valor || null;
                if (alvo.dataset.cmd === 'createLink') {
                    valor = prompt('Endereço do link:', 'https://');
                    if (!valor) return;
                }
                editor.focus();
                document.execCommand(alvo.dataset.cmd, false, valor);
                sujar();
                return;
            }
            const qual = alvo.dataset.c;
            if (qual === 'alternar' && ev.target === alvo || qual === 'titulo') el.classList.toggle('wm-minimizado');
            else if (qual === 'minimizar') { ev.stopPropagation(); el.classList.toggle('wm-minimizado'); }
            else if (qual === 'cheio') { ev.stopPropagation(); el.classList.toggle('wm-cheio'); el.classList.remove('wm-minimizado'); }
            else if (qual === 'fechar') { ev.stopPropagation(); c.fechar(false); }
            else if (qual === 'mostrar-cc') { q('linha-cc').classList.toggle('hidden'); q('linha-cco').classList.toggle('hidden'); alvo.classList.add('hidden'); c.cc.focar(); }
            else if (qual === 'enviar') enviar();
            else if (qual === 'descartar') {
                if (temConteudo() && !confirm('Descartar esta mensagem?')) return;
                const uid = c.rascunhoUid;
                c.fechar(true);
                const rasc = pastaPorPapel('rascunhos');
                if (uid && rasc) {
                    try {
                        await api(URL.acao, {method: 'POST', json: {pasta: rasc.id, uids: [uid], acao: 'apagar'}});
                        if (E.pasta === rasc.id) carregarLista({silencioso: true});
                        recarregarPastas();
                    } catch (e) { /* o rascunho fica; nada se perde */ }
                }
                aviso('Mensagem descartada', {icone: 'fa-trash-can'});
            }
        });
        q('arquivo').addEventListener('change', ev => { anexar(ev.target.files); ev.target.value = ''; });
        editor.addEventListener('input', sujar);
        q('assunto').addEventListener('input', () => { sujar(); q('titulo').textContent = q('assunto').value.trim() || opcoes.titulo || 'Nova mensagem'; });
        $$('.wm-destinos input', el).forEach(i => i.addEventListener('change', sujar));
        el.addEventListener('keydown', ev => {
            if ((ev.ctrlKey || ev.metaKey) && ev.key === 'Enter') { ev.preventDefault(); enviar(); }
            if (ev.key === 'Escape') { ev.stopPropagation(); el.classList.add('wm-minimizado'); }
        });
        let arrastes = 0;
        el.addEventListener('dragenter', ev => { if (ev.dataTransfer && Array.from(ev.dataTransfer.types).includes('Files')) { arrastes++; el.classList.add('wm-arrastando'); } });
        el.addEventListener('dragleave', () => { arrastes = Math.max(0, arrastes - 1); if (!arrastes) el.classList.remove('wm-arrastando'); });
        el.addEventListener('dragover', ev => { if (Array.from(ev.dataTransfer.types).includes('Files')) ev.preventDefault(); });
        el.addEventListener('drop', ev => {
            if (!ev.dataTransfer.files.length) return;
            ev.preventDefault(); arrastes = 0; el.classList.remove('wm-arrastando');
            anexar(ev.dataTransfer.files);
        });
        // Imagem colada vira anexo (o editor não guarda imagem embutida).
        editor.addEventListener('paste', ev => {
            const arquivos = Array.from((ev.clipboardData || {}).files || []);
            if (arquivos.length) { ev.preventDefault(); anexar(arquivos); }
        });

        compor = c;
        setTimeout(() => {
            if (!(opcoes.para || []).length) c.para.focar();
            else {
                editor.focus();
                const r = document.createRange();
                r.setStart(editor, 0); r.collapse(true);
                const s = window.getSelection(); s.removeAllRanges(); s.addRange(r);
            }
        }, 30);
        return c;
    }

    function citacao(msg) {
        return `<div class="wm-citacao-original"><p>Em ${esc(dataLonga(msg.data))}, ${esc(nomeDe(msg.de))} &lt;${esc(msg.de.email)}&gt; escreveu:</p><blockquote>${msg.citacao}</blockquote></div>`;
    }
    function responder(msg, todos) {
        if (!msg) return;
        const para = (msg.responder_para || []).length ? msg.responder_para : [msg.de];
        let cc = [];
        if (todos) {
            const ja = new Set(para.map(p => p.email.toLowerCase()));
            const extra = [...(msg.para || []), ...(msg.cc || [])].filter(p => !eu(p.email) && !ja.has(p.email.toLowerCase()));
            cc = extra;
        }
        abrirCompor({
            titulo: todos ? 'Responder a todos' : 'Responder',
            para: para.filter(p => !eu(p.email) || para.length === 1), cc: cc,
            assunto: prefixo(msg.assunto, 'Re'),
            corpo: '<p><br></p>' + blocoAssinatura() + citacao(msg),
            extras: {responde_a: msg.message_id, referencias: msg.references, respondida_pasta: msg.pasta, respondida_uid: msg.uid},
        });
    }
    function encaminhar(msg) {
        if (!msg) return;
        const cab = `<div class="wm-citacao-original"><p>---------- Mensagem encaminhada ----------<br>De: ${esc(nomeDe(msg.de))} &lt;${esc(msg.de.email)}&gt;<br>
            Data: ${esc(dataLonga(msg.data))}<br>Assunto: ${esc(msg.assunto)}<br>Para: ${esc((msg.para || []).map(p => p.email).join(', '))}</p><br>${msg.citacao}</div>`;
        abrirCompor({
            titulo: 'Encaminhar', assunto: prefixo(msg.assunto, 'Enc'),
            corpo: '<p><br></p>' + blocoAssinatura() + cab,
            anexosOriginais: (msg.anexos || []).map(a => ({nome: a.nome, tamanho: a.tamanho})),
            extras: {encaminhar_pasta: msg.pasta, encaminhar_uid: msg.uid},
        });
    }
    async function abrirRascunho(uid) {
        try {
            const d = await api(URL.mensagem, {params: {pasta: E.pasta, uid: uid, imagens: '1'}});
            const m = d.mensagem;
            abrirCompor({titulo: 'Rascunho', para: m.para, cc: m.cc, assunto: m.assunto === '(sem assunto)' ? '' : m.assunto,
                corpo: m.citacao, rascunhoUid: uid});
        } catch (e) { aviso(e.message, {erro: true}); }
    }

    // ── Histórico (voltar do celular, recarregar a página) ─────────────────
    function historico(empilhar) {
        const params = new URLSearchParams();
        if (E.pasta !== 'INBOX') params.set('pasta', E.pasta);
        if (E.aberta) params.set('m', E.aberta);
        const alvo = location.pathname + (params.toString() ? '#' + params.toString() : '');
        if (empilhar) history.pushState({wm: true}, '', alvo); else history.replaceState({wm: true}, '', alvo);
    }
    function lerEndereco() {
        const p = new URLSearchParams(location.hash.slice(1));
        return {pasta: p.get('pasta') || 'INBOX', uid: Number(p.get('m')) || null};
    }
    window.addEventListener('popstate', () => {
        const alvo = lerEndereco();
        if (alvo.pasta !== E.pasta) { E.pasta = alvo.pasta; E.pagina = 1; desenharPastas(); carregarLista(); }
        if (alvo.uid) abrirMensagem(alvo.uid, {semHistorico: true});
        else fecharLeitura();
    });

    // ── Eventos ───────────────────────────────────────────────────────────
    function fecharPastasMovel() { app.classList.remove('wm-pastas-abertas'); }

    app.addEventListener('click', ev => {
        const wm = ev.target.closest('[data-wm]');
        if (wm) {
            const qual = wm.dataset.wm;
            if (qual === 'escrever') { fecharPastasMovel(); abrirCompor(); }
            else if (qual === 'atualizar') { E.resumo = null; carregarPastas(); carregarLista(); if (!E.aberta) mostrarVisaoGeral(true); }
            else if (qual === 'visao-geral') { app.dataset.vista = 'leitura'; E.aberta = null; $$('.wm-item.wm-aberto', itensEl).forEach(el => el.classList.remove('wm-aberto')); mostrarVisaoGeral(true); historico(false); }
            else if (qual === 'configurar') mostrarConfiguracoes();
            else if (qual === 'atalhos') mostrarAtalhos();
            else if (qual === 'nova-pasta') novaPasta();
            else if (qual === 'abrir-pastas') app.classList.add('wm-pastas-abertas');
            else if (qual === 'fechar-pastas') fecharPastasMovel();
            else if (qual === 'selecionar-todas') {
                const todas = E.lista.length && E.lista.every(m => E.marcadas.has(m.uid));
                E.marcadas.clear();
                if (!todas) E.lista.forEach(m => E.marcadas.add(m.uid));
                atualizarLote();
            }
            else if (qual === 'pagina-anterior' && E.pagina > 1) { E.pagina--; carregarLista(); itensEl.scrollTop = 0; }
            else if (qual === 'pagina-seguinte' && E.pagina < E.paginas) { E.pagina++; carregarLista(); itensEl.scrollTop = 0; }
            return;
        }
        const pasta = ev.target.closest('[data-pasta]');
        if (pasta) { irParaPasta(pasta.dataset.pasta); return; }
        const filtro = ev.target.closest('[data-filtro]');
        if (filtro) {
            E.filtro = filtro.dataset.filtro; E.pagina = 1;
            $$('[data-filtro]').forEach(b => b.classList.toggle('wm-ativo', b === filtro));
            carregarLista();
            return;
        }
        const lote = ev.target.closest('[data-lote]');
        if (lote) {
            if (lote.dataset.lote === 'mais') {
                abrirMenu(lote, [
                    {rotulo: 'Marcar como Importante', icone: 'fas fa-star', fn: () => executarAcao('sinalizar', E.marcadas)},
                    {rotulo: 'Tirar de Importante', icone: 'far fa-star', fn: () => executarAcao('tirar_sinal', E.marcadas)},
                    pastaAtual().papel === 'spam' ? {rotulo: 'Não é spam', icone: 'fas fa-inbox', fn: () => executarAcao('nao_spam', E.marcadas)}
                        : {rotulo: 'Marcar como spam', icone: 'fas fa-shield-virus', fn: () => executarAcao('spam', E.marcadas)},
                    '-', ...opcoesMover(Array.from(E.marcadas)),
                ]);
            } else executarAcao(lote.dataset.lote, E.marcadas);
            return;
        }
        const ler = ev.target.closest('[data-ler]');
        if (ler) { acaoLeitura(ler.dataset.ler, ler); return; }
        const geral = ev.target.closest('[data-geral],[data-geral-abrir],[data-geral-busca],[data-geral-pasta]');
        if (geral) {
            if (geral.dataset.geralAbrir) {
                const uid = Number(geral.dataset.geralAbrir);
                if (E.pasta !== 'INBOX') irParaPasta('INBOX');
                abrirMensagem(uid, {ler: true});
            } else if (geral.dataset.geralBusca) {
                buscaEl.value = geral.dataset.geralBusca; E.pasta = 'INBOX'; E.busca = buscaEl.value; E.pagina = 1; desenharPastas(); carregarLista();
            } else if (geral.dataset.geralPasta) irParaPasta(geral.dataset.geralPasta, {forcar: true});
            else {
                const f = {nao_lidas: 'nao_lidas', importantes: 'importantes'}[geral.dataset.geral] || '';
                E.pasta = 'INBOX'; E.busca = ''; buscaEl.value = ''; E.filtro = f; E.pagina = 1;
                $$('[data-filtro]').forEach(b => b.classList.toggle('wm-ativo', b.dataset.filtro === f));
                desenharPastas(); carregarLista();
                if (window.innerWidth < 768) app.dataset.vista = 'lista';
            }
            return;
        }
    });

    function acaoLeitura(qual, botao) {
        const msg = E.msg;
        if (qual === 'voltar') { history.length > 1 && history.state && history.state.wm ? history.back() : fecharLeitura(); return; }
        if (!msg) return;
        if (qual === 'responder') responder(msg, false);
        else if (qual === 'todos') responder(msg, true);
        else if (qual === 'encaminhar') encaminhar(msg);
        else if (qual === 'imagens') abrirMensagem(msg.uid, {imagens: true, semHistorico: true, ler: true});
        else if (qual === 'detalhes') document.getElementById('wmDetalhes').classList.toggle('hidden');
        else if (qual === 'anterior' || qual === 'seguinte') {
            const i = E.lista.findIndex(m => m.uid === msg.uid) + (qual === 'anterior' ? -1 : 1);
            if (E.lista[i]) abrirMensagem(E.lista[i].uid, {ler: true});
        }
        else if (qual === 'estrela') {
            const ligar = !msg.sinalizada;
            msg.sinalizada = ligar;
            botao.classList.toggle('wm-ativo', ligar);
            botao.querySelector('i').className = (ligar ? 'fas text-amber-500' : 'far') + ' fa-star';
            botao.title = ligar ? 'Tirar de Importante (S)' : 'Marcar como Importante (S)';
            const selo = document.getElementById('wmSeloImportante');
            if (ligar && !selo) $('.wm-msg h2', leituraEl).insertAdjacentHTML('afterend', '<span class="wm-selo-importante" id="wmSeloImportante"><i class="fas fa-star"></i>Importante</span>');
            if (!ligar && selo) selo.remove();
            executarAcao(ligar ? 'sinalizar' : 'tirar_sinal', [msg.uid]);
        }
        else if (qual === 'nao_lida') { executarAcao('nao_lida', [msg.uid]); fecharLeitura(); }
        else if (qual === 'mover') abrirMenu(botao, opcoesMover([msg.uid]));
        else if (qual === 'mais') {
            abrirMenu(botao, [
                {rotulo: 'Responder a todos', icone: 'fas fa-reply-all', fn: () => responder(msg, true)},
                {rotulo: 'Imprimir', icone: 'fas fa-print', fn: () => { const f = $('iframe', leituraEl); if (f && f.contentWindow) f.contentWindow.print(); }},
                {rotulo: 'Baixar original (.eml)', icone: 'fas fa-file-arrow-down', fn: () => { window.location.href = URL.original + '?' + new URLSearchParams({pasta: msg.pasta, uid: msg.uid}); }},
                {rotulo: 'Mostrar imagens', icone: 'far fa-image', fn: () => abrirMensagem(msg.uid, {imagens: true, semHistorico: true, ler: true})},
            ]);
        }
        else executarAcao(qual, [msg.uid]);
    }

    itensEl.addEventListener('click', ev => {
        const item = ev.target.closest('.wm-item[data-uid]');
        if (!item) return;
        const uid = Number(item.dataset.uid);
        if (ev.target.closest('[data-acao="estrela"]')) {
            const m = msgDaLista(uid);
            executarAcao(m && m.sinalizada ? 'tirar_sinal' : 'sinalizar', [uid]);
            return;
        }
        const rapida = ev.target.closest('[data-rapida]');
        if (rapida) { executarAcao(rapida.dataset.rapida, [uid]); return; }
        if (ev.target.closest('[data-acao="marcar"]') || ev.ctrlKey || ev.metaKey || ev.shiftKey || E.marcadas.size) {
            marcar(uid, ev.shiftKey);
            return;
        }
        abrirMensagem(uid);
    });

    // Arrastar mensagens para uma pasta.
    itensEl.addEventListener('dragstart', ev => {
        const item = ev.target.closest('.wm-item[data-uid]');
        if (!item) return;
        const uid = Number(item.dataset.uid);
        const uids = E.marcadas.has(uid) ? Array.from(E.marcadas) : [uid];
        ev.dataTransfer.setData('application/x-wm-uids', JSON.stringify(uids));
        ev.dataTransfer.effectAllowed = 'move';
        const fantasma = cria(`<div style="position:fixed;top:-100px;padding:.5rem .8rem;border-radius:.7rem;background:#FF6B35;color:#fff;font:600 13px sans-serif">${uids.length > 1 ? uids.length + ' mensagens' : 'Mover 1 mensagem'}</div>`);
        document.body.appendChild(fantasma);
        ev.dataTransfer.setDragImage(fantasma, 10, 10);
        setTimeout(() => fantasma.remove(), 0);
    });
    const colunaPastas = $('.wm-pastas', app);
    colunaPastas.addEventListener('dragover', ev => {
        const alvo = ev.target.closest('[data-pasta]');
        if (!alvo || !Array.from(ev.dataTransfer.types).includes('application/x-wm-uids') || alvo.dataset.pasta === E.pasta) return;
        ev.preventDefault();
        $$('.wm-pasta.wm-alvo').forEach(b => b !== alvo && b.classList.remove('wm-alvo'));
        alvo.classList.add('wm-alvo');
    });
    colunaPastas.addEventListener('dragleave', ev => { const alvo = ev.target.closest('[data-pasta]'); if (alvo) alvo.classList.remove('wm-alvo'); });
    colunaPastas.addEventListener('drop', ev => {
        const alvo = ev.target.closest('[data-pasta]');
        $$('.wm-pasta.wm-alvo').forEach(b => b.classList.remove('wm-alvo'));
        if (!alvo) return;
        ev.preventDefault();
        let uids = [];
        try { uids = JSON.parse(ev.dataTransfer.getData('application/x-wm-uids') || '[]'); } catch (e) { uids = []; }
        const destino = E.pastas.find(p => p.id === alvo.dataset.pasta);
        if (!uids.length || !destino) return;
        if (destino.papel === 'lixeira') executarAcao('lixeira', uids);
        else executarAcao('mover', uids, destino.id);
    });

    // Busca
    document.getElementById('wmBuscaForm').addEventListener('submit', ev => {
        ev.preventDefault();
        E.busca = buscaEl.value.trim(); E.pagina = 1;
        desenharPastas();
        carregarLista();
    });
    buscaEl.addEventListener('search', () => {
        if (!buscaEl.value && E.busca) { E.busca = ''; E.pagina = 1; desenharPastas(); carregarLista(); }
    });

    // Teclado
    document.addEventListener('keydown', ev => {
        const t = ev.target;
        if (t.closest && (t.closest('input, textarea, select, [contenteditable="true"]') || t.closest('.wm-compor, .wm-fundo-modal'))) {
            if (ev.key === 'Escape' && t === buscaEl) buscaEl.blur();
            return;
        }
        if (ev.ctrlKey || ev.metaKey || ev.altKey) return;
        const msg = E.msg;
        const k = ev.key;
        const mover = passo => {
            if (!E.lista.length) return;
            E.foco = Math.max(0, Math.min(E.lista.length - 1, (E.foco < 0 ? (passo > 0 ? -1 : 0) : E.foco) + passo));
            if (E.aberta) abrirMensagem(E.lista[E.foco].uid, {ler: true}); else marcarFoco();
        };
        const mapa = {
            c: () => abrirCompor(), '/': () => { buscaEl.focus(); buscaEl.select(); },
            j: () => mover(1), ArrowDown: () => mover(1), k: () => mover(-1), ArrowUp: () => mover(-1),
            Enter: () => { if (E.lista[E.foco]) abrirMensagem(E.lista[E.foco].uid); },
            o: () => { if (E.lista[E.foco]) abrirMensagem(E.lista[E.foco].uid); },
            x: () => { if (E.lista[E.foco]) marcar(E.lista[E.foco].uid, false); },
            r: () => responder(msg, false), a: () => responder(msg, true), f: () => encaminhar(msg),
            e: () => alvoAtual() && executarAcao('arquivo', alvoAtual()),
            '#': () => alvoAtual() && executarAcao('lixeira', alvoAtual()),
            Delete: () => alvoAtual() && executarAcao('lixeira', alvoAtual()),
            s: () => { const m = msg ? msg : E.lista[E.foco]; if (m) executarAcao(m.sinalizada ? 'tirar_sinal' : 'sinalizar', [m.uid]); },
            u: () => { if (msg) { executarAcao('nao_lida', [msg.uid]); fecharLeitura(); } },
            '?': () => mostrarAtalhos(),
            Escape: () => { if (menuAberto) fecharMenu(); else if (E.marcadas.size) { E.marcadas.clear(); atualizarLote(); } else if (E.aberta) fecharLeitura(); },
        };
        const fn = mapa[k] || mapa[k.toLowerCase && k.length === 1 ? k.toLowerCase() : k];
        if (fn) { ev.preventDefault(); fn(); }
    });
    function alvoAtual() {
        if (E.marcadas.size) return Array.from(E.marcadas);
        if (E.aberta) return [E.aberta];
        if (E.lista[E.foco]) return [E.lista[E.foco].uid];
        return null;
    }

    // ── Novidades: confere a caixa de entrada de tempos em tempos ─────────
    async function conferirNovidades() {
        if (document.hidden) return;
        try {
            const d = await api(URL.novidades);
            const entrada = pastaPorPapel('entrada');
            if (entrada && entrada.nao_lidas !== d.nao_lidas) { entrada.nao_lidas = d.nao_lidas; entrada.total = d.total; desenharPastas(); atualizarTitulo(); }
            if (E.uidnext && d.uidnext > E.uidnext) {
                const novas = d.uidnext - E.uidnext;
                E.resumo = null;
                if (E.pasta === 'INBOX' && E.pagina === 1 && !E.busca) carregarLista({silencioso: true});
                aviso(novas > 1 ? `${novas} novas mensagens` : 'Nova mensagem na caixa de entrada', {icone: 'fa-envelope',
                    acao: E.pasta === 'INBOX' ? null : {rotulo: 'Ver', fn: () => irParaPasta('INBOX')}});
            }
            E.uidnext = d.uidnext;
        } catch (e) { /* tenta de novo na próxima volta */ }
    }
    setInterval(conferirNovidades, 45000);
    document.addEventListener('visibilitychange', () => { if (!document.hidden) conferirNovidades(); });

    // ── Início ────────────────────────────────────────────────────────────
    ajustarAltura();
    const inicio = lerEndereco();
    E.pasta = inicio.pasta;
    carregarPastas();
    carregarLista().then(() => {
        if (inicio.uid) abrirMensagem(inicio.uid, {semHistorico: true, ler: true});
    });
    if (!inicio.uid) mostrarVisaoGeral();
    conferirNovidades();
    setTimeout(ajustarAltura, 300);
})();
