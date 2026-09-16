/* Rotina Gerencial — o calendário da semana (segunda a sábado).

   Lê a configuração e os dados dos json_script "rt-config" e "rt-dados" e
   monta o FullCalendar: a semana inteira na tela larga e, no celular, um dia
   por vez — em lista (padrão, fácil de ler com o polegar) ou em grade (para
   arrastar), com as abas de seg a sáb presas no topo e troca de dia por toque
   ou deslizando para o lado. Toda mudança passa pela API; se o servidor
   recusar (atividade travada, horário inválido), o bloco volta para onde
   estava e a mensagem aparece.

   O relógio é o do servidor: a página traz a hora "de parede" do portal e o
   calendário soma o tempo que passou desde que abriu. Assim a linha do
   "agora", o destaque da atividade atual e o cartão lateral batem com os
   avisos, mesmo com o relógio do aparelho errado ou em outro fuso. */
(function () {
    'use strict';

    var LARGURA_DIA = 768;   // abaixo disso, um dia por vez
    var MINUTO = 60000;
    var CHAVE_VISTA = 'rotina-vista-dia';   // lista | grade, lembrado por aparelho
    var MESES = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto',
        'setembro', 'outubro', 'novembro', 'dezembro'];

    function gancho(raiz, nome) { return raiz ? raiz.querySelector('[data-rt="' + nome + '"]') : null; }
    function doisDigitos(n) { return (n < 10 ? '0' : '') + n; }
    function hhmm(data) { return doisDigitos(data.getHours()) + ':' + doisDigitos(data.getMinutes()); }
    function paraMinutos(texto) { var p = String(texto).split(':'); return (+p[0]) * 60 + (+p[1]); }
    function deMinutos(total) { return doisDigitos(Math.floor(total / 60)) + ':' + doisDigitos(total % 60); }
    function lerData(iso) { var p = iso.split('-'); return new Date(+p[0], +p[1] - 1, +p[2]); }
    function somarDias(data, dias) { var d = new Date(data.getTime()); d.setDate(d.getDate() + dias); return d; }
    function diaDaSemana(data) { return (data.getDay() + 6) % 7; }   // segunda = 0 … domingo = 6
    function comHora(data, texto) {
        var d = new Date(data.getTime());
        var minutos = paraMinutos(texto);
        d.setHours(Math.floor(minutos / 60), minutos % 60, 0, 0);
        return d;
    }
    function mesmoDia(inicio, fim) {
        var ultimo = new Date(fim.getTime() - 1);
        return inicio.getFullYear() === ultimo.getFullYear() && inicio.getMonth() === ultimo.getMonth()
            && inicio.getDate() === ultimo.getDate();
    }
    function duracao(minutos) {
        if (minutos < 60) { return minutos + ' min'; }
        var horas = Math.floor(minutos / 60), resto = minutos % 60;
        return horas + 'h' + (resto ? doisDigitos(resto) : '');
    }
    function plural(n, um, varios) { return n + ' ' + (n === 1 ? um : varios); }
    function lerJson(id) {
        var el = document.getElementById(id);
        if (!el) { return null; }
        try { return JSON.parse(el.textContent); } catch (e) { return null; }
    }
    function criar(tag, classe, texto) {
        var el = document.createElement(tag);
        if (classe) { el.className = classe; }
        if (texto !== undefined && texto !== null) { el.textContent = texto; }
        return el;
    }
    function icone(nome) {
        var i = criar('i', 'fas ' + nome);
        i.setAttribute('aria-hidden', 'true');
        return i;
    }
    function mostrarErro(el, mensagem) { if (el) { el.textContent = mensagem; el.classList.remove('hidden'); } }
    function esconderErro(el) { if (el) { el.textContent = ''; el.classList.add('hidden'); } }
    function horarioValido(inicio, fim) {
        if (!/^\d{2}:\d{2}$/.test(inicio) || !/^\d{2}:\d{2}$/.test(fim)) { return 'Informe o horário de início e de fim.'; }
        if (inicio < '05:00' || fim > '23:00') { return 'Os horários precisam ficar entre 05:00 e 23:00.'; }
        if (fim <= inicio) { return 'O fim precisa ser depois do início.'; }
        return '';
    }
    function lerPreferencia(chave) { try { return window.localStorage.getItem(chave); } catch (e) { return null; } }
    function gravarPreferencia(chave, valor) { try { window.localStorage.setItem(chave, valor); } catch (e) { /* sem storage */ } }

    function montar(cfg, dados, app, alvo) {
        var R = window.Rotina;
        var pessoa = cfg.modo === 'pessoa';
        var semana = dados.semana;
        var segunda = lerData(semana.inicio);
        var atividades = {};
        (dados.atividades || []).forEach(function (a) { atividades[a.id] = a; });

        var parede = semana.agora.split(/[-T:]/);
        var paredeInicial = new Date(+parede[0], +parede[1] - 1, +parede[2], +parede[3], +parede[4], +(parede[5] || 0));
        var abertaEm = Date.now();
        function agora() { return new Date(paredeInicial.getTime() + (Date.now() - abertaEm)); }
        function minutoDoDia(data) { return data.getHours() * 60 + data.getMinutes() + data.getSeconds() / 60; }

        var hojeIdx = -1;
        semana.dias.forEach(function (d) { if (d.hoje) { hojeIdx = d.dia_semana; } });
        var temHoje = pessoa && hojeIdx >= 0 && !semana.proxima;

        function nomeDoDia(idx) { return (semana.dias[idx] || {}).nome || ''; }
        function dataCurta(idx) { var d = (semana.dias[idx] || {}).data || ''; return d ? d.slice(8, 10) + '/' + d.slice(5, 7) : ''; }
        function dataPorExtenso(idx) {
            var d = (semana.dias[idx] || {}).data || '';
            return d ? (+d.slice(8, 10)) + ' de ' + MESES[+d.slice(5, 7) - 1] : '';
        }
        function urlDe(modelo, id) {
            var i = modelo.lastIndexOf('/0/');
            return modelo.slice(0, i) + '/' + id + '/' + modelo.slice(i + 3);
        }

        /* O mesmo corte do CSS (matchMedia), e não window.innerWidth: em alguns WebViews o
           innerWidth ainda vale 980 quando a página abre, e o celular ficava com a semana inteira. */
        var consultaDia = window.matchMedia ? window.matchMedia('(max-width: ' + (LARGURA_DIA - 0.02) + 'px)') : null;
        function modoDia() { return consultaDia ? consultaDia.matches : window.innerWidth < LARGURA_DIA; }
        var vista = lerPreferencia(CHAVE_VISTA) === 'grade' ? 'grade' : 'lista';
        function emLista() { return modoDia() && vista === 'lista'; }

        /* Os modais vão para o <body>: um ancestral com transform prenderia o position: fixed. */
        var modalDet = document.getElementById('rt-modal-detalhe');
        var modalForm = document.getElementById('rt-modal-form');
        [modalDet, modalForm].forEach(function (m) { if (m && m.parentNode !== document.body) { document.body.appendChild(m); } });

        var diaAtual = temHoje ? hojeIdx : 0;
        var destaque = cfg.destaque || {};
        if (destaque.dia !== null && destaque.dia !== undefined) { diaAtual = destaque.dia; }
        if (destaque.atividade && atividades[destaque.atividade]) { diaAtual = atividades[destaque.atividade].dia_semana; }

        /* ---------------------------------------------------------------- */
        /* O que está acontecendo agora                                       */
        /* ---------------------------------------------------------------- */
        function atividadesDoDia(idx) {
            return Object.keys(atividades).map(function (id) { return atividades[id]; })
                .filter(function (a) { return a.dia_semana === idx; })
                .sort(function (x, y) {
                    return paraMinutos(x.inicio) - paraMinutos(y.inicio) || paraMinutos(x.fim) - paraMinutos(y.fim);
                });
        }

        /* A atividade de agora e a próxima de hoje (só na rotina da pessoa, na semana corrente). */
        function situacaoDeHoje() {
            var resultado = { atual: null, proxima: null, minuto: minutoDoDia(agora()) };
            if (!temHoje) { return resultado; }
            atividadesDoDia(hojeIdx).forEach(function (a) {
                var ini = paraMinutos(a.inicio), fim = paraMinutos(a.fim);
                if (!resultado.atual && ini <= resultado.minuto && resultado.minuto < fim) { resultado.atual = a; }
                if (!resultado.proxima && ini > resultado.minuto) { resultado.proxima = a; }
            });
            return resultado;
        }

        /* ---------------------------------------------------------------- */
        /* Eventos                                                            */
        /* ---------------------------------------------------------------- */
        function evento(a) {
            var data = somarDias(segunda, a.dia_semana);
            var classes = ['rt-cat-' + String(a.categoria).toLowerCase(), a.pode_mover ? 'rt-livre' : 'rt-travada'];
            var atual = situacaoDeHoje().atual;
            if (atual && atual.id === a.id) { classes.push('rt-ev-agora'); }
            return {
                id: String(a.id),
                title: a.titulo,
                start: comHora(data, a.inicio),
                end: comHora(data, a.fim),
                editable: !!a.pode_mover,
                classNames: classes
            };
        }

        /* A faixa de horas da grade. Quem só consulta a própria rotina vê o dia dela, sem
           horas vazias em volta; quem cria (gestão, modelo, pessoa com "pode criar") ganha
           folga para arrastar uma atividade nova antes ou depois. */
        function faixaDeHorario() {
            var justa = pessoa && !cfg.podeCriar;
            var minimo = justa ? null : 7 * 60, maximo = justa ? null : 20 * 60;
            Object.keys(atividades).forEach(function (id) {
                var a = atividades[id];
                var ini = Math.floor(paraMinutos(a.inicio) / 60) * 60, fim = Math.ceil(paraMinutos(a.fim) / 60) * 60;
                minimo = minimo === null ? ini : Math.min(minimo, ini);
                maximo = maximo === null ? fim : Math.max(maximo, fim);
            });
            if (minimo === null) { minimo = 8 * 60; maximo = 18 * 60; }
            return { min: deMinutos(minimo) + ':00', max: deMinutos(Math.min(maximo, 24 * 60)) + ':00' };
        }

        function rolagemInicial() {
            var faixa = paraMinutos(faixaDeHorario().min);
            var alvoMin = faixa;
            if (destaque.atividade && atividades[destaque.atividade]) {
                alvoMin = paraMinutos(atividades[destaque.atividade].inicio) - 45;
            } else if (temHoje) {
                alvoMin = Math.floor(minutoDoDia(agora())) - 60;
            }
            return deMinutos(Math.max(faixa, Math.min(alvoMin, 22 * 60))) + ':00';
        }

        function altura() {
            if (modoDia()) { return Math.max(460, window.innerHeight - 190); }
            return Math.max(560, Math.min(window.innerHeight - 150, 1100));
        }

        var faixaInicial = faixaDeHorario();
        var calendario = new FullCalendar.Calendar(alvo, {
            initialView: modoDia() ? 'timeGridDay' : 'timeGridWeek',
            initialDate: modoDia() ? somarDias(segunda, diaAtual) : segunda,
            validRange: { start: segunda, end: somarDias(segunda, 6) },
            headerToolbar: false,
            firstDay: 1,
            hiddenDays: [0],
            allDaySlot: false,
            dayHeaders: !modoDia(),
            slotMinTime: faixaInicial.min,
            slotMaxTime: faixaInicial.max,
            slotDuration: '00:15:00',
            slotLabelInterval: '01:00',
            snapDuration: '00:05:00',
            scrollTime: rolagemInicial(),
            scrollTimeReset: false,
            nowIndicator: temHoje,
            now: agora,
            height: altura(),
            expandRows: true,
            stickyHeaderDates: true,
            slotEventOverlap: false,
            eventMinHeight: 16,
            eventShortHeight: 28,
            forceEventDuration: true,
            defaultTimedEventDuration: '00:30:00',
            editable: true,
            selectable: !!cfg.podeCriar,
            selectMirror: true,
            unselectAuto: true,
            longPressDelay: 350,
            eventLongPressDelay: 350,
            selectLongPressDelay: 450,
            windowResizeDelay: 150,
            noEventsText: 'Nenhuma atividade',
            events: Object.keys(atividades).map(function (id) { return evento(atividades[id]); }),

            dayHeaderContent: function (arg) {
                var idx = diaDaSemana(arg.date);
                var info = semana.dias[idx] || {};
                var raiz = criar('div', 'rt-dh');
                if (cfg.mostrarDatas) {
                    if (pessoa && info.hoje) { raiz.classList.add('rt-dh-hoje'); }
                    raiz.appendChild(criar('span', 'rt-dh-nome', info.curto || ''));
                    raiz.appendChild(criar('span', 'rt-dh-num', String(arg.date.getDate())));
                } else {
                    raiz.classList.add('rt-dh-sem-data');
                    raiz.appendChild(criar('span', 'rt-dh-nome', (info.nome || '').replace('-feira', '')));
                }
                return { domNodes: [raiz] };
            },

            slotLabelContent: function (arg) { return hhmm(arg.date); },

            eventContent: function (arg) {
                var a = atividades[arg.event.id] || {};
                var inicio = arg.event.start;
                var fim = arg.event.end || arg.event.start;
                var minutos = Math.round((fim - inicio) / MINUTO);
                var curto = minutos < 30;
                var raiz = criar('div', 'rt-ev' + (curto ? ' rt-ev-curto' : ''));
                var marca = null;
                if (a.bloqueada) { marca = icone('fa-lock rt-ev-icone'); }
                else if (a.criada_pela_pessoa) { marca = icone('fa-user-pen rt-ev-icone'); }
                var titulo = criar('span', 'rt-ev-titulo', arg.event.title);
                if (curto) {
                    if (marca) { raiz.appendChild(marca); }
                    raiz.appendChild(criar('span', 'rt-ev-hora', hhmm(inicio)));
                    raiz.appendChild(titulo);
                } else {
                    var topo = criar('div', 'rt-ev-topo');
                    topo.appendChild(titulo);
                    if (marca) { topo.appendChild(marca); }
                    raiz.appendChild(topo);
                    raiz.appendChild(criar('span', 'rt-ev-hora', hhmm(inicio) + '–' + hhmm(fim)));
                }
                return { domNodes: [raiz] };
            },

            eventDidMount: function (info) {
                var a = atividades[info.event.id];
                if (!a) { return; }
                info.el.setAttribute('aria-label', a.titulo + ', ' + nomeDoDia(a.dia_semana) + ', das '
                    + a.inicio + ' às ' + a.fim + (a.bloqueada ? ', horário travado' : ''));
            },

            selectAllow: function (selecao) {
                return mesmoDia(selecao.start, selecao.end) && diaDaSemana(selecao.start) <= 5;
            },
            eventAllow: function (destino) {
                return mesmoDia(destino.start, destino.end) && diaDaSemana(destino.start) <= 5;
            },

            select: function (info) {
                calendario.unselect();
                var fim = info.end;
                if (fim - info.start <= 15 * MINUTO) { fim = new Date(info.start.getTime() + 30 * MINUTO); }
                abrirFormulario({ dia: diaDaSemana(info.start), inicio: hhmm(info.start), fim: hhmm(fim) });
            },

            eventClick: function (info) {
                info.jsEvent.preventDefault();
                var a = atividades[info.event.id];
                if (a) { abrirAtividade(a); }
            },

            eventDrop: function (info) { salvarHorario(info.event, info.revert); },
            eventResize: function (info) { salvarHorario(info.event, info.revert); },
            windowResize: function () { aplicarModo(); }
        });

        function abrirAtividade(a) {
            if (!pessoa && a.pode_editar) { abrirFormulario({ atividade: a }); } else { abrirDetalhe(a); }
        }

        /* ---------------------------------------------------------------- */
        /* Mudanças                                                           */
        /* ---------------------------------------------------------------- */
        function depoisDeMudar() {
            var faixa = faixaDeHorario();
            if (calendario.getOption('slotMinTime') !== faixa.min) { calendario.setOption('slotMinTime', faixa.min); }
            if (calendario.getOption('slotMaxTime') !== faixa.max) { calendario.setOption('slotMaxTime', faixa.max); }
            atualizarLegenda();
            atualizarPainelHoje();
            desenharDia();
        }

        function atualizarAtividade(a) {
            atividades[a.id] = a;
            var existente = calendario.getEventById(String(a.id));
            if (existente) { existente.remove(); }
            calendario.addEvent(evento(a));
            depoisDeMudar();
        }

        function removerAtividade(id) {
            delete atividades[id];
            var existente = calendario.getEventById(String(id));
            if (existente) { existente.remove(); }
            depoisDeMudar();
        }

        function salvarHorario(ev, desfazer) {
            var corpo = { dia_semana: diaDaSemana(ev.start), inicio: hhmm(ev.start), fim: hhmm(ev.end) };
            R.api(urlDe(cfg.urls.atualizar, ev.id), { corpo: corpo, csrf: cfg.csrf }).then(function (resposta) {
                var a = resposta.atividade;
                atualizarAtividade(a);
                R.avisar(a.titulo + ': ' + nomeDoDia(a.dia_semana) + ', ' + a.inicio + '–' + a.fim + '.', 'success');
            }).catch(function (erro) {
                desfazer();
                R.avisar(erro.message, 'error');
            });
        }

        function excluir(a, depois) {
            var pergunta = 'Excluir "' + a.titulo + '" (' + nomeDoDia(a.dia_semana) + ', ' + a.inicio + '–' + a.fim + ')?';
            if (!window.confirm(pergunta)) { return; }
            R.api(urlDe(cfg.urls.excluir, a.id), { corpo: {}, csrf: cfg.csrf }).then(function () {
                removerAtividade(a.id);
                R.avisar('Atividade excluída.', 'success');
                if (depois) { depois(); }
            }).catch(function (erro) {
                R.avisar(erro.message, 'error');
            });
        }

        /* Destaque passageiro de uma atividade (vinda do aviso, do cartão "Agora" ou da lista de hoje). */
        var destacada = { id: null, ate: 0 };
        var temporizadorDestaque = null;
        function focarAtividade(id, abrir) {
            var a = atividades[id];
            if (!a) { return; }
            destacada = { id: String(id), ate: Date.now() + 5000 };
            clearTimeout(temporizadorDestaque);
            if (modoDia()) { irParaDia(a.dia_semana); }   // na lista, já desenha com o destaque
            if (emLista()) {
                var item = app.querySelector('[data-rt-item="' + id + '"]');
                if (item && item.scrollIntoView) { item.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
            } else {
                if (modoDia()) {
                    var cartao = app.querySelector('.rt-cal-card');
                    if (cartao && cartao.scrollIntoView) { cartao.scrollIntoView({ behavior: 'smooth', block: 'start' }); }
                }
                calendario.scrollToTime(deMinutos(Math.max(paraMinutos(a.inicio) - 45, 0)) + ':00');
                calendario.getEvents().forEach(function (ev) {
                    var classes = ev.classNames.filter(function (c) { return c !== 'rt-destaque'; });
                    if (ev.id === String(id)) { classes = classes.concat('rt-destaque'); }
                    if (classes.length !== ev.classNames.length || ev.id === String(id)) { ev.setProp('classNames', classes); }
                });
            }
            temporizadorDestaque = setTimeout(function () {
                destacada = { id: null, ate: 0 };
                var ev = calendario.getEventById(String(id));
                if (ev) { ev.setProp('classNames', ev.classNames.filter(function (c) { return c !== 'rt-destaque'; })); }
                var item = app.querySelector('[data-rt-item="' + id + '"]');
                if (item) { item.classList.remove('rt-li-destaque'); }
            }, 5000);
            if (abrir) { abrirAtividade(a); }
        }

        /* Na grade, a atividade que está acontecendo ganha contorno (a classe muda sozinha com o relógio). */
        function marcarAgoraNaGrade() {
            if (!temHoje) { return; }
            var atual = situacaoDeHoje().atual;
            var idAtual = atual ? String(atual.id) : null;
            calendario.getEvents().forEach(function (ev) {
                var tem = ev.classNames.indexOf('rt-ev-agora') !== -1;
                var deve = ev.id === idAtual;
                if (tem === deve) { return; }
                var classes = ev.classNames.filter(function (c) { return c !== 'rt-ev-agora'; });
                ev.setProp('classNames', deve ? classes.concat('rt-ev-agora') : classes);
            });
        }

        /* ---------------------------------------------------------------- */
        /* Celular: um dia por vez — abas, lista e troca por deslize          */
        /* ---------------------------------------------------------------- */
        function montarAbasDias() {
            var caixa = gancho(app, 'dias');
            if (!caixa) { return; }
            caixa.innerHTML = '';
            semana.dias.forEach(function (d) {
                var botao = criar('button', 'rt-dia');
                botao.type = 'button';
                botao.setAttribute('role', 'tab');
                botao.setAttribute('data-dia', d.dia_semana);
                botao.setAttribute('aria-label', d.nome + (cfg.mostrarDatas ? ', ' + dataPorExtenso(d.dia_semana) : '')
                    + (temHoje && d.hoje ? ' (hoje)' : ''));
                if (temHoje && d.hoje) { botao.classList.add('rt-dia-hoje'); }
                botao.appendChild(criar('span', 'rt-dia-nome', d.curto));
                botao.appendChild(criar('span', 'rt-dia-num', cfg.mostrarDatas ? String(d.numero) : ''));
                botao.addEventListener('click', function () { irParaDia(d.dia_semana); });
                caixa.appendChild(botao);
            });
            contarPorDia();
        }

        /* Dia sem nenhuma atividade fica apagado na aba. */
        function contarPorDia() {
            app.querySelectorAll('.rt-dia').forEach(function (botao) {
                botao.classList.toggle('rt-dia-vazio', !atividadesDoDia(+botao.getAttribute('data-dia')).length);
            });
        }

        function marcarAbaAtiva() {
            app.querySelectorAll('.rt-dia').forEach(function (botao) {
                var ativa = +botao.getAttribute('data-dia') === diaAtual;
                botao.classList.toggle('rt-dia-ativo', ativa);
                botao.setAttribute('aria-selected', ativa ? 'true' : 'false');
                botao.tabIndex = ativa ? 0 : -1;
            });
        }

        function irParaDia(idx, direcao) {
            if (idx < 0 || idx > 5) { return; }
            var mudou = idx !== diaAtual;
            diaAtual = idx;
            if (modoDia()) { calendario.gotoDate(somarDias(segunda, idx)); }
            marcarAbaAtiva();
            desenharDia(mudou ? direcao || 0 : 0);
        }

        function aplicarModo() {
            var dia = modoDia();
            var eraLista = app.classList.contains('rt-vista-lista') && app.classList.contains('rt-app-dia');
            app.classList.toggle('rt-app-dia', dia);
            app.classList.toggle('rt-vista-lista', dia && vista === 'lista');
            app.classList.toggle('rt-vista-grade', !dia || vista === 'grade');
            var tipo = dia ? 'timeGridDay' : 'timeGridWeek';
            if (calendario.view.type !== tipo) {
                calendario.changeView(tipo, dia ? somarDias(segunda, diaAtual) : segunda);
            }
            if (calendario.getOption('dayHeaders') !== !dia) { calendario.setOption('dayHeaders', !dia); }
            calendario.setOption('height', altura());
            app.querySelectorAll('[data-rt-vista]').forEach(function (botao) {
                botao.setAttribute('aria-pressed', botao.getAttribute('data-rt-vista') === vista ? 'true' : 'false');
            });
            // A grade estava escondida: o FullCalendar precisa medir de novo.
            if (eraLista && !emLista()) { calendario.updateSize(); }
            medirTopo();
            marcarAbaAtiva();
            desenharDia();
        }

        function trocarVista(nova) {
            if (nova === vista) { return; }
            vista = nova;
            gravarPreferencia(CHAVE_VISTA, nova);
            aplicarModo();
            if (!emLista()) {
                calendario.updateSize();
                var n = agora();
                if (temHoje && diaAtual === hojeIdx) {
                    calendario.scrollToTime(deMinutos(Math.max(Math.floor(minutoDoDia(n)) - 60, 0)) + ':00');
                }
            }
        }

        /* A barra de dias gruda logo abaixo do cabeçalho do portal (que também é fixo). */
        function medirTopo() {
            var topo = document.getElementById('rc-topo');
            var altura = topo ? Math.round(topo.getBoundingClientRect().height) : 0;
            app.style.setProperty('--rt-topo', altura + 'px');
        }

        function desenharCabecaDoDia() {
            var titulo = gancho(app, 'dia-titulo');
            var resumo = gancho(app, 'dia-resumo');
            if (!titulo || !resumo) { return; }
            var doDia = atividadesDoDia(diaAtual);
            var ehHoje = temHoje && diaAtual === hojeIdx;
            titulo.textContent = (ehHoje ? 'Hoje, ' + nomeDoDia(diaAtual).toLowerCase() : nomeDoDia(diaAtual))
                + (cfg.mostrarDatas ? ', ' + dataPorExtenso(diaAtual) : '');
            var partes = [];
            if (doDia.length) {
                var minutos = doDia.reduce(function (soma, a) { return soma + paraMinutos(a.fim) - paraMinutos(a.inicio); }, 0);
                partes.push(plural(doDia.length, 'atividade', 'atividades'));
                partes.push(doDia[0].inicio + '–' + doDia[doDia.length - 1].fim);
                partes.push(duracao(minutos));
                if (ehHoje) {
                    var minuto = minutoDoDia(agora());
                    var feitas = doDia.filter(function (a) { return paraMinutos(a.fim) <= minuto; }).length;
                    partes.push(plural(feitas, 'concluída', 'concluídas'));
                }
            }
            resumo.textContent = partes.length ? partes.join(' · ') : 'Nenhuma atividade';
        }

        function selo(classe, iconeNome, texto) {
            var el = criar('span', 'rt-li-selo ' + classe);
            if (iconeNome) { el.appendChild(icone(iconeNome)); }
            el.appendChild(document.createTextNode(texto));
            return el;
        }

        var animacaoLista = null;
        var chaveDaLista = '';
        /* `soSeMudou`: o relógio (a cada 30 s) só redesenha quando algo visível mudou —
           outro minuto, outra atividade "agora" ou "próxima". Redesenhar à toa atrapalha o toque. */
        function desenharLista(direcao, soSeMudou) {
            var lista = gancho(app, 'lista');
            if (!lista || !emLista()) { return; }
            var doDia = atividadesDoDia(diaAtual);
            var ehHoje = temHoje && diaAtual === hojeIdx;
            var situacao = situacaoDeHoje();
            var minuto = situacao.minuto;
            if (destacada.id && Date.now() > destacada.ate) { destacada = { id: null, ate: 0 }; }
            var chave = [diaAtual, doDia.length, ehHoje ? Math.floor(minuto) : '',
                situacao.atual ? situacao.atual.id : '', situacao.proxima ? situacao.proxima.id : '', destacada.id].join('|');
            if (soSeMudou && chave === chaveDaLista) { return; }
            chaveDaLista = chave;
            var focada = document.activeElement && lista.contains(document.activeElement)
                ? document.activeElement.getAttribute('data-rt-item') : null;

            lista.innerHTML = '';
            if (!doDia.length) {
                var vazio = criar('li', 'rt-li-vazio');
                vazio.appendChild(icone('fa-calendar-day'));
                vazio.appendChild(criar('span', '', 'Nenhuma atividade neste dia.'));
                if (cfg.podeCriar) {
                    var nova = criar('button', 'rt-btn rt-btn-primario rt-btn-sm');
                    nova.type = 'button';
                    nova.appendChild(icone('fa-plus'));
                    nova.appendChild(document.createTextNode(' Nova atividade'));
                    nova.addEventListener('click', function () { abrirFormulario({ dia: diaAtual, inicio: '09:00', fim: '09:30' }); });
                    vazio.appendChild(nova);
                }
                lista.appendChild(vazio);
            }

            var jaTemLinhaAgora = false;
            doDia.forEach(function (a) {
                var ini = paraMinutos(a.inicio), fim = paraMinutos(a.fim);
                var feita = ehHoje && fim <= minuto;
                var eAgora = ehHoje && situacao.atual === a;
                var eProxima = ehHoje && situacao.proxima === a;

                // Intervalo livre agora: uma linha mostra onde o "agora" está na lista.
                if (eProxima && !situacao.atual && !jaTemLinhaAgora && doDia[0] !== a) {
                    var linha = criar('li', 'rt-li-agora-linha');
                    linha.appendChild(criar('span', '', 'Agora · ' + hhmm(agora()) + ' · livre até ' + a.inicio));
                    lista.appendChild(linha);
                    jaTemLinhaAgora = true;
                }

                var item = criar('li', 'rt-li');
                var botao = criar('button', 'rt-li-botao rt-cat-' + String(a.categoria).toLowerCase()
                    + (feita ? ' rt-li-feita' : '') + (eAgora ? ' rt-li-agora' : '') + (eProxima ? ' rt-li-proxima' : '')
                    + (destacada.id === String(a.id) ? ' rt-li-destaque' : ''));
                botao.type = 'button';
                botao.setAttribute('data-rt-item', a.id);
                botao.setAttribute('aria-label', a.titulo + ', das ' + a.inicio + ' às ' + a.fim + ', ' + a.categoria_nome
                    + (a.bloqueada ? ', horário travado' : '') + (eAgora ? ', acontecendo agora' : '')
                    + (eProxima ? ', é a próxima' : '') + (feita ? ', já passou' : ''));

                var horas = criar('span', 'rt-li-horas');
                horas.appendChild(criar('span', 'rt-li-inicio', a.inicio));
                horas.appendChild(criar('span', 'rt-li-fim', a.fim));
                botao.appendChild(horas);

                var corpo = criar('span', 'rt-li-corpo');
                var topo = criar('span', 'rt-li-topo');
                topo.appendChild(criar('span', 'rt-li-titulo', a.titulo));
                if (a.bloqueada) {
                    var trava = icone('fa-lock rt-li-icone');
                    trava.title = 'Horário travado pela gestão';
                    topo.appendChild(trava);
                } else if (a.criada_pela_pessoa) {
                    var propria = icone('fa-user-pen rt-li-icone');
                    propria.title = pessoa ? 'Criada por você' : 'Criada pela própria pessoa';
                    topo.appendChild(propria);
                }
                corpo.appendChild(topo);

                var meta = criar('span', 'rt-li-meta');
                meta.appendChild(criar('span', 'rt-li-cat', a.categoria_nome));
                meta.appendChild(criar('span', 'rt-li-duracao', duracao(fim - ini)));
                if (eAgora) {
                    meta.appendChild(selo('rt-li-selo-agora', '', 'Agora · termina em ' + duracao(Math.max(1, Math.ceil(fim - minuto)))));
                } else if (eProxima) {
                    meta.appendChild(selo('rt-li-selo-proxima', 'fa-forward', 'Próxima · em ' + duracao(Math.max(1, Math.ceil(ini - minuto)))));
                }
                corpo.appendChild(meta);
                if (eAgora) {
                    var trilho = criar('span', 'rt-li-progresso');
                    var barra = criar('span', 'rt-li-progresso-barra');
                    barra.style.width = Math.min(100, Math.max(0, 100 * (minuto - ini) / (fim - ini))) + '%';
                    trilho.appendChild(barra);
                    corpo.appendChild(trilho);
                }
                botao.appendChild(corpo);
                botao.addEventListener('click', function () { abrirAtividade(a); });
                item.appendChild(botao);
                lista.appendChild(item);
            });

            if (direcao) {
                lista.classList.remove('rt-lista-da-direita', 'rt-lista-da-esquerda');
                void lista.offsetWidth;   // reinicia a animação
                lista.classList.add(direcao > 0 ? 'rt-lista-da-direita' : 'rt-lista-da-esquerda');
                clearTimeout(animacaoLista);
                animacaoLista = setTimeout(function () {
                    lista.classList.remove('rt-lista-da-direita', 'rt-lista-da-esquerda');
                }, 400);
            }
            if (focada) {
                var deVolta = lista.querySelector('[data-rt-item="' + focada + '"]');
                if (deVolta) { try { deVolta.focus({ preventScroll: true }); } catch (e) { deVolta.focus(); } }
            }
        }

        function desenharDia(direcao, soSeMudou) {
            if (!modoDia()) { return; }
            desenharCabecaDoDia();
            desenharLista(direcao, soSeMudou);
        }

        /* Deslizar para o lado troca o dia (só na lista: na grade o toque longo arrasta atividades). */
        function ligarDeslize() {
            var lista = gancho(app, 'lista');
            if (!lista) { return; }
            var inicio = null;
            lista.addEventListener('touchstart', function (e) {
                if (e.touches.length !== 1) { inicio = null; return; }
                inicio = { x: e.touches[0].clientX, y: e.touches[0].clientY, t: Date.now() };
            }, { passive: true });
            lista.addEventListener('touchend', function (e) {
                if (!inicio || !e.changedTouches.length) { return; }
                var dx = e.changedTouches[0].clientX - inicio.x;
                var dy = e.changedTouches[0].clientY - inicio.y;
                var rapido = Date.now() - inicio.t < 800;
                inicio = null;
                if (!rapido || Math.abs(dx) < 60 || Math.abs(dx) < Math.abs(dy) * 1.8) { return; }
                var destino = diaAtual + (dx < 0 ? 1 : -1);
                if (destino >= 0 && destino <= 5) { irParaDia(destino, dx < 0 ? 1 : -1); }
            }, { passive: true });
        }

        /* Teclado nas abas: setas trocam o dia (padrão de tablist). */
        function ligarTecladoDasAbas() {
            var caixa = gancho(app, 'dias');
            if (!caixa) { return; }
            caixa.addEventListener('keydown', function (e) {
                var passo = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
                if (!passo) { return; }
                var destino = diaAtual + passo;
                if (destino < 0 || destino > 5) { return; }
                e.preventDefault();
                irParaDia(destino, passo);
                var aba = caixa.querySelector('[data-dia="' + destino + '"]');
                if (aba) { aba.focus(); }
            });
        }

        /* ---------------------------------------------------------------- */
        /* Painel lateral: legenda, agora e hoje                              */
        /* ---------------------------------------------------------------- */
        function atualizarLegenda() {
            var minutos = {}, total = 0, quantas = 0;
            Object.keys(atividades).forEach(function (id) {
                var a = atividades[id];
                var m = paraMinutos(a.fim) - paraMinutos(a.inicio);
                minutos[a.categoria] = (minutos[a.categoria] || 0) + m;
                total += m;
                quantas += 1;
            });
            var barra = gancho(app, 'distribuicao');
            if (barra) { barra.innerHTML = ''; }
            app.querySelectorAll('.rt-legenda-item').forEach(function (item) {
                var campo = item.querySelector('[data-rt-horas]');
                if (!campo) { return; }
                var m = minutos[campo.getAttribute('data-rt-horas')] || 0;
                campo.textContent = m ? duracao(m) : '—';
                if (barra && m && total) {
                    var fatia = criar('span');
                    fatia.style.width = (100 * m / total) + '%';
                    fatia.style.background = item.style.getPropertyValue('--rt-cor');
                    barra.appendChild(fatia);
                }
            });
            var rotulo = gancho(app, 'total-semana');
            if (rotulo) {
                rotulo.textContent = quantas
                    ? quantas + (quantas === 1 ? ' atividade · ' : ' atividades · ') + duracao(total)
                    : 'Nenhuma atividade';
            }
            contarPorDia();
        }

        function atualizarPainelHoje() {
            if (!pessoa) { return; }
            var cartao = gancho(app, 'agora');
            var lista = gancho(app, 'hoje-lista');
            var conta = gancho(app, 'hoje-conta');
            var situacao = situacaoDeHoje();
            var minutoAgora = situacao.minuto;
            var idx = semana.proxima ? 0 : hojeIdx;
            var doDia = idx >= 0 ? atividadesDoDia(idx) : [];
            var atual = situacao.atual, proxima = situacao.proxima;
            if (!temHoje && semana.proxima) { proxima = doDia[0] || null; }

            if (lista) {
                lista.innerHTML = '';
                if (!doDia.length) {
                    lista.appendChild(criar('li', 'rt-hoje-vazio', 'Nenhuma atividade neste dia.'));
                }
                doDia.forEach(function (a) {
                    var item = criar('li');
                    var botao = criar('button', 'rt-hoje-item');
                    botao.type = 'button';
                    botao.style.setProperty('--rt-cor', a.cor);
                    if (temHoje) {
                        if (paraMinutos(a.fim) <= minutoAgora) { botao.classList.add('rt-hoje-feita'); }
                        else if (a === atual) { botao.classList.add('rt-hoje-agora'); }
                    }
                    botao.appendChild(criar('span', 'rt-hoje-marca'));
                    botao.appendChild(criar('span', 'rt-hoje-hora', a.inicio));
                    botao.appendChild(criar('span', 'rt-hoje-titulo', a.titulo));
                    if (a.bloqueada) { botao.appendChild(icone('fa-lock rt-hoje-trava')); }
                    botao.addEventListener('click', function () { focarAtividade(a.id, true); });
                    item.appendChild(botao);
                    lista.appendChild(item);
                });
            }
            if (conta) { conta.textContent = doDia.length ? String(doDia.length) : ''; }
            marcarAgoraNaGrade();
            if (!cartao) { return; }

            cartao.innerHTML = '';
            var principal = criar('div', 'rt-agora-principal');
            cartao.appendChild(principal);
            var rotulo = criar('p', 'rt-agora-rotulo');
            rotulo.appendChild(criar('span', 'rt-pulso'));
            principal.appendChild(rotulo);
            cartao.classList.toggle('rt-agora-com-atividade', !!atual);
            if (atual) {
                var ini = paraMinutos(atual.inicio), fim = paraMinutos(atual.fim);
                cartao.style.setProperty('--rt-agora-cor', atual.cor);
                rotulo.appendChild(document.createTextNode(' Agora · ' + atual.categoria_nome));
                if (atual.bloqueada) {
                    var trava = icone('fa-lock rt-agora-trava');
                    trava.title = 'Horário travado pela gestão';
                    rotulo.appendChild(trava);
                }
                principal.appendChild(criar('h3', 'rt-agora-titulo', atual.titulo));
                principal.appendChild(criar('p', 'rt-agora-hora', atual.inicio + '–' + atual.fim
                    + ' · termina em ' + duracao(Math.max(1, Math.ceil(fim - minutoAgora)))));
                var trilho = criar('div', 'rt-progresso');
                var barra = criar('div', 'rt-progresso-barra');
                barra.style.width = Math.min(100, Math.max(0, 100 * (minutoAgora - ini) / (fim - ini))) + '%';
                trilho.appendChild(barra);
                principal.appendChild(trilho);
                cartao.style.cursor = 'pointer';
                cartao.onclick = function (e) { if (!e.target.closest('a')) { focarAtividade(atual.id, true); } };
            } else {
                cartao.style.removeProperty('--rt-agora-cor');
                cartao.style.cursor = '';
                cartao.onclick = null;
                rotulo.appendChild(document.createTextNode(semana.proxima ? ' Domingo' : ' Agora'));
                var mensagem;
                if (semana.proxima) { mensagem = 'Hoje é domingo. Sua próxima semana começa amanhã e já está aqui.'; }
                else if (!doDia.length) { mensagem = 'Nenhuma atividade na sua rotina de hoje.'; }
                else if (proxima) { mensagem = 'Nenhuma atividade neste momento.'; }
                else { mensagem = 'Rotina de hoje concluída. Bom trabalho!'; }
                principal.appendChild(criar('p', 'rt-agora-vazio', mensagem));
            }
            if (proxima) {
                var linha = criar('div', 'rt-proxima');
                linha.appendChild(icone('fa-forward'));
                var texto = criar('span', 'rt-proxima-texto');
                var cabeca = criar('span', 'rt-proxima-rotulo', semana.proxima ? 'Amanhã' : 'Próxima');
                if (temHoje) {
                    var falta = Math.max(1, Math.ceil(paraMinutos(proxima.inicio) - minutoAgora));
                    cabeca.appendChild(criar('span', 'rt-proxima-em', ' · em ' + duracao(falta)));
                }
                texto.appendChild(cabeca);
                texto.appendChild(criar('strong', '', proxima.inicio + ' ' + proxima.titulo));
                linha.appendChild(texto);
                cartao.appendChild(linha);
            }
        }

        /* ---------------------------------------------------------------- */
        /* Detalhe                                                            */
        /* ---------------------------------------------------------------- */
        var det = {
            card: gancho(modalDet, 'det-card'),
            categoria: gancho(modalDet, 'det-categoria'),
            titulo: gancho(modalDet, 'det-titulo'),
            quando: gancho(modalDet, 'det-quando'),
            rotulo: gancho(modalDet, 'det-rotulo'),
            status: gancho(modalDet, 'det-status'),
            descricao: gancho(modalDet, 'det-descricao'),
            horario: gancho(modalDet, 'det-horario'),
            erro: gancho(modalDet, 'det-erro'),
            editar: gancho(modalDet, 'det-editar'),
            excluir: gancho(modalDet, 'det-excluir')
        };
        var aberta = null;

        function abrirDetalhe(a) {
            if (!modalDet) { return; }
            aberta = a;
            det.card.style.setProperty('--rt-cor', a.cor);
            det.categoria.textContent = a.categoria_nome;
            det.titulo.textContent = a.titulo;
            var quando = nomeDoDia(a.dia_semana) + (cfg.mostrarDatas ? ', ' + dataCurta(a.dia_semana) : '');
            det.quando.textContent = quando + ' · ' + a.inicio + '–' + a.fim + ' ('
                + duracao(paraMinutos(a.fim) - paraMinutos(a.inicio)) + ')';
            det.rotulo.textContent = a.categoria_rotulo;

            var livre = !a.bloqueada;
            var texto;
            if (a.bloqueada) { texto = pessoa ? 'Horário travado pela gestão.' : 'Travada: a pessoa não consegue mover.'; }
            else if (pessoa) { texto = a.pode_mover ? 'Você pode arrastar esta atividade para outro horário.' : 'Atividade livre.'; }
            else { texto = 'Livre: a pessoa pode mover para outro horário.'; }
            if (pessoa && a.pode_mover && emLista()) {   // na lista não há arrastar: o caminho é o formulário
                texto = a.pode_editar ? 'Você pode editar esta atividade, inclusive o dia e o horário.'
                    : 'Você pode mudar o dia e o horário desta atividade aqui embaixo.';
            }
            if (a.criada_pela_pessoa) { texto += pessoa ? ' Criada por você.' : ' Criada pela própria pessoa.'; }
            det.status.className = 'rt-det-status' + (livre ? ' rt-det-status-livre' : '');
            det.status.innerHTML = '';
            det.status.appendChild(icone(livre ? 'fa-up-down-left-right' : 'fa-lock'));
            det.status.appendChild(document.createTextNode(' ' + texto));

            if (a.descricao) {
                det.descricao.className = 'rt-det-descricao';
                det.descricao.innerHTML = R.linkificar(a.descricao);
            } else {
                det.descricao.className = 'rt-det-descricao rt-det-descricao-vazia';
                det.descricao.textContent = 'Sem descrição.';
            }

            var mudarHorario = pessoa && a.pode_mover && !a.pode_editar;
            det.horario.classList.toggle('hidden', !mudarHorario);
            if (mudarHorario) {
                det.horario.elements.dia_semana.value = String(a.dia_semana);
                det.horario.elements.inicio.value = a.inicio;
                det.horario.elements.fim.value = a.fim;
                esconderErro(det.erro);
            }
            det.editar.classList.toggle('hidden', !a.pode_editar);
            det.excluir.classList.toggle('hidden', !a.pode_excluir);
            R.abrirModal(modalDet);
        }

        if (modalDet) {
            det.editar.addEventListener('click', function () {
                var a = aberta;
                R.fecharModal(modalDet);
                if (a) { setTimeout(function () { abrirFormulario({ atividade: a }); }, 120); }
            });
            det.excluir.addEventListener('click', function () {
                if (aberta) { excluir(aberta, function () { R.fecharModal(modalDet); }); }
            });
            det.horario.addEventListener('submit', function (e) {
                e.preventDefault();
                var a = aberta;
                if (!a) { return; }
                var campos = det.horario.elements;
                var corpo = {
                    dia_semana: +campos.dia_semana.value,
                    inicio: String(campos.inicio.value || '').slice(0, 5),
                    fim: String(campos.fim.value || '').slice(0, 5)
                };
                var problema = horarioValido(corpo.inicio, corpo.fim);
                if (problema) { mostrarErro(det.erro, problema); return; }
                var botao = det.horario.querySelector('button[type=submit]');
                botao.disabled = true;
                R.api(urlDe(cfg.urls.atualizar, a.id), { corpo: corpo, csrf: cfg.csrf }).then(function (resposta) {
                    atualizarAtividade(resposta.atividade);
                    R.fecharModal(modalDet);
                    R.avisar('Horário atualizado.', 'success');
                    focarAtividade(resposta.atividade.id, false);
                }).catch(function (erro) {
                    mostrarErro(det.erro, erro.message);
                }).then(function () {
                    botao.disabled = false;
                });
            });
        }

        /* ---------------------------------------------------------------- */
        /* Formulário (criar / editar)                                        */
        /* ---------------------------------------------------------------- */
        var form = gancho(modalForm, 'form');
        var formTitulo = gancho(modalForm, 'form-titulo');
        var formDia = gancho(modalForm, 'form-dia');
        var formRepetir = gancho(modalForm, 'form-repetir');
        var formErro = gancho(modalForm, 'form-erro');
        var formExcluir = gancho(modalForm, 'form-excluir');
        var formSalvar = gancho(modalForm, 'form-salvar');
        var editando = null;

        function marcarCategoria() {
            form.querySelectorAll('.rt-cat-opcao').forEach(function (opcao) {
                opcao.classList.toggle('rt-marcada', opcao.querySelector('input').checked);
            });
        }

        function abrirFormulario(opcoes) {
            if (!form) { return; }
            var a = opcoes.atividade || null;
            editando = a;
            form.reset();
            esconderErro(formErro);
            formTitulo.textContent = a ? 'Editar atividade' : 'Nova atividade';
            var campos = form.elements;
            campos.titulo.value = a ? a.titulo : '';
            campos.inicio.value = a ? a.inicio : (opcoes.inicio || '09:00');
            campos.fim.value = a ? a.fim : (opcoes.fim || '09:30');
            campos.descricao.value = a ? (a.descricao || '') : '';
            var categoria = a ? a.categoria : 'COMPLEMENTAR';
            form.querySelectorAll('input[name=categoria]').forEach(function (r) { r.checked = r.value === categoria; });
            marcarCategoria();
            if (campos.bloqueada) { campos.bloqueada.checked = a ? !!a.bloqueada : false; }
            formDia.classList.toggle('hidden', !a);
            formRepetir.classList.toggle('hidden', !!a);
            if (a) {
                campos.dia_semana.value = String(a.dia_semana);
            } else {
                var dia = (opcoes.dia !== undefined && opcoes.dia !== null) ? opcoes.dia : diaAtual;
                form.querySelectorAll('input[name=repetir_em]').forEach(function (c) { c.checked = +c.value === dia; });
            }
            formExcluir.classList.toggle('hidden', !(a && a.pode_excluir));
            R.abrirModal(modalForm);
        }

        if (form) {
            form.addEventListener('change', function (e) {
                if (e.target.name === 'categoria') { marcarCategoria(); }
            });

            form.addEventListener('submit', function (e) {
                e.preventDefault();
                var campos = form.elements;
                var marcada = form.querySelector('input[name=categoria]:checked');
                var corpo = {
                    titulo: campos.titulo.value.trim(),
                    inicio: String(campos.inicio.value || '').slice(0, 5),
                    fim: String(campos.fim.value || '').slice(0, 5),
                    descricao: campos.descricao.value,
                    categoria: marcada ? marcada.value : 'COMPLEMENTAR'
                };
                if (!corpo.titulo) { mostrarErro(formErro, 'Informe o título da atividade.'); campos.titulo.focus(); return; }
                var problema = horarioValido(corpo.inicio, corpo.fim);
                if (problema) { mostrarErro(formErro, problema); return; }
                if (campos.bloqueada) { corpo.bloqueada = campos.bloqueada.checked; }

                var url;
                if (editando) {
                    corpo.dia_semana = +campos.dia_semana.value;
                    url = urlDe(cfg.urls.atualizar, editando.id);
                } else {
                    corpo.repetir_em = Array.prototype.map.call(
                        form.querySelectorAll('input[name=repetir_em]:checked'), function (c) { return +c.value; });
                    if (!corpo.repetir_em.length) { mostrarErro(formErro, 'Escolha pelo menos um dia.'); return; }
                    if (cfg.usuario) { corpo.usuario = cfg.usuario; }
                    url = cfg.urls.criar;
                }

                esconderErro(formErro);
                formSalvar.disabled = true;
                R.api(url, { corpo: corpo, csrf: cfg.csrf }).then(function (resposta) {
                    if (editando) {
                        atualizarAtividade(resposta.atividade);
                        R.avisar('Atividade salva.', 'success');
                        focarAtividade(resposta.atividade.id, false);
                    } else {
                        var novas = resposta.atividades || [];
                        novas.forEach(function (a) { atividades[a.id] = a; calendario.addEvent(evento(a)); });
                        depoisDeMudar();
                        R.avisar(novas.length > 1 ? novas.length + ' atividades criadas.' : 'Atividade criada.', 'success');
                        if (novas[0]) { focarAtividade(novas[0].id, false); }
                    }
                    R.fecharModal(modalForm);
                }).catch(function (erro) {
                    mostrarErro(formErro, erro.message);
                }).then(function () {
                    formSalvar.disabled = false;
                });
            });

            formExcluir.addEventListener('click', function () {
                if (editando) { excluir(editando, function () { R.fecharModal(modalForm); }); }
            });
        }

        /* ---------------------------------------------------------------- */
        /* Botões do topo e partida                                           */
        /* ---------------------------------------------------------------- */
        var botaoAgora = gancho(app, 'btn-agora');
        if (botaoAgora) {
            botaoAgora.addEventListener('click', function () {
                if (hojeIdx >= 0) { irParaDia(hojeIdx); }
                if (emLista()) {
                    var alvoLista = app.querySelector('.rt-li-agora, .rt-li-proxima') || app.querySelector('.rt-li-botao');
                    if (alvoLista && alvoLista.scrollIntoView) { alvoLista.scrollIntoView({ behavior: 'smooth', block: 'center' }); }
                    return;
                }
                calendario.scrollToTime(deMinutos(Math.max(Math.floor(minutoDoDia(agora())) - 60, 0)) + ':00');
            });
        }
        var botaoNova = gancho(app, 'btn-nova');
        if (botaoNova) {
            botaoNova.addEventListener('click', function () {
                abrirFormulario({ dia: diaAtual, inicio: '09:00', fim: '09:30' });
            });
        }
        app.querySelectorAll('[data-rt-vista]').forEach(function (botao) {
            botao.addEventListener('click', function () { trocarVista(botao.getAttribute('data-rt-vista')); });
        });
        if (consultaDia) {
            if (consultaDia.addEventListener) { consultaDia.addEventListener('change', aplicarModo); }
            else if (consultaDia.addListener) { consultaDia.addListener(aplicarModo); }
        }
        window.addEventListener('load', aplicarModo);

        calendario.render();
        montarAbasDias();
        ligarDeslize();
        ligarTecladoDasAbas();
        aplicarModo();
        atualizarLegenda();
        atualizarPainelHoje();
        setInterval(function () {
            atualizarPainelHoje();
            if (!document.hidden) { desenharDia(0, true); }
        }, 30000);

        if (destaque.atividade && atividades[destaque.atividade]) {
            setTimeout(function () {
                focarAtividade(destaque.atividade, true);
                if (window.history && window.history.replaceState) {
                    window.history.replaceState(null, '', window.location.pathname);
                }
            }, 300);
        }

        return {
            calendario: calendario,
            atividades: atividades,
            focar: focarAtividade,
            irParaDia: irParaDia,
            vista: function (nova) { if (nova) { trocarVista(nova); } return vista; },
            recarregar: function () {
                return R.api(cfg.urls.listar).then(function (novos) {
                    Object.keys(atividades).forEach(function (id) { delete atividades[id]; });
                    (novos.atividades || []).forEach(function (a) { atividades[a.id] = a; });
                    calendario.getEvents().forEach(function (ev) { ev.remove(); });
                    Object.keys(atividades).forEach(function (id) { calendario.addEvent(evento(atividades[id])); });
                    depoisDeMudar();
                });
            }
        };
    }

    function iniciar() {
        var cfg = lerJson('rt-config');
        var dados = lerJson('rt-dados');
        var app = document.getElementById('rt-app');
        var alvo = document.getElementById('rt-calendario');
        if (!cfg || !dados || !app || !alvo || !window.Rotina) { return null; }
        if (typeof window.FullCalendar === 'undefined') {
            alvo.appendChild(criar('p', 'rt-agora-vazio',
                'Não foi possível carregar o calendário. Confira a internet e atualize a página.'));
            return null;
        }
        return montar(cfg, dados, app, alvo);
    }

    window.RotinaCalendario = { iniciar: iniciar, instancia: null };

    function comecar() {
        if (document.getElementById('rt-config') && !window.RotinaCalendario.instancia) {
            window.RotinaCalendario.instancia = iniciar();
        }
    }
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', comecar);
    } else {
        comecar();
    }
})();
