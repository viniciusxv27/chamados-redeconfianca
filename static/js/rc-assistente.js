/* Janela flutuante do assistente (templates/assistente/_widget.html).
 *
 * A conversa fica no sessionStorage, por usuário: sobrevive à troca de página na
 * mesma aba e não aparece para quem entrar depois no mesmo navegador. O servidor
 * só é chamado quando a pessoa abre a janela ou pergunta.
 *
 * Desde 09/11/2026 é uma conversa só (antes: abas "Orientação" e "Claude"). Tudo vai
 * para /assistente/perguntar/: com Claude, a resposta dele já vem alimentada pela
 * busca da orientação; sem Claude, vem a orientação. Os cartões (POPs, páginas,
 * chamado) aparecem nos dois casos.
 */
(function () {
    'use strict';

    var raiz = document.getElementById('rcAssist');
    if (!raiz) return;

    var fab = document.getElementById('rcaFab');
    var janela = document.getElementById('rcaJanela');
    var msgs = document.getElementById('rcaMsgs');
    var form = document.getElementById('rcaForm');
    var campo = document.getElementById('rcaTexto');
    var enviar = document.getElementById('rcaEnviar');
    var subtitulo = document.getElementById('rcaSubtitulo');
    var linkPagina = document.getElementById('rcaPagina');

    var PREFIXO = 'rcAssist:';
    var CHAVE = PREFIXO + raiz.dataset.uid;
    var MAX_FALAS = 40;
    var CELULAR = window.matchMedia('(max-width: 639px)');
    var SUGESTOES = ['Como fazer uma sangria?', 'Como abrir um chamado?', 'Onde vejo meu contracheque?', 'Como consulto minhas férias?'];

    var estado = {aberto: false, falas: [], claude: false};
    var embutido = document.getElementById('rcAssistEmbutido');
    var estadoPedido = false;
    var enviando = false;

    // -- memória da aba --
    function lerMemoria() {
        try {
            // Outra pessoa entrou nesta aba: a conversa anterior não fica guardando lugar.
            for (var i = sessionStorage.length - 1; i >= 0; i--) {
                var k = sessionStorage.key(i);
                if (k && k.indexOf(PREFIXO) === 0 && k !== CHAVE) sessionStorage.removeItem(k);
            }
            var salvo = JSON.parse(sessionStorage.getItem(CHAVE) || 'null');
            if (salvo && salvo.falas) {
                estado.aberto = !!salvo.aberto;
                estado.claude = !!salvo.claude;
                // Conversa guardada no formato antigo (duas abas): fica a da aba que estava aberta.
                estado.falas = Array.isArray(salvo.falas) ? salvo.falas
                    : ((salvo.modo === 'claude' ? salvo.falas.claude : salvo.falas.orienta) || []);
            }
        } catch (e) { /* sem armazenamento: a conversa vale só nesta página */ }
    }
    function salvar() {
        try {
            sessionStorage.setItem(CHAVE, JSON.stringify(estado));
        } catch (e) { /* idem */ }
    }

    // -- desenho --
    function el(tag, classe, texto) {
        var n = document.createElement(tag);
        if (classe) n.className = classe;
        if (texto !== undefined) n.textContent = texto;
        return n;
    }
    function fimDaConversa() { msgs.scrollTop = msgs.scrollHeight; }

    function markdown(destino, texto) {
        if (window.RCMarkdown) {
            destino.classList.add('rc-md');
            destino.innerHTML = window.RCMarkdown.render(texto);
        } else {
            destino.style.whiteSpace = 'pre-wrap';
            destino.textContent = texto;
        }
    }

    // Só caminho do próprio portal vira cartão. O servidor já garante; aqui é a segunda trava.
    function caminhoInterno(url) {
        return typeof url === 'string' && /^\/(?!\/)/.test(url);
    }

    var ICONES = {pop: 'fa-file-lines', arquivo: 'fa-file', treinamento: 'fa-circle-play', trilha: 'fa-route', pagina: 'fa-window-maximize'};

    function grupoDeCartoes(destino, titulo, itens) {
        itens = (itens || []).filter(function (i) { return caminhoInterno(i.url); });
        if (!itens.length) return;
        var grupo = el('div', 'rca-grupo');
        grupo.appendChild(el('div', 'rca-grupo-titulo', titulo));
        itens.forEach(function (item) {
            var a = el('a', 'rca-cartao');
            a.href = item.url;
            var icone = el('i', 'fas ' + (ICONES[item.tipo] || 'fa-link'));
            icone.setAttribute('aria-hidden', 'true');
            a.appendChild(icone);
            var corpo = el('span');
            corpo.appendChild(el('strong', '', item.titulo));
            var detalhe = [item.tipo === 'pagina' ? '' : item.rotulo, item.resumo].filter(Boolean).join(' · ');
            if (detalhe) corpo.appendChild(el('small', '', detalhe));
            a.appendChild(corpo);
            grupo.appendChild(a);
        });
        destino.appendChild(grupo);
    }

    function desenharFala(fala) {
        if (fala.papel === 'user') {
            msgs.appendChild(el('div', 'rca-fala rca-pessoa', fala.texto));
            return;
        }
        if (fala.erro) {
            msgs.appendChild(el('div', 'rca-fala rca-erro', fala.texto));
            return;
        }
        var bolha = el('div', 'rca-fala rca-bot');
        var texto = el('div');
        markdown(texto, fala.texto);
        bolha.appendChild(texto);
        grupoDeCartoes(bolha, 'POPs e documentos', fala.fontes);
        grupoDeCartoes(bolha, 'Páginas do portal', fala.paginas);
        if (fala.chamado && caminhoInterno(fala.chamado.url)) {
            var botao = el('a', 'rca-chamado');
            botao.href = fala.chamado.url;
            var icone = el('i', 'fas fa-ticket');
            icone.setAttribute('aria-hidden', 'true');
            botao.appendChild(icone);
            botao.appendChild(el('span', '', fala.chamado.rotulo || 'Abrir chamado'));
            bolha.appendChild(botao);
            bolha.appendChild(el('small', 'rca-chamado-nota', 'Abre o formulário preenchido para você revisar e enviar.'));
        }
        msgs.appendChild(bolha);
    }

    function desenharVazio() {
        var vazio = el('div', 'rca-vazio');
        // O robô do assistente (o mesmo do botão); sem a imagem, fica o ícone de antes.
        var icone;
        if (raiz.dataset.robo) {
            icone = el('img', 'rca-vazio-robo');
            icone.src = raiz.dataset.robo;
            icone.alt = '';
        } else {
            icone = el('i', 'fas fa-comments');
            icone.setAttribute('aria-hidden', 'true');
        }
        vazio.appendChild(icone);
        vazio.appendChild(el('p', '', estado.claude
            ? 'Pergunte o que precisar: passo a passo e POPs, seus dados do portal (ponto, vendas, metas, resultados) e os BIs. Se for o caso, deixo um chamado pronto para você revisar.'
            : 'Descreva a sua dúvida. Mostro o passo a passo, os POPs e as páginas e, se precisar, deixo um chamado pronto para você revisar.'));
        var lista = el('div', 'rca-sugestoes');
        SUGESTOES.forEach(function (s) {
            var b = el('button', 'rca-sugestao', s);
            b.type = 'button';
            b.addEventListener('click', function () { perguntar(s); });
            lista.appendChild(b);
        });
        vazio.appendChild(lista);
        msgs.appendChild(vazio);
    }

    function desenhar() {
        msgs.textContent = '';
        if (!estado.falas.length) desenharVazio();
        estado.falas.forEach(desenharFala);
        subtitulo.textContent = estado.claude ? 'Claude · POPs e seus dados' : 'Dúvidas, POPs e páginas';
        fimDaConversa();
    }

    // -- carregamento sob demanda --
    function carregarMarkdown() {
        if (window.RCMarkdown || document.getElementById('rcaScriptMarkdown')) return;
        var s = document.createElement('script');
        s.id = 'rcaScriptMarkdown';
        s.src = raiz.dataset.urlMarkdown;
        s.onload = desenhar;
        document.head.appendChild(s);
    }

    function pedirEstado() {
        if (estadoPedido) return;
        estadoPedido = true;
        fetch(raiz.dataset.urlEstado, {credentials: 'same-origin', headers: {'X-Requested-With': 'XMLHttpRequest'}})
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (d) {
                if (!d) return;
                estado.claude = !!d.claude;
                desenhar();
                salvar();
                if (d.pagina_claude && !embutido) {
                    linkPagina.href = d.pagina_claude;
                    linkPagina.hidden = false;
                }
            })
            .catch(function () { estadoPedido = false; });
    }

    // -- abrir e fechar --
    function ajustarAltura() {
        if (!CELULAR.matches || embutido) return;
        var altura = window.visualViewport ? window.visualViewport.height : window.innerHeight;
        janela.style.setProperty('--rca-altura', altura + 'px');
    }

    function abrir() {
        estado.aberto = true;
        janela.hidden = false;
        fab.setAttribute('aria-expanded', 'true');
        fab.setAttribute('aria-label', 'Fechar o assistente');
        if (CELULAR.matches) document.documentElement.classList.add('rca-travado');
        // As duas janelas moram no mesmo canto: abre uma de cada vez.
        var suporte = document.getElementById('supportChatWindow');
        if (suporte) suporte.style.display = 'none';
        ajustarAltura();
        carregarMarkdown();
        pedirEstado();
        desenhar();
        salvar();
        if (!CELULAR.matches) campo.focus();
    }

    function fechar() {
        estado.aberto = false;
        janela.hidden = true;
        fab.setAttribute('aria-expanded', 'false');
        fab.setAttribute('aria-label', 'Abrir o assistente');
        document.documentElement.classList.remove('rca-travado');
        salvar();
    }

    // -- perguntar --
    function historicoParaOServidor(falas) {
        return falas.filter(function (f) { return !f.erro; }).slice(-6).map(function (f) {
            return {papel: f.papel, texto: f.texto};
        });
    }

    function travar(sim) {
        enviando = sim;
        enviar.disabled = sim;
        campo.disabled = sim;
    }

    function perguntar(texto) {
        texto = (texto || '').trim();
        if (!texto || enviando) return;
        var falas = estado.falas;
        var corpo = {mensagem: texto, historico: historicoParaOServidor(falas)};

        falas.push({papel: 'user', texto: texto});
        if (falas.length > MAX_FALAS) falas.splice(0, falas.length - MAX_FALAS);
        campo.value = '';
        campo.style.height = 'auto';
        salvar();
        desenhar();
        travar(true);
        var pensando = el('div', 'rca-fala rca-pensando', estado.claude ? 'Pensando… consultas e análises podem levar alguns minutos.' : 'Procurando no portal…');
        msgs.appendChild(pensando);
        fimDaConversa();

        fetch(raiz.dataset.urlUnica, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {'Content-Type': 'application/json', 'X-CSRFToken': raiz.dataset.csrf, 'X-Requested-With': 'XMLHttpRequest'},
            body: JSON.stringify(corpo),
        })
            .then(function (r) {
                // Sessão vencida ou bloqueio do portal chegam como redirect para uma tela, não como JSON.
                var tipo = r.headers.get('content-type') || '';
                if (tipo.indexOf('application/json') === -1) {
                    throw new Error('A sua sessão expirou ou há uma pendência no portal. Recarregue a página.');
                }
                return r.json();
            })
            .then(function (d) {
                if (d.erro) {
                    falas.push({papel: 'assistant', erro: true, texto: d.erro});
                } else {
                    falas.push({
                        papel: 'assistant',
                        texto: d.resposta || '',
                        fontes: d.fontes || [],
                        paginas: d.paginas || [],
                        chamado: d.chamado || null,
                    });
                }
            })
            .catch(function (e) {
                var aviso = e && e.message && e.message.indexOf('Recarregue') !== -1 ? e.message : 'Sem conexão. Tente de novo.';
                falas.push({papel: 'assistant', erro: true, texto: aviso});
            })
            .then(function () {
                travar(false);
                salvar();
                desenhar();
                if (!CELULAR.matches) campo.focus();
            });
    }

    // -- eventos --
    fab.addEventListener('click', function () {
        if (janela.hidden) { abrir(); } else { fechar(); }
    });
    document.getElementById('rcaFechar').addEventListener('click', fechar);
    document.getElementById('rcaLimpar').addEventListener('click', function () {
        estado.falas = [];
        salvar();
        desenhar();
    });
    form.addEventListener('submit', function (e) {
        e.preventDefault();
        perguntar(campo.value);
    });
    campo.addEventListener('input', function () {
        campo.style.height = 'auto';
        campo.style.height = Math.min(campo.scrollHeight, 112) + 'px';
    });
    campo.addEventListener('keydown', function (e) {
        // No celular o Enter do teclado quebra linha: quem envia é o botão.
        if (e.key === 'Enter' && !e.shiftKey && !CELULAR.matches) {
            e.preventDefault();
            perguntar(campo.value);
        }
    });
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && !janela.hidden && !embutido) fechar();
    });
    // No computador o link abre em outra aba e a conversa continua aqui; no celular
    // abre na mesma (funciona também no app) e a janela sai da frente da página.
    msgs.addEventListener('click', function (e) {
        var a = e.target.closest('a[href]');
        if (!a) return;
        if (CELULAR.matches) {
            if (!embutido) { estado.aberto = false; salvar(); }
        } else {
            a.target = '_blank';
            a.rel = 'noopener';
        }
    });
    var botaoSuporte = document.getElementById('supportChatToggle');
    if (botaoSuporte && !embutido) botaoSuporte.addEventListener('click', function () { if (!janela.hidden) fechar(); });
    if (window.visualViewport) window.visualViewport.addEventListener('resize', ajustarAltura);

    // -- início --
    lerMemoria();
    if (embutido) {
        raiz.classList.add('rca-na-pagina');
        janela.classList.add('rca-embutida');
        embutido.appendChild(raiz);
        abrir();
    } else if (estado.aberto && !CELULAR.matches) {
        abrir();
    } else {
        estado.aberto = false;
    }
})();
