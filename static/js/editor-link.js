/* Link clicável no editor do comunicado.
 *
 * Usado por communications/create.html e edit.html, que têm a mesma barra de
 * ferramentas. A função pública é rcAbrirLink(); rcTirarLink() desfaz.
 *
 * Duas decisões que valem explicação:
 *
 * - **Não usa window.prompt.** O portal roda dentro de um WebView, e WebView
 *   que não implementa onJsPrompt devolve null sem abrir nada: o botão
 *   simplesmente não funcionaria, sem erro nenhum. A caixinha aqui é HTML.
 *
 * - **A seleção é guardada e devolvida na mão.** Abrir a caixa move o foco
 *   para o campo de texto e o navegador descarta a seleção do contenteditable;
 *   sem guardar o Range antes, o link seria aplicado no lugar errado.
 */
(function () {
    'use strict';

    var ESQUEMAS_PROIBIDOS = /^\s*(javascript|data|vbscript|file)\s*:/i;
    var JA_TEM_ESQUEMA = /^(https?:|mailto:|tel:)/i;
    var PARECE_EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    var PARECE_URL = /^(https?:\/\/|www\.)\S+$/i;

    var selecaoGuardada = null;
    var caixa = null;

    function editor() {
        return document.getElementById('message-editor');
    }

    function sincronizar() {
        if (typeof window.syncContent === 'function') window.syncContent();
    }

    function guardarSelecao() {
        var sel = window.getSelection();
        if (!sel || !sel.rangeCount) return;
        var r = sel.getRangeAt(0);
        var ed = editor();
        // Só interessa seleção que esteja dentro do editor.
        if (ed && ed.contains(r.commonAncestorContainer)) {
            selecaoGuardada = r.cloneRange();
        }
    }

    function devolverSelecao() {
        var ed = editor();
        if (!ed) return;
        ed.focus();
        if (!selecaoGuardada) return;
        var sel = window.getSelection();
        sel.removeAllRanges();
        sel.addRange(selecaoGuardada);
    }

    /** Endereço utilizável, ou null se for esquema perigoso. */
    function normalizar(bruto) {
        var v = (bruto || '').trim();
        if (!v) return null;
        // javascript: num href vira execução de script para quem só clicou.
        if (ESQUEMAS_PROIBIDOS.test(v)) return null;
        if (JA_TEM_ESQUEMA.test(v)) return v;
        if (v.indexOf('//') === 0) return 'https:' + v;
        if (v.charAt(0) === '/') return v;             // caminho do próprio portal
        if (PARECE_EMAIL.test(v)) return 'mailto:' + v;
        return 'https://' + v;
    }

    function eExterno(url) {
        return /^(https?:)?\/\//i.test(url) || /^(mailto|tel):/i.test(url);
    }

    function linkNaSelecao() {
        var sel = window.getSelection();
        if (!sel || !sel.rangeCount) return null;
        var no = sel.getRangeAt(0).commonAncestorContainer;
        if (no.nodeType === 3) no = no.parentNode;
        var ed = editor();
        while (no && no !== ed) {
            if (no.tagName === 'A') return no;
            no = no.parentNode;
        }
        return null;
    }

    function escapar(texto) {
        var d = document.createElement('div');
        d.textContent = texto;
        return d.innerHTML;
    }

    /** Todo link do editor sai com target/rel coerentes. */
    function ajustarLinks() {
        var ed = editor();
        if (!ed) return;
        ed.querySelectorAll('a[href]').forEach(function (a) {
            var href = a.getAttribute('href') || '';
            if (ESQUEMAS_PROIBIDOS.test(href)) {
                // Nunca deveria chegar aqui, mas se chegar (colado, herdado de
                // um comunicado antigo), o link vira texto comum.
                a.removeAttribute('href');
                return;
            }
            if (eExterno(href)) {
                a.setAttribute('target', '_blank');
                // Sem isso a página aberta ganha window.opener e pode mexer na nossa.
                a.setAttribute('rel', 'noopener noreferrer');
            } else {
                // Link interno abre na mesma aba: dentro do app, abrir fora
                // jogaria a pessoa para o navegador sem motivo.
                a.removeAttribute('target');
                a.removeAttribute('rel');
            }
        });
    }

    function aplicar(url, textoNovo, textoOriginal, linkExistente) {
        devolverSelecao();

        if (linkExistente) {
            linkExistente.setAttribute('href', url);
            if (textoNovo && textoNovo !== linkExistente.textContent) {
                linkExistente.textContent = textoNovo;
            }
            ajustarLinks();
            sincronizar();
            return;
        }

        var sel = window.getSelection();
        var temSelecao = sel && sel.rangeCount && !sel.isCollapsed;

        if (temSelecao && textoNovo === textoOriginal) {
            // Mantém negrito, cor e o que mais estiver dentro da seleção.
            document.execCommand('createLink', false, url);
        } else {
            var alvo = textoNovo || url;
            document.execCommand('insertHTML', false,
                '<a href="' + escapar(url) + '">' + escapar(alvo) + '</a>&nbsp;');
        }
        ajustarLinks();
        sincronizar();
    }

    // ── a caixinha ──────────────────────────────────────────────────────────

    function montarCaixa() {
        if (caixa) return caixa;
        caixa = document.createElement('div');
        caixa.id = 'rc-caixa-link';
        caixa.hidden = true;
        caixa.innerHTML =
            '<div class="rc-link-fundo" data-fechar></div>' +
            '<div class="rc-link-card" role="dialog" aria-label="Inserir link">' +
            '  <p class="rc-link-titulo">Link</p>' +
            '  <label class="rc-link-rot" for="rc-link-url">Endereço</label>' +
            '  <input id="rc-link-url" type="text" inputmode="url" autocomplete="off"' +
            '         placeholder="www.exemplo.com.br">' +
            '  <label class="rc-link-rot" for="rc-link-texto">Texto que aparece</label>' +
            '  <input id="rc-link-texto" type="text" autocomplete="off"' +
            '         placeholder="clique aqui">' +
            '  <p class="rc-link-erro" hidden></p>' +
            '  <div class="rc-link-botoes">' +
            '    <button type="button" class="rc-link-cancela" data-fechar>Cancelar</button>' +
            '    <button type="button" class="rc-link-ok">Aplicar</button>' +
            '  </div>' +
            '</div>';
        document.body.appendChild(caixa);

        caixa.querySelectorAll('[data-fechar]').forEach(function (el) {
            el.addEventListener('click', fechar);
        });
        caixa.querySelector('.rc-link-ok').addEventListener('click', confirmar);
        caixa.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') { e.preventDefault(); confirmar(); }
            if (e.key === 'Escape') { e.preventDefault(); fechar(); }
        });
        return caixa;
    }

    var contexto = {};

    function fechar() {
        if (caixa) caixa.hidden = true;
        var ed = editor();
        if (ed) devolverSelecao();
    }

    function erro(msg) {
        var p = caixa.querySelector('.rc-link-erro');
        p.textContent = msg || '';
        p.hidden = !msg;
    }

    function confirmar() {
        var url = normalizar(document.getElementById('rc-link-url').value);
        if (!url) {
            erro('Endereço inválido. Use algo como www.exemplo.com.br.');
            return;
        }
        var texto = document.getElementById('rc-link-texto').value.trim();
        caixa.hidden = true;
        aplicar(url, texto, contexto.textoOriginal, contexto.link);
    }

    window.rcAbrirLink = function () {
        var ed = editor();
        if (!ed) return;
        guardarSelecao();
        montarCaixa();
        erro('');

        var link = linkNaSelecao();
        var sel = window.getSelection();
        var selecionado = (sel && sel.rangeCount && !sel.isCollapsed)
            ? sel.toString().trim() : '';

        contexto = {link: link, textoOriginal: selecionado};

        var campoUrl = document.getElementById('rc-link-url');
        var campoTexto = document.getElementById('rc-link-texto');

        if (link) {
            campoUrl.value = link.getAttribute('href') || '';
            campoTexto.value = link.textContent;
            contexto.textoOriginal = link.textContent;
        } else {
            // Se a pessoa selecionou o próprio endereço, ele já vai preenchido:
            // era esse o caminho mais comum — colar a URL e depois "linkar".
            campoUrl.value = PARECE_URL.test(selecionado) ? selecionado : '';
            campoTexto.value = selecionado;
        }

        caixa.hidden = false;
        setTimeout(function () {
            (campoUrl.value ? campoTexto : campoUrl).focus();
        }, 30);
    };

    window.rcTirarLink = function () {
        var ed = editor();
        if (!ed) return;
        ed.focus();
        document.execCommand('unlink');
        sincronizar();
    };

    /** Colou uma URL sozinha? Já entra como link. Devolve true se tratou. */
    window.rcLinkDePaste = function (texto) {
        var v = (texto || '').trim();
        if (!v || /\s/.test(v)) return false;
        if (!PARECE_URL.test(v) && !PARECE_EMAIL.test(v)) return false;
        var url = normalizar(v);
        if (!url) return false;
        document.execCommand('insertHTML', false,
            '<a href="' + escapar(url) + '">' + escapar(v) + '</a>&nbsp;');
        ajustarLinks();
        sincronizar();
        return true;
    };

    document.addEventListener('DOMContentLoaded', function () {
        var ed = editor();
        if (!ed) return;
        ['keyup', 'mouseup'].forEach(function (ev) {
            ed.addEventListener(ev, guardarSelecao);
        });
        // Comunicado sendo editado pode trazer link antigo sem target/rel.
        ajustarLinks();
    });
})();
