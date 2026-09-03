/* Encolhe a foto no próprio celular antes de enviar.
 *
 * Por que existe: foto de celular hoje tem 5 a 12 MB. Subir isso em 4G leva
 * dezenas de segundos, e se a conexão oscilar ou o app for para segundo plano
 * no meio, o corpo do multipart chega cortado — o Django responde "Bad Request
 * (400)" e a pessoa não faz ideia do que aconteceu. Reduzida para 1600px de
 * lado maior, a mesma foto vira ~300 KB e sobe de primeira.
 *
 * Uso: <form data-encolher-imagem> com um <input type="file"> dentro.
 *
 * Falha sempre para o lado de enviar: se o navegador não souber decodificar
 * (HEIC do iPhone, por exemplo), se não tiver DataTransfer, se der qualquer
 * erro — segue com o arquivo original, que é o que acontecia antes.
 */
(function () {
    'use strict';

    var LADO_MAXIMO = 1600;
    var A_PARTIR_DE = 1.5 * 1024 * 1024;   // abaixo disso não compensa mexer
    var QUALIDADE = 0.85;

    function podeEncolher(arquivo) {
        if (!arquivo || arquivo.size <= A_PARTIR_DE) return false;
        // PDF e HEIC o canvas não abre; JPEG/PNG/WebP sim.
        return /^image\/(jpeg|png|webp)$/i.test(arquivo.type || '');
    }

    function temSuporte() {
        return typeof DataTransfer !== 'undefined' &&
               typeof URL !== 'undefined' && !!URL.createObjectURL &&
               !!document.createElement('canvas').toBlob;
    }

    function encolher(arquivo) {
        return new Promise(function (resolve) {
            var url = URL.createObjectURL(arquivo);
            var img = new Image();
            var desistiu = false;

            // Rede de segurança: se a decodificação travar, manda o original.
            var relogio = setTimeout(function () {
                desistiu = true;
                URL.revokeObjectURL(url);
                resolve(arquivo);
            }, 8000);

            img.onload = function () {
                if (desistiu) return;
                clearTimeout(relogio);
                try {
                    var escala = Math.min(1, LADO_MAXIMO / Math.max(img.width, img.height));
                    if (escala >= 1) { URL.revokeObjectURL(url); return resolve(arquivo); }

                    var tela = document.createElement('canvas');
                    tela.width = Math.round(img.width * escala);
                    tela.height = Math.round(img.height * escala);
                    tela.getContext('2d').drawImage(img, 0, 0, tela.width, tela.height);

                    tela.toBlob(function (blob) {
                        URL.revokeObjectURL(url);
                        // Se por algum motivo ficou maior, fica com o original.
                        if (!blob || blob.size >= arquivo.size) return resolve(arquivo);
                        var nome = (arquivo.name || 'foto').replace(/\.[^.]+$/, '') + '.jpg';
                        resolve(new File([blob], nome, {
                            type: 'image/jpeg',
                            lastModified: arquivo.lastModified || Date.now(),
                        }));
                    }, 'image/jpeg', QUALIDADE);
                } catch (e) {
                    URL.revokeObjectURL(url);
                    resolve(arquivo);
                }
            };

            img.onerror = function () {
                if (desistiu) return;
                clearTimeout(relogio);
                URL.revokeObjectURL(url);
                resolve(arquivo);          // HEIC e afins caem aqui
            };

            img.src = url;
        });
    }

    function avisar(form, texto) {
        var alvo = form.querySelector('[data-encolher-aviso]');
        if (alvo) alvo.textContent = texto || '';
    }

    /** Todos os campos de arquivo do formulário que valem encolher. */
    function camposComImagem(form) {
        var alvos = [];
        form.querySelectorAll('input[type="file"]').forEach(function (input) {
            var arquivo = input.files && input.files[0];
            if (arquivo && podeEncolher(arquivo)) alvos.push({input: input, arquivo: arquivo});
        });
        return alvos;
    }

    document.addEventListener('submit', function (e) {
        var form = e.target;
        if (!form || !form.hasAttribute || !form.hasAttribute('data-encolher-imagem')) return;
        if (form.dataset.encolhido === '1') return;      // segunda passada: deixa ir

        // O pré-cadastro tem um campo por documento, não só um: encolhe todos
        // os que forem imagem grande, e deixa PDF e Word passarem intactos.
        var alvos = camposComImagem(form);
        if (!alvos.length || !temSuporte()) return;

        e.preventDefault();
        var botao = form.querySelector('button[type="submit"], button:not([type])');
        if (botao) botao.disabled = true;
        avisar(form, alvos.length > 1
            ? 'Preparando as imagens…' : 'Preparando a imagem…');

        Promise.all(alvos.map(function (alvo) {
            return encolher(alvo.arquivo).then(function (menor) {
                try {
                    if (menor !== alvo.arquivo) {
                        var pacote = new DataTransfer();
                        pacote.items.add(menor);
                        alvo.input.files = pacote.files;
                    }
                } catch (err) {
                    /* segue com o original */
                }
            });
        })).then(function () {
            form.dataset.encolhido = '1';
            avisar(form, '');
            if (botao) botao.disabled = false;
            if (form.requestSubmit) form.requestSubmit(botao || undefined);
            else form.submit();
        });
    }, false);
})();
