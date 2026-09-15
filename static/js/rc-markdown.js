/* Markdown leve e seguro — as respostas do Assistente Claude (/assistente/).
 *
 * Todo o texto é escapado ANTES de qualquer transformação: o que o modelo (ou
 * um texto que ele leu, como uma pauta ou uma transcrição) escrever nunca vira
 * tag de verdade. Só esta sintaxe ganha forma; o resto aparece como texto:
 *
 *   títulos (#), **negrito**, *itálico*, ~~riscado~~, `código`, blocos ```,
 *   listas (-, *, 1.) com recuo, citações (>), tabelas (| a | b |), linhas
 *   (---) e links [texto](url) ou soltos — só http(s), mailto e caminhos do
 *   próprio portal (/...).
 *
 *   RCMarkdown.render(texto) -> HTML seguro
 */
(function (global) {
    'use strict';

    function escapar(texto) {
        return String(texto)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function desescapar(texto) {
        return texto.replace(/&quot;/g, '"').replace(/&#39;/g, "'")
            .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
    }

    function urlSegura(escapada) {
        var url = desescapar(escapada).trim();
        if (/^https?:\/\/[^\s]+$/i.test(url) || /^mailto:[^\s]+$/i.test(url) || /^\/(?!\/)[^\s]*$/.test(url)) {
            return url;
        }
        return null;
    }

    function ancora(url, rotulo) {
        var origem = global.location ? global.location.origin : '';
        var externo = /^https?:\/\//i.test(url) && !(origem && url.indexOf(origem + '/') === 0);
        return '<a href="' + escapar(url) + '"' +
            (externo ? ' target="_blank" rel="noopener noreferrer"' : '') + '>' + rotulo + '</a>';
    }

    function enfatizar(texto) {
        return texto
            .replace(/\*\*(?=\S)([\s\S]*?\S)\*\*/g, '<strong>$1</strong>')
            .replace(/__(?=\S)([\s\S]*?\S)__/g, '<strong>$1</strong>')
            .replace(/~~(?=\S)([\s\S]*?\S)~~/g, '<del>$1</del>')
            .replace(/(^|[^\w*])\*(?=[^\s*])([^*\n]*?[^\s*])\*(?![\w*])/g, '$1<em>$2</em>')
            .replace(/(^|[^\w])_(?=[^\s_])([^_\n]*?[^\s_])_(?!\w)/g, '$1<em>$2</em>');
    }

    // Recebe texto JÁ escapado. Código, links e URLs são guardados antes da
    // ênfase: um "_" dentro de uma URL não pode virar itálico.
    function emLinha(texto) {
        var guardados = [];
        function guardar(html) {
            guardados.push(html);
            return '' + (guardados.length - 1) + '';
        }
        texto = texto.replace(/`([^`\n]+)`/g, function (_, codigo) {
            return guardar('<code>' + codigo + '</code>');
        });
        texto = texto.replace(/\[([^\]\n]+)\]\(([^)\s]+)\)/g, function (tudo, rotulo, url) {
            var segura = urlSegura(url);
            return segura ? guardar(ancora(segura, enfatizar(rotulo))) : tudo;
        });
        texto = texto.replace(/(^|[\s(])(https?:\/\/[^\s<]*[^\s<.,;:!?)\]])/g, function (tudo, antes, url) {
            var segura = urlSegura(url);
            return segura ? antes + guardar(ancora(segura, url)) : tudo;
        });
        texto = enfatizar(texto);
        return texto.replace(/(\d+)/g, function (_, i) { return guardados[+i]; });
    }

    var ITEM = /^(\s*)([-*+]|\d{1,3}[.)])\s+(.*)$/;
    var CERCA = /^\s*```/;
    var TITULO = /^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$/;
    var LINHA = /^\s{0,3}([-*_])(\s*\1){2,}\s*$/;
    var CITACAO = /^\s{0,3}&gt;/;
    var SEPARADOR = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

    function celulas(linha) {
        var limpa = linha.trim().replace(/^\|/, '').replace(/\|$/, '');
        return limpa.split(/(?<!\\)\|/).map(function (c) { return c.replace(/\\\|/g, '|').trim(); });
    }

    function ehTabela(linhas, i) {
        return i + 1 < linhas.length && linhas[i].indexOf('|') !== -1 &&
            linhas[i + 1].indexOf('|') !== -1 && SEPARADOR.test(linhas[i + 1]);
    }

    function inicioDeBloco(linhas, i) {
        var linha = linhas[i];
        return CERCA.test(linha) || TITULO.test(linha) || LINHA.test(linha) ||
            CITACAO.test(linha) || ITEM.test(linha) || ehTabela(linhas, i);
    }

    function tabela(linhas, i) {
        var cabecalho = celulas(linhas[i]);
        var alinhamentos = celulas(linhas[i + 1]).map(function (c) {
            if (/^:-+:$/.test(c)) return 'center';
            if (/^-+:$/.test(c)) return 'right';
            return '';
        });
        i += 2;
        var corpo = [];
        while (i < linhas.length && linhas[i].trim() && linhas[i].indexOf('|') !== -1) {
            corpo.push(celulas(linhas[i]));
            i++;
        }
        function celula(tag, conteudo, coluna) {
            var alinhar = alinhamentos[coluna] ? ' style="text-align:' + alinhamentos[coluna] + '"' : '';
            return '<' + tag + alinhar + '>' + emLinha(conteudo || '') + '</' + tag + '>';
        }
        var html = '<div class="rc-md-tabela"><table><thead><tr>' +
            cabecalho.map(function (c, n) { return celula('th', c, n); }).join('') + '</tr></thead><tbody>' +
            corpo.map(function (linha) {
                return '<tr>' + cabecalho.map(function (_, n) { return celula('td', linha[n], n); }).join('') + '</tr>';
            }).join('') + '</tbody></table></div>';
        return {html: html, proximo: i};
    }

    function lista(linhas, i) {
        var base = linhas[i].match(ITEM);
        var recuo = base[1].length;
        var ordenada = /\d/.test(base[2]);
        var itens = [];
        while (i < linhas.length) {
            var m = linhas[i].match(ITEM);
            if (m && m[1].length === recuo && /\d/.test(m[2]) === ordenada) {
                itens.push({texto: [m[3]], filhos: ''});
                i++;
            } else if (m && m[1].length > recuo && itens.length) {
                var sub = lista(linhas, i);
                itens[itens.length - 1].filhos += sub.html;
                i = sub.proximo;
            } else if (!m && itens.length && /^\s+\S/.test(linhas[i])) {
                itens[itens.length - 1].texto.push(linhas[i].trim());     // continuação recuada do item
                i++;
            } else {
                break;
            }
        }
        var tag = ordenada ? 'ol' : 'ul';
        var inicio = ordenada ? parseInt(base[2], 10) : 1;
        // Lista numerada separada por linha em branco continua a contagem.
        var abre = '<' + tag + (ordenada && inicio !== 1 ? ' start="' + inicio + '"' : '') + '>';
        return {
            html: abre + itens.map(function (item) {
                return '<li>' + emLinha(item.texto.join(' ')) + item.filhos + '</li>';
            }).join('') + '</' + tag + '>',
            proximo: i
        };
    }

    function blocos(linhas) {
        var html = [];
        var i = 0;
        while (i < linhas.length) {
            var linha = linhas[i];
            if (!linha.trim()) { i++; continue; }

            if (CERCA.test(linha)) {
                var codigo = [];
                i++;
                while (i < linhas.length && !CERCA.test(linhas[i])) { codigo.push(linhas[i]); i++; }
                i++;
                html.push('<pre><code>' + codigo.join('\n') + '</code></pre>');
                continue;
            }
            var titulo = linha.match(TITULO);
            if (titulo) {
                var nivel = Math.min(titulo[1].length + 2, 6);        // "#" vira h3: a bolha é pequena
                html.push('<h' + nivel + '>' + emLinha(titulo[2]) + '</h' + nivel + '>');
                i++;
                continue;
            }
            if (LINHA.test(linha)) { html.push('<hr>'); i++; continue; }
            if (CITACAO.test(linha)) {
                var citacao = [];
                while (i < linhas.length && CITACAO.test(linhas[i])) {
                    citacao.push(linhas[i].replace(/^\s{0,3}&gt;\s?/, ''));
                    i++;
                }
                html.push('<blockquote>' + blocos(citacao) + '</blockquote>');
                continue;
            }
            if (ehTabela(linhas, i)) {
                var t = tabela(linhas, i);
                html.push(t.html);
                i = t.proximo;
                continue;
            }
            if (ITEM.test(linha)) {
                var l = lista(linhas, i);
                html.push(l.html);
                i = l.proximo;
                continue;
            }
            var paragrafo = [];
            while (i < linhas.length && linhas[i].trim() && (!paragrafo.length || !inicioDeBloco(linhas, i))) {
                paragrafo.push(emLinha(linhas[i].trim()));
                i++;
            }
            html.push('<p>' + paragrafo.join('<br>') + '</p>');
        }
        return html.join('');
    }

    function render(texto) {
        var normalizado = String(texto == null ? '' : texto).replace(/\r\n?/g, '\n');
        return blocos(escapar(normalizado).split('\n'));
    }

    global.RCMarkdown = {render: render};
})(typeof window !== 'undefined' ? window : globalThis);
