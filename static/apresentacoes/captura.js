/* Captura as telas de um módulo do portal no navegador de quem pediu.
 *
 * Cada tela abre num iframe invisível da mesma origem (com ?captura_apresentacao=1,
 * que o middleware do módulo libera para iframe), espera carregar, tira os
 * avisos que cobrem a página e vira JPEG com html2canvas. A foto sai com a
 * sessão e as permissões de quem está gerando — é a tela que essa pessoa vê.
 */
(function () {
    'use strict';
    const LARGURA = 1440;
    const ALTURA = 900;

    function esperar(ms) { return new Promise((r) => setTimeout(r, ms)); }

    function comParametro(url) {
        return url + (url.includes('?') ? '&' : '?') + 'captura_apresentacao=1';
    }

    function tirarSobreposicoes(doc) {
        // Portões de comunicados, cookies e modais fixos cobrem a tela e não fazem parte do módulo.
        doc.querySelectorAll('body *').forEach((el) => {
            try {
                const estilo = doc.defaultView.getComputedStyle(el);
                if (estilo.position !== 'fixed') return;
                const caixa = el.getBoundingClientRect();
                const cobre = caixa.width * caixa.height > 0.35 * LARGURA * ALTURA;
                const aviso = /Comunicados aguardando|de acordo|cookies|Aceitar|Lembrete/i.test(el.textContent || '');
                if ((cobre && Number(estilo.zIndex || 0) >= 40) || (aviso && cobre)) el.remove();
            } catch (e) { /* elemento sumiu no meio */ }
        });
        doc.querySelectorAll('[role=dialog], .modal.show, .fixed.inset-0').forEach((el) => {
            const caixa = el.getBoundingClientRect();
            if (caixa.width * caixa.height > 0.35 * LARGURA * ALTURA) el.remove();
        });
        doc.documentElement.style.overflow = 'hidden';
    }

    async function capturar(url, { tempoLimite = 30000 } = {}) {
        const iframe = document.createElement('iframe');
        iframe.setAttribute('aria-hidden', 'true');
        iframe.tabIndex = -1;
        Object.assign(iframe.style, {
            position: 'fixed', left: '-30000px', top: '0', width: LARGURA + 'px', height: ALTURA + 'px',
            border: '0', pointerEvents: 'none',
        });
        const carregou = new Promise((resolve, reject) => {
            const relogio = setTimeout(() => reject(new Error('A tela demorou demais para abrir.')), tempoLimite);
            iframe.addEventListener('load', () => { clearTimeout(relogio); resolve(); }, { once: true });
        });
        iframe.src = comParametro(url);
        document.body.appendChild(iframe);
        try {
            await carregou;
            const doc = iframe.contentDocument;
            if (!doc || !doc.body || (doc.contentType && doc.contentType !== 'text/html')) {
                throw new Error('A tela não é uma página.');
            }
            if (new URL(iframe.contentWindow.location.href).pathname.startsWith('/login')) {
                throw new Error('A sessão expirou.');
            }
            // Tailwind (CDN) e fontes montam o visual depois do load.
            await esperar(1800);
            if (doc.fonts && doc.fonts.ready) await Promise.race([doc.fonts.ready, esperar(3000)]);
            tirarSobreposicoes(doc);
            await esperar(250);
            const fundo = doc.defaultView.getComputedStyle(doc.body).backgroundColor;
            const tela = await window.html2canvas(doc.documentElement, {
                width: LARGURA, height: ALTURA, windowWidth: LARGURA, windowHeight: ALTURA, x: 0, y: 0,
                scale: 1, useCORS: true, logging: false,
                backgroundColor: fundo && fundo !== 'rgba(0, 0, 0, 0)' ? fundo : '#ffffff',
            });
            return await new Promise((resolve) => tela.toBlob(resolve, 'image/jpeg', 0.88));
        } finally {
            iframe.remove();
        }
    }

    async function enviar(blob, { urlUpload, apresentacao, nome, csrf }) {
        const dados = new FormData();
        dados.append('arquivo', blob, (nome || 'tela').replace(/[^\w-]+/g, '-').slice(0, 60) + '.jpg');
        dados.append('apresentacao', apresentacao);
        dados.append('origem', 'CAPTURA');
        dados.append('nome', nome || 'Tela do portal');
        const resposta = await fetch(urlUpload, {
            method: 'POST', body: dados, credentials: 'same-origin', headers: { 'X-CSRFToken': csrf },
        });
        const json = await resposta.json().catch(() => ({}));
        if (!resposta.ok || !json.ok) throw new Error(json.erro || 'Não foi possível guardar a captura.');
        return json.midia;
    }

    window.APRES_CAPTURA = { capturar, enviar };
})();
