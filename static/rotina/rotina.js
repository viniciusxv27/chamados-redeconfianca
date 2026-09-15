/* Rotina Gerencial — utilidades das telas do módulo: modais, API, filtros e
   interruptores. Tudo fica em window.Rotina; nada mais vai para o escopo global.

   Ganchos por atributo (sem JS por tela):
   - data-rt-abrir="id-do-modal"            abre o modal
   - data-rt-fechar (dentro do modal)       fecha o modal (Esc também fecha)
   - data-rt-filtro=".seletor-dos-itens"    campo de busca que filtra por data-busca
       data-rt-contador="#id"               onde escrever quantos ficaram visíveis
   - data-rt-marcar=".itens" data-valor=1|0 marca/desmarca as caixas dos itens visíveis
   - data-rt-conta-marcados="input[name=x]" escreve quantas caixas estão marcadas
   - data-rt-opcao="ativa|pode_criar" data-url="..." interruptor salvo na hora pela API */
(function () {
    'use strict';

    if (window.Rotina) { return; }

    function csrf() {
        var campo = document.querySelector('[name=csrfmiddlewaretoken]');
        if (campo && campo.value) { return campo.value; }
        var achado = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
        return achado ? decodeURIComponent(achado[1]) : '';
    }

    function avisar(mensagem, tipo) {
        if (typeof window.showToast === 'function') {
            window.showToast(mensagem, tipo || 'info');
        } else if (window.console) {
            window.console.log('[rotina]', mensagem);
        }
    }

    /* Chama a API do módulo. Resolve com o JSON quando {ok: true}; rejeita com
       um Error cuja mensagem já é para a pessoa ler. */
    function api(url, opcoes) {
        opcoes = opcoes || {};
        var metodo = opcoes.metodo || (opcoes.corpo ? 'POST' : 'GET');
        var cabecalhos = { 'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest' };
        if (metodo !== 'GET') {
            cabecalhos['Content-Type'] = 'application/json';
            cabecalhos['X-CSRFToken'] = opcoes.csrf || csrf();
        }
        return fetch(url, {
            method: metodo,
            credentials: 'same-origin',
            headers: cabecalhos,
            body: metodo === 'GET' ? undefined : JSON.stringify(opcoes.corpo || {})
        }).then(function (resposta) {
            return resposta.json().catch(function () { return null; }).then(function (dados) {
                if (!resposta.ok || !dados || dados.ok === false) {
                    var mensagem = (dados && dados.erro) || (resposta.status === 403
                        ? 'Sem permissão para isso. Atualize a página e tente de novo.'
                        : 'Não foi possível falar com o portal. Tente de novo.');
                    var erro = new Error(mensagem);
                    erro.status = resposta.status;
                    throw erro;
                }
                return dados;
            });
        }, function () {
            throw new Error('Sem conexão com o portal. Confira a internet e tente de novo.');
        });
    }

    /* ------------------------------------------------------------------ */
    /* Modais: segue a regra de custom.css — visível já no primeiro quadro, */
    /* a animação é só enfeite e fechar tira o clique na hora.              */
    /* ------------------------------------------------------------------ */
    function elemento(alvo) {
        return typeof alvo === 'string' ? document.getElementById(alvo) : alvo;
    }

    function abrirModal(alvo) {
        var modal = elemento(alvo);
        if (!modal) { return; }
        // No <body>, um ancestral com transform não prende o position: fixed.
        if (modal.parentNode !== document.body) { document.body.appendChild(modal); }
        clearTimeout(modal._rtFechando);
        modal._rtFoco = document.activeElement;
        modal.classList.remove('hidden', 'esta-fechando', 'rc-anima');
        document.body.classList.add('rt-modal-aberto');
        window.requestAnimationFrame(function () { modal.classList.add('rc-anima'); });
        var foco = modal.querySelector('[data-rt-foco]')
            || modal.querySelector('input:not([type=hidden]):not([type=checkbox]):not([type=radio]):not([type=search]), select, textarea');
        if (foco) {
            setTimeout(function () {
                try { foco.focus({ preventScroll: true }); } catch (e) { foco.focus(); }
            }, 60);
        }
    }

    function fecharModal(alvo) {
        var modal = elemento(alvo);
        if (!modal || modal.classList.contains('hidden')) { return; }
        modal.classList.add('esta-fechando');
        modal._rtFechando = setTimeout(function () {
            modal.classList.add('hidden');
            modal.classList.remove('esta-fechando', 'rc-anima');
            if (!document.querySelector('.rt-modal:not(.hidden)')) {
                document.body.classList.remove('rt-modal-aberto');
            }
            var anterior = modal._rtFoco;
            if (anterior && typeof anterior.focus === 'function' && document.contains(anterior)) {
                try { anterior.focus({ preventScroll: true }); } catch (e) { /* sem foco, sem problema */ }
            }
        }, 230);
    }

    /* ------------------------------------------------------------------ */
    /* Texto seguro                                                         */
    /* ------------------------------------------------------------------ */
    var ENTIDADES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

    function escapar(texto) {
        return String(texto == null ? '' : texto).replace(/[&<>"']/g, function (c) { return ENTIDADES[c]; });
    }

    /* Escapa tudo e só então transforma http(s)://… em link. Pontuação no fim
       da frase ("veja https://site.com.") fica fora do link. */
    function linkificar(texto) {
        return escapar(texto).replace(/\bhttps?:\/\/[^\s<]+/gi, function (url) {
            var sobra = '';
            var fim = url.match(/[.,:!?)\]]+$/);
            if (fim) {
                sobra = fim[0];
                url = url.slice(0, -sobra.length);
            }
            return '<a href="' + url + '" target="_blank" rel="noopener noreferrer">' + url + '</a>' + sobra;
        });
    }

    /* Marcas de acento (U+0300 a U+036F) que o normalize('NFD') separa das letras.
       Montado com fromCharCode para o arquivo não carregar caracteres invisíveis. */
    var ACENTOS = new RegExp('[' + String.fromCharCode(0x300) + '-' + String.fromCharCode(0x36f) + ']', 'g');

    function normalizar(texto) {
        return String(texto || '').toLowerCase().normalize('NFD').replace(ACENTOS, '');
    }

    /* ------------------------------------------------------------------ */
    /* Filtros, marcação em lote e interruptores                           */
    /* ------------------------------------------------------------------ */
    function filtrar(campo) {
        var termos = normalizar(campo.value).trim().split(/\s+/).filter(Boolean);
        var visiveis = 0;
        document.querySelectorAll(campo.getAttribute('data-rt-filtro')).forEach(function (item) {
            if (item._rtBusca === undefined) { item._rtBusca = normalizar(item.getAttribute('data-busca')); }
            var mostra = termos.every(function (termo) { return item._rtBusca.indexOf(termo) !== -1; });
            item.style.display = mostra ? '' : 'none';
            if (mostra) { visiveis += 1; }
        });
        var contador = campo.getAttribute('data-rt-contador');
        var alvo = contador && document.querySelector(contador);
        if (alvo) { alvo.textContent = visiveis; }
    }

    function contarMarcados() {
        document.querySelectorAll('[data-rt-conta-marcados]').forEach(function (el) {
            el.textContent = document.querySelectorAll(el.getAttribute('data-rt-conta-marcados') + ':checked').length;
        });
    }

    function marcarVisiveis(seletor, marcar) {
        document.querySelectorAll(seletor).forEach(function (item) {
            if (item.style.display === 'none') { return; }
            var caixa = item.querySelector('input[type=checkbox]');
            if (caixa && !caixa.disabled) { caixa.checked = marcar; }
        });
        contarMarcados();
    }

    function alternarOpcao(caixa) {
        var campo = caixa.getAttribute('data-rt-opcao');
        var corpo = {};
        corpo[campo] = caixa.checked;
        caixa.disabled = true;
        api(caixa.getAttribute('data-url'), { corpo: corpo }).then(function (dados) {
            var mensagem = caixa.getAttribute(caixa.checked ? 'data-msg-ligado' : 'data-msg-desligado');
            avisar(mensagem || 'Alteração salva.', 'success');
            var linha = caixa.closest('[data-rt-linha]');
            if (linha && campo === 'ativa') { linha.classList.toggle('rt-pessoa-inativa', !caixa.checked); }
            document.dispatchEvent(new CustomEvent('rotina:opcao', {
                detail: { campo: campo, valor: caixa.checked, rotina: dados.rotina }
            }));
        }).catch(function (erro) {
            caixa.checked = !caixa.checked;
            avisar(erro.message, 'error');
        }).then(function () {
            caixa.disabled = false;
        });
    }

    document.addEventListener('click', function (evento) {
        var alvo = evento.target;
        if (!alvo || !alvo.closest) { return; }
        var abrir = alvo.closest('[data-rt-abrir]');
        if (abrir) {
            evento.preventDefault();
            abrirModal(abrir.getAttribute('data-rt-abrir'));
            return;
        }
        var fechar = alvo.closest('[data-rt-fechar]');
        if (fechar && fechar.closest('.rt-modal')) {
            evento.preventDefault();
            fecharModal(fechar.closest('.rt-modal'));
            return;
        }
        var marcar = alvo.closest('[data-rt-marcar]');
        if (marcar) {
            evento.preventDefault();
            marcarVisiveis(marcar.getAttribute('data-rt-marcar'), marcar.getAttribute('data-valor') !== '0');
        }
    });

    document.addEventListener('keydown', function (evento) {
        if (evento.key !== 'Escape') { return; }
        var abertos = document.querySelectorAll('.rt-modal:not(.hidden):not(.esta-fechando)');
        if (abertos.length) { fecharModal(abertos[abertos.length - 1]); }
    });

    document.addEventListener('input', function (evento) {
        if (evento.target.matches && evento.target.matches('[data-rt-filtro]')) { filtrar(evento.target); }
    });

    document.addEventListener('change', function (evento) {
        var alvo = evento.target;
        if (!alvo.matches) { return; }
        if (alvo.matches('[data-rt-opcao]')) { alternarOpcao(alvo); }
        if (alvo.matches('input[type=checkbox]')) { contarMarcados(); }
    });

    window.Rotina = {
        api: api,
        csrf: csrf,
        avisar: avisar,
        abrirModal: abrirModal,
        fecharModal: fecharModal,
        escapar: escapar,
        linkificar: linkificar,
        normalizar: normalizar,
        filtrar: filtrar,
        contarMarcados: contarMarcados
    };
})();
