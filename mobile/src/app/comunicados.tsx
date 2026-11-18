import { useMemo } from 'react';
import { FlatList, RefreshControl, View } from 'react-native';

import { CartaoComunicado } from '@/components/comunicados/cartao-comunicado';
import { BarraDetalhe, CabecalhoGrande, Entrada, EsqueletoLista, EstadoErro, EstadoVazio, Tela } from '@/components/ui';
import { marca } from '@/constants/theme';
import { useComunicados } from '@/hooks/consultas';
import { mensagemDoErro } from '@/services/api';
import { EsqueletoRodape } from '@/components/ui/esqueleto';

/** Mural: os comunicados que chegam para a pessoa (os mesmos da home do portal). */
export default function Comunicados() {
  const { data, isPending, isError, error, refetch, isRefetching, fetchNextPage, hasNextPage, isFetchingNextPage } = useComunicados();
  const itens = useMemo(() => data?.pages.flatMap((p) => p.resultados) ?? [], [data]);

  return (
    <Tela>
      <BarraDetalhe />
      <CabecalhoGrande titulo="Mural" subtitulo="Comunicados" />
      {isPending ? (
        <View style={{ paddingHorizontal: 20 }}>
          <EsqueletoLista quantos={3} />
        </View>
      ) : isError && !itens.length ? (
        <EstadoErro mensagem={mensagemDoErro(error)} aoTentar={refetch} />
      ) : (
        <FlatList
          data={itens}
          keyExtractor={(c) => String(c.id)}
          renderItem={({ item, index }) => (
            <Entrada indice={index < 8 ? index : 0}>
              <CartaoComunicado comunicado={item} />
            </Entrada>
          )}
          contentContainerStyle={{ paddingHorizontal: 20, paddingTop: 8, paddingBottom: 40, gap: 18 }}
          onEndReachedThreshold={0.5}
          onEndReached={() => hasNextPage && !isFetchingNextPage && fetchNextPage()}
          refreshControl={<RefreshControl refreshing={isRefetching && !isFetchingNextPage} onRefresh={() => void refetch()} tintColor={marca[500]} colors={[marca[500]]} />}
          ListFooterComponent={isFetchingNextPage ? <EsqueletoRodape /> : undefined}
          ListEmptyComponent={<EstadoVazio icone="megaphone-outline" titulo="Nenhum comunicado" texto="Os comunicados da rede aparecem aqui." />}
        />
      )}
    </Tela>
  );
}
