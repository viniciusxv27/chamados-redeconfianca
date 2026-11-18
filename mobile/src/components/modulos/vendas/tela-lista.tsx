/** Tela de lista paginada do módulo Vendas: voltar, título, filtros fixos e lista com rolagem infinita. */
import { type ReactElement, type ReactNode, useEffect, useState } from 'react';
import { FlatList, RefreshControl, View } from 'react-native';

import { BarraDetalhe, CabecalhoGrande, Entrada, EsqueletoLista, EstadoErro, EstadoVazio, type NomeIcone, Tela } from '@/components/ui';
import { marca } from '@/constants/theme';
import { mensagemDoErro } from '@/services/api';
import { EsqueletoRodape } from '@/components/ui/esqueleto';

/** Texto digitado → busca, depois que a pessoa para de digitar (350 ms). */
export function useBuscaAdiada(inicial = '') {
  const [texto, setTexto] = useState(inicial);
  const [busca, setBusca] = useState(inicial.trim());
  useEffect(() => {
    const t = setTimeout(() => setBusca(texto.trim()), 350);
    return () => clearTimeout(t);
  }, [texto]);
  return { texto, setTexto, busca };
}

type Consulta = {
  isPending: boolean;
  isError: boolean;
  error: unknown;
  isRefetching: boolean;
  isFetchingNextPage: boolean;
  hasNextPage: boolean;
  fetchNextPage: () => unknown;
  refetch: () => unknown;
};

export function TelaLista<T>({
  titulo,
  subtitulo,
  filtros,
  itens,
  consulta,
  chave,
  item,
  vazio,
  direita,
}: {
  titulo: string;
  subtitulo?: string;
  filtros?: ReactNode;
  itens: T[];
  consulta: Consulta;
  chave: (t: T) => string;
  item: (t: T) => ReactElement;
  vazio: { icone: NomeIcone; titulo: string; texto?: string };
  direita?: ReactNode;
}) {
  return (
    <Tela>
      <BarraDetalhe direita={direita} />
      <CabecalhoGrande titulo={titulo} subtitulo={subtitulo} />
      {filtros ? <View className="gap-3 px-5 pb-3">{filtros}</View> : null}
      {consulta.isPending ? (
        <View className="px-5 pt-1">
          <EsqueletoLista quantos={5} />
        </View>
      ) : consulta.isError && !itens.length ? (
        <EstadoErro mensagem={mensagemDoErro(consulta.error)} aoTentar={() => void consulta.refetch()} />
      ) : (
        <FlatList
          data={itens}
          keyExtractor={chave}
          renderItem={({ item: t, index }) => <Entrada indice={index < 10 ? index : 0}>{item(t)}</Entrada>}
          contentContainerStyle={{ paddingHorizontal: 20, paddingTop: 4, paddingBottom: 40, gap: 12 }}
          onEndReachedThreshold={0.4}
          onEndReached={() => consulta.hasNextPage && !consulta.isFetchingNextPage && consulta.fetchNextPage()}
          refreshControl={
            <RefreshControl
              refreshing={consulta.isRefetching && !consulta.isFetchingNextPage}
              onRefresh={() => void consulta.refetch()}
              tintColor={marca[500]}
              colors={[marca[500]]}
            />
          }
          ListFooterComponent={consulta.isFetchingNextPage ? <EsqueletoRodape /> : undefined}
          ListEmptyComponent={<EstadoVazio icone={vazio.icone} titulo={vazio.titulo} texto={vazio.texto} />}
          keyboardDismissMode="on-drag"
          keyboardShouldPersistTaps="handled"
        />
      )}
    </Tela>
  );
}
