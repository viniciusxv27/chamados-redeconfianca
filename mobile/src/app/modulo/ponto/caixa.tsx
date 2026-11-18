import { useEffect, useMemo, useState } from 'react';
import { FlatList, RefreshControl, ScrollView, View } from 'react-native';

import { CartaoSolicitacao } from '@/components/modulos/ponto/pecas';
import { BarraDetalhe, CabecalhoGrande, Campo, Chip, Entrada, EsqueletoLista, EstadoErro, EstadoVazio, Tela } from '@/components/ui';
import { marca } from '@/constants/theme';
import { useCaixaDP } from '@/hooks/modulos/ponto';
import { mensagemDoErro } from '@/services/api';
import type { FiltroCaixa, SolicitacaoResumo } from '@/services/modulos/ponto';
import { EsqueletoRodape } from '@/components/ui/esqueleto';

const FILTROS: { valor: FiltroCaixa; rotulo: string; kpi?: 'aguardando' | 'devolvidas' | 'lancar' }[] = [
  { valor: 'pendentes', rotulo: 'Pendentes' },
  { valor: 'lancar', rotulo: 'Ajustes a lançar', kpi: 'lancar' },
  { valor: 'devolvidas', rotulo: 'Devolvidas', kpi: 'devolvidas' },
  { valor: 'aprovadas', rotulo: 'Aprovadas' },
  { valor: 'todas', rotulo: 'Todas' },
];

/** Caixa do DP (só para quem analisa — o servidor responde 403 para os outros). */
export default function CaixaDP() {
  const [filtro, setFiltro] = useState<FiltroCaixa>('pendentes');
  const [texto, setTexto] = useState('');
  const [busca, setBusca] = useState('');
  useEffect(() => {
    const t = setTimeout(() => setBusca(texto.trim()), 350);
    return () => clearTimeout(t);
  }, [texto]);
  const { data, isPending, isError, error, refetch, isRefetching, fetchNextPage, hasNextPage, isFetchingNextPage } = useCaixaDP(filtro, busca);
  const itens = useMemo(() => data?.pages.flatMap((p) => p.resultados) ?? [], [data]);
  const kpis = data?.pages[0]?.kpis;

  return (
    <Tela>
      <BarraDetalhe />
      <FlatList<SolicitacaoResumo>
        data={isPending || (isError && !itens.length) ? [] : itens}
        keyExtractor={(s) => String(s.id)}
        keyboardDismissMode="on-drag"
        ListHeaderComponent={
          <View className="gap-3 pb-3">
            <CabecalhoGrande
              titulo="Caixa do DP"
              subtitulo={kpis ? `${kpis.aguardando} aguardando · ${kpis.em_analise} em análise${kpis.atrasadas ? ` · ${kpis.atrasadas} há mais de 2 dias` : ''}` : 'Solicitações'}
            />
            <View className="px-5">
              <Campo icone="search" placeholder="Nome, e-mail ou #número" value={texto} onChangeText={setTexto} autoCorrect={false} returnKeyType="search" />
            </View>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ paddingHorizontal: 20, gap: 8 }}>
              {FILTROS.map((f) => (
                <Chip key={f.valor} rotulo={f.kpi && kpis?.[f.kpi] ? `${f.rotulo} (${kpis[f.kpi]})` : f.rotulo} ativo={filtro === f.valor} onPress={() => setFiltro(f.valor)} />
              ))}
            </ScrollView>
            {isPending ? (
              <View className="px-5">
                <EsqueletoLista quantos={4} />
              </View>
            ) : isError && !itens.length ? (
              <EstadoErro mensagem={mensagemDoErro(error)} aoTentar={() => void refetch()} />
            ) : null}
          </View>
        }
        renderItem={({ item, index }) => (
          <Entrada indice={index < 10 ? index : 0} style={{ paddingHorizontal: 20 }}>
            <CartaoSolicitacao sol={item} mostrarPessoa />
          </Entrada>
        )}
        contentContainerStyle={{ paddingBottom: 40, gap: 12 }}
        onEndReachedThreshold={0.4}
        onEndReached={() => hasNextPage && !isFetchingNextPage && fetchNextPage()}
        refreshControl={<RefreshControl refreshing={isRefetching && !isFetchingNextPage} onRefresh={() => void refetch()} tintColor={marca[500]} colors={[marca[500]]} />}
        ListFooterComponent={isFetchingNextPage ? <EsqueletoRodape /> : undefined}
        ListEmptyComponent={isPending || isError ? undefined : <EstadoVazio icone="checkmark-done-circle-outline" titulo="Nada aqui" texto={busca ? `Nenhuma solicitação com "${busca}".` : 'Nenhuma solicitação neste filtro.'} />}
      />
    </Tela>
  );
}
