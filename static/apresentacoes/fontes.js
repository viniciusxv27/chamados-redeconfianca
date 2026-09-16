/* Seletor de fontes pesquisável (Google Fonts + fontes do sistema).
 *
 * Marcação: <div data-seletor-fonte data-url="…/fontes.json"><input type="hidden" name="fonte_titulo" value="Montserrat"> …</div>
 * O componente cria o campo de busca e a lista. Cada opção é escrita na própria
 * fonte — carregada sob demanda só com as letras do nome (&text=), então a
 * lista de mais de mil fontes não pesa.
 */
(function () {
    'use strict';
    let catalogo = null;
    const carregadas = new Set();

    function carregarCatalogo(url) {
        if (!catalogo) {
            catalogo = fetch(url, { credentials: 'same-origin' })
                .then((r) => r.json())
                .then((dados) => [
                    ...(dados.google || []).map((f) => ({ ...f, origem: 'google' })),
                    ...(dados.sistema || []).map((f) => ({ ...f, origem: 'sistema' })),
                ])
                .catch(() => [{ familia: 'Montserrat', categoria: 'sans-serif', origem: 'google' }]);
        }
        return catalogo;
    }

    function previa(fonte) {
        if (fonte.origem !== 'google' || carregadas.has(fonte.familia)) return;
        carregadas.add(fonte.familia);
        const link = document.createElement('link');
        link.rel = 'stylesheet';
        link.href = 'https://fonts.googleapis.com/css2?family=' + encodeURIComponent(fonte.familia) +
            '&text=' + encodeURIComponent(fonte.familia + 'AaBbCc123') + '&display=swap';
        document.head.appendChild(link);
    }

    function carregarInteira(familia) {
        const id = 'ap-fonte-' + familia.replace(/\W+/g, '-');
        if (document.getElementById(id)) return;
        const link = document.createElement('link');
        link.id = id;
        link.rel = 'stylesheet';
        link.href = 'https://fonts.googleapis.com/css2?family=' + encodeURIComponent(familia) + ':wght@400;700;900&display=swap';
        document.head.appendChild(link);
    }

    const ROTULOS = { 'sans-serif': 'sem serifa', serif: 'serifada', display: 'decorativa', handwriting: 'manuscrita', monospace: 'monoespaçada' };

    function montar(caixa) {
        const oculto = caixa.querySelector('input[type=hidden]');
        const busca = document.createElement('input');
        busca.type = 'text';
        busca.className = 'ap-campo';
        busca.setAttribute('role', 'combobox');
        busca.setAttribute('aria-expanded', 'false');
        busca.setAttribute('aria-autocomplete', 'list');
        busca.placeholder = 'Buscar entre mais de mil fontes…';
        busca.value = oculto.value;
        busca.style.fontFamily = `'${oculto.value}', Montserrat, sans-serif`;
        const lista = document.createElement('div');
        lista.className = 'ap-fontes-lista';
        lista.setAttribute('role', 'listbox');
        lista.hidden = true;
        caixa.style.position = 'relative';
        caixa.append(busca, lista);
        const exemplo = caixa.parentElement.querySelector('[data-exemplo-fonte]');
        let marcado = -1;
        let visiveis = [];
        const observador = new IntersectionObserver((entradas) => {
            entradas.forEach((e) => { if (e.isIntersecting) previa(e.target._fonte); });
        }, { root: lista });

        function escolher(fonte) {
            oculto.value = fonte.familia;
            busca.value = fonte.familia;
            busca.style.fontFamily = `'${fonte.familia}', Montserrat, sans-serif`;
            if (fonte.origem === 'google') carregarInteira(fonte.familia);
            if (exemplo) exemplo.style.fontFamily = `'${fonte.familia}', Montserrat, sans-serif`;
            fechar();
            oculto.dispatchEvent(new Event('change', { bubbles: true }));
        }

        function fechar() {
            lista.hidden = true;
            busca.setAttribute('aria-expanded', 'false');
        }

        async function filtrar() {
            const todas = await carregarCatalogo(caixa.dataset.url);
            const termo = busca.value.trim().toLowerCase();
            visiveis = (termo && termo !== oculto.value.toLowerCase()
                ? todas.filter((f) => f.familia.toLowerCase().includes(termo) || (ROTULOS[f.categoria] || '').includes(termo))
                : todas).slice(0, 250);
            lista.replaceChildren();
            observador.disconnect();
            visiveis.forEach((fonte, i) => {
                const opcao = document.createElement('button');
                opcao.type = 'button';
                opcao.className = 'ap-fonte-opcao';
                opcao.setAttribute('role', 'option');
                opcao.setAttribute('aria-selected', fonte.familia === oculto.value ? 'true' : 'false');
                const nome = document.createElement('span');
                nome.textContent = fonte.familia;
                nome.style.fontFamily = `'${fonte.familia}', Montserrat, sans-serif`;
                const tipo = document.createElement('small');
                tipo.textContent = fonte.origem === 'sistema' ? 'do sistema' : (ROTULOS[fonte.categoria] || fonte.categoria);
                opcao.append(nome, tipo);
                opcao._fonte = fonte;
                opcao.addEventListener('mousedown', (ev) => { ev.preventDefault(); escolher(fonte); });
                lista.appendChild(opcao);
                observador.observe(opcao);
                if (i === 0) marcado = 0;
            });
            if (!visiveis.length) {
                const vazio = document.createElement('p');
                vazio.className = 'px-3 py-2 text-sm';
                vazio.textContent = 'Nenhuma fonte com esse nome.';
                lista.appendChild(vazio);
            }
            lista.hidden = false;
            busca.setAttribute('aria-expanded', 'true');
        }

        busca.addEventListener('focus', () => { busca.select(); filtrar(); });
        busca.addEventListener('input', filtrar);
        busca.addEventListener('blur', () => setTimeout(() => {
            fechar();
            busca.value = oculto.value;
        }, 120));
        busca.addEventListener('keydown', (ev) => {
            const opcoes = lista.querySelectorAll('.ap-fonte-opcao');
            if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
                ev.preventDefault();
                marcado = Math.max(0, Math.min(opcoes.length - 1, marcado + (ev.key === 'ArrowDown' ? 1 : -1)));
                opcoes.forEach((o, i) => o.setAttribute('aria-selected', i === marcado ? 'true' : 'false'));
                if (opcoes[marcado]) opcoes[marcado].scrollIntoView({ block: 'nearest' });
            } else if (ev.key === 'Enter') {
                ev.preventDefault();
                if (opcoes[marcado]) escolher(opcoes[marcado]._fonte);
            } else if (ev.key === 'Escape') {
                fechar();
                busca.value = oculto.value;
            }
        });
        if (oculto.value) carregarInteira(oculto.value);
        caixa._definir = (familia) => { if (familia) escolher({ familia, origem: 'google' }); };
    }

    window.APRES_FONTES = { montar };
    document.addEventListener('DOMContentLoaded', () => {
        document.querySelectorAll('[data-seletor-fonte]').forEach(montar);
    });
})();
