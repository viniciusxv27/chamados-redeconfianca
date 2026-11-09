/**
 * Envio seguro de formulário longo (pré-cadastro) — 09/11/2026.
 *
 * Problema: o POST normal troca a página. Com erro de rede ou do servidor, o
 * navegador mostra a tela de erro e a pessoa perde tudo — dados e documentos.
 *
 * Uso: <form data-envio-seguro data-rascunho="chave-unica"> (+ este script).
 *
 * - O envio vai por fetch com o cabeçalho X-Envio-Seguro: 1. A view responde
 *   JSON: {ok: true, redirect} ou {ok: false, erros: [...]}. Em qualquer falha a
 *   página fica como está: campos e arquivos anexados continuam lá e um aviso
 *   explica o que fazer. "Enviar de novo" reaproveita tudo.
 * - Rascunho no aparelho (localStorage) dos campos de texto e escolhas — nunca
 *   senha nem arquivo —, para sobreviver a página fechada ou recarregada. Some
 *   ao concluir e depois de RASCUNHO_DIAS.
 * - Convive com static/js/upload-imagem.js: ele encolhe as fotos e reenvia o
 *   formulário; este só age no envio que ninguém interrompeu.
 */
(function () {
    'use strict';

    var TEMPO_MAXIMO = 180000;          // 3 min: documentos grandes em 3G
    var RASCUNHO_DIAS = 7;
    var PREFIXO = 'rc-rascunho:';

    function campoGuardavel(el) {
        if (!el.name || el.disabled) return false;
        var tipo = (el.type || '').toLowerCase();
        if (tipo === 'password' || tipo === 'file' || tipo === 'hidden' || tipo === 'submit' || tipo === 'button') return false;
        if (el.name === 'csrfmiddlewaretoken') return false;
        return true;
    }

    function ler(chave) {
        try {
            var bruto = window.localStorage.getItem(PREFIXO + chave);
            if (!bruto) return null;
            var dados = JSON.parse(bruto);
            if (!dados || !dados.em || (Date.now() - dados.em) > RASCUNHO_DIAS * 864e5) {
                window.localStorage.removeItem(PREFIXO + chave);
                return null;
            }
            return dados;
        } catch (e) { return null; }
    }

    function gravar(form, chave) {
        var campos = {};
        Array.prototype.forEach.call(form.elements, function (el) {
            if (!campoGuardavel(el)) return;
            var lista = campos[el.name] || (campos[el.name] = []);
            var tipo = (el.type || '').toLowerCase();
            if (tipo === 'radio' || tipo === 'checkbox') lista.push(el.checked ? (el.value || 'on') : null);
            else lista.push(el.value);
        });
        try { window.localStorage.setItem(PREFIXO + chave, JSON.stringify({em: Date.now(), campos: campos})); } catch (e) { /* sem espaço ou bloqueado */ }
    }

    function apagar(chave) {
        try { window.localStorage.removeItem(PREFIXO + chave); } catch (e) { /* ok */ }
    }

    /** Preenche só o que está vazio: o que a página já trouxe (dados salvos ou devolvidos) vale mais. */
    function restaurar(form, chave) {
        var dados = ler(chave);
        if (!dados || !dados.campos) return 0;
        var vistos = {}, preenchidos = 0;
        Array.prototype.forEach.call(form.elements, function (el) {
            if (!campoGuardavel(el)) return;
            var i = vistos[el.name] = (vistos[el.name] === undefined ? 0 : vistos[el.name] + 1);
            var lista = dados.campos[el.name];
            if (!lista || i >= lista.length) return;
            var valor = lista[i];
            var tipo = (el.type || '').toLowerCase();
            if (tipo === 'radio' || tipo === 'checkbox') {
                if (valor !== null && !el.checked) {
                    var grupoMarcado = tipo === 'radio' && form.querySelector('input[type=radio][name="' + el.name + '"]:checked');
                    if (!grupoMarcado) { el.checked = true; preenchidos++; el.dispatchEvent(new Event('change', {bubbles: true})); }
                }
            } else if (valor && !el.value) {
                el.value = valor; preenchidos++;
                el.dispatchEvent(new Event('change', {bubbles: true}));
            }
        });
        return preenchidos;
    }

    function caixaDeAviso(form) {
        var caixa = form.querySelector('[data-envio-aviso]');
        if (!caixa) {
            caixa = document.createElement('div');
            caixa.setAttribute('data-envio-aviso', '');
            caixa.setAttribute('role', 'alert');
            caixa.setAttribute('tabindex', '-1');
            form.insertBefore(caixa, form.firstChild);
        }
        return caixa;
    }

    function avisar(form, tom, titulo, linhas) {
        var caixa = caixaDeAviso(form);
        var cores = tom === 'erro'
            ? 'background:#fef2f2;border:1px solid #fecaca;color:#991b1b'
            : (tom === 'ok' ? 'background:#f0fdf4;border:1px solid #bbf7d0;color:#166534'
                            : 'background:#fffbeb;border:1px solid #fde68a;color:#92400e');
        caixa.style.cssText = cores + ';border-radius:12px;padding:12px 14px;font-size:14px;line-height:1.45';
        caixa.innerHTML = '';
        var forte = document.createElement('strong');
        forte.textContent = titulo;
        caixa.appendChild(forte);
        if (linhas && linhas.length) {
            var ul = document.createElement('ul');
            ul.style.cssText = 'margin:6px 0 0 18px;list-style:disc';
            linhas.forEach(function (t) { var li = document.createElement('li'); li.textContent = t; ul.appendChild(li); });
            caixa.appendChild(ul);
        }
        caixa.scrollIntoView({behavior: 'smooth', block: 'center'});
        try { caixa.focus({preventScroll: true}); } catch (e) { /* ok */ }
    }

    function botaoDe(form, submitter) {
        return submitter || form.querySelector('button[type="submit"], button:not([type])');
    }

    function enviar(form, submitter) {
        var chave = form.getAttribute('data-rascunho') || location.pathname;
        var botao = botaoDe(form, submitter);
        var textoOriginal = botao ? botao.innerHTML : '';
        if (botao) { botao.disabled = true; botao.innerHTML = 'Enviando… não feche esta página'; }
        gravar(form, chave);

        var dados;
        try { dados = submitter ? new FormData(form, submitter) : new FormData(form); }
        catch (e) { dados = new FormData(form); }

        var controle = window.AbortController ? new AbortController() : null;
        var relogio = controle ? setTimeout(function () { controle.abort(); }, TEMPO_MAXIMO) : null;

        function liberar() {
            if (relogio) clearTimeout(relogio);
            if (botao) { botao.disabled = false; botao.innerHTML = textoOriginal; }
        }

        fetch(form.getAttribute('action') || location.href, {
            method: 'POST', body: dados, credentials: 'same-origin',
            headers: {'X-Envio-Seguro': '1', 'X-Requested-With': 'XMLHttpRequest'},
            signal: controle ? controle.signal : undefined
        }).then(function (resposta) {
            return resposta.text().then(function (texto) {
                var json = null;
                try { json = JSON.parse(texto); } catch (e) { /* não era JSON: falha do servidor/proxy */ }
                return {status: resposta.status, json: json};
            });
        }).then(function (r) {
            liberar();
            if (r.json && r.json.ok) {
                apagar(chave);
                avisar(form, 'ok', 'Enviado! Abrindo a próxima tela…');
                window.location.href = r.json.redirect || '/';
                return;
            }
            if (r.json && r.json.erros && r.json.erros.length) {
                var titulo = r.json.erro_inesperado ? 'Não conseguimos salvar agora.'
                    : (r.json.link_invalido ? 'Não foi possível enviar por este link.' : 'Confira antes de enviar de novo:');
                avisar(form, 'erro', titulo, r.json.erros);
                return;
            }
            if (r.status === 403) {
                avisar(form, 'erro', 'A página expirou.', [
                    'Seus dados ficaram guardados neste aparelho. Recarregue a página (os campos voltam preenchidos), anexe os documentos de novo e envie.']);
                return;
            }
            avisar(form, 'erro', 'Erro inesperado no servidor (' + r.status + ').', [
                'Nada foi perdido: seus dados e documentos continuam aqui.',
                'Confira e toque em enviar de novo. Se continuar, avise o RH.']);
        }).catch(function (erro) {
            liberar();
            var demorou = erro && erro.name === 'AbortError';
            avisar(form, 'erro', demorou ? 'O envio demorou demais e foi interrompido.' : 'Sem conexão com a internet no momento do envio.', [
                'Nada foi perdido: seus dados e documentos continuam aqui.',
                demorou ? 'Tente de novo numa rede melhor (Wi-Fi, se puder).' : 'Confira a internet e toque em enviar de novo.']);
        });
    }

    function preparar(form) {
        var chave = form.getAttribute('data-rascunho') || location.pathname;
        var n = restaurar(form, chave);
        if (n) {
            avisar(form, 'aviso', 'Recuperamos o que você tinha preenchido neste aparelho.', [
                'Confira os campos. Senha e documentos não ficam guardados: preencha e anexe de novo.']);
        }
        var espera = null;
        function guardar() {
            if (espera) clearTimeout(espera);
            espera = setTimeout(function () { gravar(form, chave); }, 600);
        }
        form.addEventListener('input', guardar);
        form.addEventListener('change', guardar);
    }

    document.addEventListener('submit', function (e) {
        var form = e.target;
        if (!form || !form.hasAttribute || !form.hasAttribute('data-envio-seguro')) return;
        // upload-imagem.js interrompe o primeiro envio para encolher as fotos e reenvia depois.
        if (e.defaultPrevented) return;
        if (!window.fetch || !window.FormData) return;     // navegador muito antigo: envio normal
        e.preventDefault();
        enviar(form, e.submitter);
    }, false);

    function iniciar() {
        document.querySelectorAll('form[data-envio-seguro]').forEach(preparar);
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', iniciar);
    else iniciar();
})();
