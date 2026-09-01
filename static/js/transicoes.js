/* Transição ao trocar de tela — ver static/css/transicoes.css.
 *
 * Este arquivo faz duas coisas:
 *   1. decide qual dos dois caminhos o navegador vai usar (marca .rc-fallback);
 *   2. mostra a barrinha de progresso entre o toque no link e a tela nova
 *      aparecer, que é o intervalo em que o WebView não dá sinal nenhum.
 *
 * Regra de ouro: nada aqui pode deixar a tela apagada. Todo esmaecimento tem
 * volta por tempo, por pageshow e por visibilitychange.
 */
(function () {
    'use strict';

    var raiz = document.documentElement;
    var reduzido = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    // A transição entre documentos chegou junto com o evento pagereveal
    // (Chrome 126, Safari 18.2). Sem ele, o CSS @view-transition é ignorado e
    // quem anima é o .rc-fallback.
    var nativo = ('onpagereveal' in window) &&
                 !!(window.CSS && CSS.supports && CSS.supports('view-transition-name', 'none'));
    if (!nativo && !reduzido) {
        raiz.classList.add('rc-fallback');
    }

    var barra = null;
    var voltarDepois = null;

    function pegarBarra() {
        if (!barra) {
            barra = document.getElementById('rc-barra');
        }
        if (!barra && document.body) {
            barra = document.createElement('div');
            barra.id = 'rc-barra';
            barra.setAttribute('aria-hidden', 'true');
            barra.appendChild(document.createElement('span'));
            document.body.appendChild(barra);
        }
        return barra;
    }

    function comecar() {
        var b = pegarBarra();
        if (b) {
            // reinicia a animação da barra a cada clique
            var tira = b.firstChild;
            tira.style.animation = 'none';
            void tira.offsetWidth;
            tira.style.animation = '';
            b.classList.add('rc-barra-on');
        }
        raiz.classList.add('rc-saindo');

        clearTimeout(voltarDepois);
        // Se depois disso ainda estamos aqui, a navegação não aconteceu:
        // era download, um confirm() cancelado ou um link que não levou a lugar
        // nenhum. Devolve a tela ao normal.
        voltarDepois = setTimeout(desfazer, 1400);
    }

    function desfazer() {
        clearTimeout(voltarDepois);
        raiz.classList.remove('rc-saindo');
        if (barra) {
            barra.classList.remove('rc-barra-on');
        }
    }

    function navegacaoNormal(e, a) {
        if (e.defaultPrevented) return false;
        if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return false;
        if (!a || !a.href) return false;
        if (a.hasAttribute('download')) return false;
        if (a.target && a.target !== '_self') return false;

        var href = a.getAttribute('href') || '';
        if (!href || href.charAt(0) === '#') return false;
        if (/^(javascript|mailto|tel|sms|whatsapp|blob|data):/i.test(href)) return false;

        var url;
        try {
            url = new URL(a.href, location.href);
        } catch (err) {
            return false;
        }
        if (url.origin !== location.origin) return false;

        // Link que baixa arquivo não troca de tela: a barra ficaria correndo à
        // toa e o conteúdo esmaeceria sem motivo.
        if (/\/(download|export|exportar|baixar)/i.test(url.pathname)) return false;
        if (/(^|[?&])(export|download|formato|format)=/i.test(url.search)) return false;
        if (/^\/media\//i.test(url.pathname)) return false;

        // Só mudou a âncora: continua na mesma tela.
        if (url.pathname === location.pathname && url.search === location.search && url.hash) return false;

        return true;
    }

    // Os dois escutam na subida, não na captura: assim os handlers da própria
    // página já rodaram e o e.defaultPrevented diz a verdade. Metade dos botões
    // do portal é onsubmit="return confirm(...)" — em captura a tela esmaecia
    // enquanto o diálogo estava aberto e voltava sozinha depois, o que parecia
    // defeito. Em troca, quem chamar stopPropagation perde a barrinha; deixar de
    // animar é bem melhor do que animar no momento errado.
    document.addEventListener('click', function (e) {
        var a = e.target && e.target.closest ? e.target.closest('a') : null;
        if (!a) return;
        if (!navegacaoNormal(e, a)) return;
        comecar();
    });

    // Formulário que envia também troca de tela — menos os de busca que só
    // filtram na própria página via JS (esses chamam preventDefault).
    document.addEventListener('submit', function (e) {
        var f = e.target;
        if (e.defaultPrevented || !f || f.hasAttribute('data-sem-transicao')) return;
        if (f.target && f.target !== '_self') return;
        comecar();
    });

    // Voltar pelo histórico traz a página do cache já pronta: nada de barra
    // parada nem conteúdo apagado.
    window.addEventListener('pageshow', desfazer);
    window.addEventListener('pagehide', desfazer);
    document.addEventListener('visibilitychange', function () {
        if (document.visibilityState === 'visible') desfazer();
    });
})();
