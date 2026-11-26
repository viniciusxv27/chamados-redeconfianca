/**
 * Texto com a formatação leve do Impulso (descrição e comentários da meta), o mesmo desenho de
 * impulso/formatacao.py no portal: **negrito**, *itálico* ou _itálico_, ~~riscado~~, `código`,
 * linhas com "- " (lista), "1. " (numerada), "# " (título), "> " (citação), [texto](https://…)
 * e endereços soltos viram link. Texto antigo, sem marcação, aparece igual.
 */
import { Fragment, type ReactNode } from 'react';
import { Platform, Text, View } from 'react-native';

import { Texto } from '@/components/ui/texto';
import { fontes, marca } from '@/constants/theme';
import { useCores } from '@/providers/tema';
import { abrirNoPortal } from '@/utils/links';

const ITEM = /^\s*[-*•]\s+(.*)$/;
const NUMERO = /^\s*(\d{1,3})[.)]\s+(.*)$/;
const TITULO = /^(#{1,3})\s+(.*)$/;
const CITACAO = /^>\s?(.*)$/;
// Na ordem do portal: código, link, endereço solto, negrito, riscado, itálico (* ou _).
const INLINE = new RegExp(
  [
    '`([^`\\n]{1,500})`',
    '\\[([^\\]\\n]{1,300})\\]\\((https?:\\/\\/[^\\s)]{1,1000})\\)',
    '(?<![="\\wÀ-ÿ/])(https?:\\/\\/[^\\s<]{3,1000})',
    '\\*\\*(?=\\S)(.+?)(?<=\\S)\\*\\*',
    '~~(?=\\S)(.+?)(?<=\\S)~~',
    '(?<![\\wÀ-ÿ*])\\*(?=\\S)([^*\\n]+?)(?<=\\S)\\*(?![\\wÀ-ÿ*])',
    '(?<![\\wÀ-ÿ_])_(?=\\S)([^_\\n]+?)(?<=\\S)_(?![\\wÀ-ÿ_])',
  ].join('|'),
  'g',
);
const MONO = Platform.OS === 'ios' ? 'Menlo' : 'monospace';

type Bloco =
  | { tipo: 'p'; linhas: string[] }
  | { tipo: 'lista'; numerada: boolean; itens: { n?: string; texto: string }[] }
  | { tipo: 'titulo'; nivel: number; texto: string }
  | { tipo: 'citacao'; texto: string };

function blocos(texto: string): Bloco[] {
  const saida: Bloco[] = [];
  let paragrafo: string[] = [];
  let lista: Extract<Bloco, { tipo: 'lista' }> | null = null;
  const fecharParagrafo = () => {
    if (paragrafo.length) saida.push({ tipo: 'p', linhas: paragrafo });
    paragrafo = [];
  };
  const fecharLista = () => {
    if (lista?.itens.length) saida.push(lista);
    lista = null;
  };
  for (const linha of texto.replace(/\r\n?/g, '\n').split('\n')) {
    const item = linha.match(ITEM);
    const numero = item ? null : linha.match(NUMERO);
    if (item || numero) {
      fecharParagrafo();
      const numerada = !item;
      if (lista && lista.numerada !== numerada) fecharLista();
      if (!lista) lista = { tipo: 'lista', numerada, itens: [] };
      lista.itens.push(item ? { texto: item[1] } : { n: numero![1], texto: numero![2] });
      continue;
    }
    fecharLista();
    const titulo = linha.match(TITULO);
    const citacao = linha.match(CITACAO);
    if (!linha.trim()) fecharParagrafo();
    else if (titulo) {
      fecharParagrafo();
      saida.push({ tipo: 'titulo', nivel: titulo[1].length, texto: titulo[2] });
    } else if (citacao) {
      fecharParagrafo();
      saida.push({ tipo: 'citacao', texto: citacao[1] });
    } else paragrafo.push(linha);
  }
  fecharParagrafo();
  fecharLista();
  return saida;
}

function emLinha(texto: string, corLink: string, sublinharLink: boolean, chave = 'i'): ReactNode[] {
  const partes: ReactNode[] = [];
  let ultimo = 0;
  let i = 0;
  for (const m of texto.matchAll(INLINE)) {
    const inicio = m.index ?? 0;
    if (inicio > ultimo) partes.push(texto.slice(ultimo, inicio));
    const k = `${chave}-${i++}`;
    const [, codigo, linkTexto, linkUrl, urlSolta, negrito, riscado, italico1, italico2] = m;
    let fim = inicio + m[0].length;
    if (codigo !== undefined) {
      partes.push(
        <Text key={k} style={{ fontFamily: MONO, fontSize: 13, backgroundColor: 'rgba(127,127,127,0.18)' }}>
          {codigo}
        </Text>,
      );
    } else if (linkUrl !== undefined || urlSolta !== undefined) {
      let url = linkUrl ?? urlSolta!;
      let rotulo = linkTexto ?? url;
      if (urlSolta !== undefined) {
        // pontuação no fim fica fora do link (como no portal)
        while (url && '.,;:!?)'.includes(url[url.length - 1])) url = url.slice(0, -1);
        rotulo = url;
        fim = inicio + url.length;
      }
      partes.push(
        <Text
          key={k}
          accessibilityRole="link"
          onPress={() => void abrirNoPortal(url)}
          style={{ color: corLink, fontFamily: fontes.seminegrito, textDecorationLine: sublinharLink ? 'underline' : 'none' }}>
          {rotulo}
        </Text>,
      );
    } else if (negrito !== undefined) {
      partes.push(
        <Text key={k} style={{ fontFamily: fontes.negrito }}>
          {emLinha(negrito, corLink, sublinharLink, k)}
        </Text>,
      );
    } else if (riscado !== undefined) {
      partes.push(
        <Text key={k} style={{ textDecorationLine: 'line-through' }}>
          {emLinha(riscado, corLink, sublinharLink, k)}
        </Text>,
      );
    } else {
      partes.push(
        <Text key={k} style={{ fontStyle: 'italic' }}>
          {italico1 ?? italico2}
        </Text>,
      );
    }
    ultimo = fim;
  }
  if (ultimo < texto.length) partes.push(texto.slice(ultimo));
  return partes;
}

export function TextoFormatado({ texto, cor, selectable }: { texto: string; cor?: string; selectable?: boolean }) {
  const cores = useCores();
  const corTexto = cor ?? cores.texto;
  // Em balão colorido (cor própria) o link fica da mesma cor, sublinhado.
  const corLink = cor ?? marca[600];
  const sublinhar = Boolean(cor);
  const lista = blocos(texto);
  if (!lista.length) return null;
  return (
    <View style={{ gap: 6 }}>
      {lista.map((b, i) => {
        if (b.tipo === 'p') {
          return (
            <Texto key={i} selectable={selectable} style={{ color: corTexto }}>
              {b.linhas.map((l, j) => (
                <Fragment key={j}>
                  {j ? '\n' : null}
                  {emLinha(l, corLink, sublinhar, `${i}.${j}`)}
                </Fragment>
              ))}
            </Texto>
          );
        }
        if (b.tipo === 'titulo') {
          const tamanho = b.nivel === 1 ? 18 : b.nivel === 2 ? 16 : 15;
          return (
            <Texto key={i} selectable={selectable} style={{ color: corTexto, fontFamily: b.nivel === 3 ? fontes.seminegrito : fontes.negrito, fontSize: tamanho, lineHeight: tamanho + 6 }}>
              {emLinha(b.texto, corLink, sublinhar, `${i}`)}
            </Texto>
          );
        }
        if (b.tipo === 'citacao') {
          return (
            <View key={i} style={{ borderLeftWidth: 3, borderLeftColor: cor ?? marca[500], paddingLeft: 10 }}>
              <Texto selectable={selectable} style={{ color: cor ?? cores.texto2, fontStyle: 'italic' }}>
                {emLinha(b.texto, corLink, sublinhar, `${i}`)}
              </Texto>
            </View>
          );
        }
        return (
          <View key={i} style={{ gap: 3 }}>
            {b.itens.map((it, j) => (
              <View key={j} className="flex-row gap-2">
                <Texto style={{ color: cor ?? marca[600], fontFamily: fontes.negrito, minWidth: b.numerada ? 18 : 10 }}>
                  {b.numerada ? `${it.n}.` : '•'}
                </Texto>
                <Texto selectable={selectable} className="flex-1" style={{ color: corTexto }}>
                  {emLinha(it.texto, corLink, sublinhar, `${i}.${j}`)}
                </Texto>
              </View>
            ))}
          </View>
        );
      })}
    </View>
  );
}

