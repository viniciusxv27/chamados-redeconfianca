/**
 * Peças comuns das telas de módulo (Impulso, Ponto, Contracheque…).
 * Cada módulo tem a sua pasta em src/app/modulo/<chave>/ e usa estas peças para
 * todos terem o mesmo jeito: barra com voltar, título grande, lista com puxar
 * para atualizar, estados de carregando/erro/vazio.
 */
import * as WebBrowser from 'expo-web-browser';
import type { ReactNode } from 'react';
import { RefreshControl, ScrollView, View } from 'react-native';

import { BarraDetalhe, CabecalhoGrande, Cartao, Esqueleto, EsqueletoDetalhe, EsqueletoLista, EsqueletoPainel, EstadoErro, GrupoEsqueleto, Icone, type NomeIcone, Tela, Texto } from '@/components/ui';
import { marca } from '@/constants/theme';
import { mensagemDoErro } from '@/services/api';
import { comAlfa } from '@/utils/formatar';

/** Tela de módulo com rolagem, título grande e puxar-para-atualizar. */
export function TelaModulo({
  titulo,
  subtitulo,
  direita,
  carregando,
  erro,
  aoAtualizar,
  atualizando = false,
  esqueleto = 'lista',
  children,
}: {
  titulo: string;
  subtitulo?: string;
  direita?: ReactNode;
  carregando?: boolean;
  erro?: unknown;
  aoAtualizar?: () => void;
  atualizando?: boolean;
  /** Forma do carregamento: lista de cartões, painel de números ou detalhe. */
  esqueleto?: 'lista' | 'painel' | 'detalhe';
  children?: ReactNode;
}) {
  const carregandoEl = esqueleto === 'painel' ? <EsqueletoPainel /> : esqueleto === 'detalhe' ? <EsqueletoDetalhe /> : <EsqueletoLista quantos={4} />;
  return (
    <Tela>
      <BarraDetalhe direita={direita} />
      <ScrollView
        contentContainerStyle={{ paddingBottom: 56 }}
        refreshControl={aoAtualizar ? <RefreshControl refreshing={atualizando} onRefresh={aoAtualizar} tintColor={marca[500]} colors={[marca[500]]} /> : undefined}>
        <CabecalhoGrande titulo={titulo} subtitulo={subtitulo} />
        <View style={{ gap: 20, paddingHorizontal: 20 }}>
          {carregando ? carregandoEl : erro ? <EstadoErro mensagem={mensagemDoErro(erro)} aoTentar={aoAtualizar} /> : children}
        </View>
      </ScrollView>
    </Tela>
  );
}

/** Número grande com rótulo (resumo no topo das telas de módulo). */
export function Indicador({ rotulo, valor, detalhe, icone, cor = marca[500] }: { rotulo: string; valor: string; detalhe?: string; icone?: NomeIcone; cor?: string }) {
  return (
    <Cartao style={{ flexBasis: '47%', flexGrow: 1, padding: 18, gap: 10 }}>
      {icone ? (
        <View style={{ width: 34, height: 34, borderRadius: 10, backgroundColor: comAlfa(cor, 0.13), alignItems: 'center', justifyContent: 'center' }}>
          <Icone nome={icone} tamanho={18} cor={cor} />
        </View>
      ) : null}
      <Texto variante="titulo2" numberOfLines={1} adjustsFontSizeToFit>
        {valor}
      </Texto>
      <Texto variante="legenda" tom="suave" numberOfLines={2}>
        {rotulo}
      </Texto>
      {detalhe ? (
        <Texto variante="legenda" tom="fraco" numberOfLines={2}>
          {detalhe}
        </Texto>
      ) : null}
    </Cartao>
  );
}

/** Linha rótulo → valor dentro de um cartão. */
export function LinhaValor({ rotulo, valor, destaque, cor }: { rotulo: string; valor: string; destaque?: boolean; cor?: string }) {
  return (
    <View className="flex-row items-center justify-between gap-3" style={{ paddingVertical: 10 }}>
      <Texto variante="legenda" tom="suave" className="flex-1">
        {rotulo}
      </Texto>
      <Texto variante={destaque ? 'corpoForte' : 'legenda'} style={cor ? { color: cor } : undefined}>
        {valor}
      </Texto>
    </View>
  );
}

/** Esqueleto de bloco (enquanto um cartão específico carrega). */
export function EsqueletoBloco({ linhas = 3 }: { linhas?: number }) {
  return (
    <GrupoEsqueleto>
      <Cartao style={{ padding: 16, gap: 10 }}>
        {Array.from({ length: linhas }, (_, i) => (
          <Esqueleto key={i} largura={`${90 - i * 12}%`} />
        ))}
      </Cartao>
    </GrupoEsqueleto>
  );
}

/** Abre arquivo protegido (link assinado da API: vale 5 min) no navegador do app. */
export function abrirArquivo(url: string) {
  return WebBrowser.openBrowserAsync(url, { presentationStyle: WebBrowser.WebBrowserPresentationStyle.PAGE_SHEET });
}

export { mensagemDoErro };
