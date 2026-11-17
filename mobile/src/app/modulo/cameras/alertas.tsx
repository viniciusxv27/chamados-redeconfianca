import { useMemo, useState } from 'react';
import { ActivityIndicator, FlatList, RefreshControl, ScrollView, View } from 'react-native';

import { CartaoAlerta } from '@/components/modulos/cameras/pecas';
import { BarraDetalhe, CabecalhoGrande, Chip, Entrada, EsqueletoLista, EstadoErro, EstadoVazio, Tela } from '@/components/ui';
import { Seletor } from '@/components/ui/seletor';
import { marca } from '@/constants/theme';
import { useAlertasCameras } from '@/hooks/modulos/cameras';
import { mensagemDoErro } from '@/services/api';
import type { Opcao } from '@/services/modulos/cameras';

const STATUS_PADRAO: Opcao[] = [
  { valor: 'abertos', rotulo: 'Abertos' },
  { valor: 'NOVO', rotulo: 'Novo' },
  { valor: 'ANALISE', rotulo: 'Em análise' },
  { valor: 'CONFIRMADO', rotulo: 'Furto confirmado' },
  { valor: 'FALSO', rotulo: 'Falso alarme' },
];

/** Os alertas da web (/cameras/alertas/), com os mesmos filtros: situação, tipo e loja. Abertos vêm do mais provável. */
export default function AlertasCameras() {
  const [status, setStatus] = useState('abertos');
  const [tipo, setTipo] = useState('');
  const [loja, setLoja] = useState(0);
  const filtro = useMemo(() => ({ status, tipo: tipo || undefined, loja: loja ? String(loja) : undefined }), [status, tipo, loja]);
  const consulta = useAlertasCameras(filtro);
  const itens = useMemo(() => consulta.data?.pages.flatMap((p) => p.alertas) ?? [], [consulta.data]);
  const primeira = consulta.data?.pages[0];
  const lojas = useMemo(
    () => [{ id: 0, nome: 'Todas as lojas' }, ...(primeira?.opcoes.lojas ?? []).map((o) => ({ id: Number(o.valor), nome: o.rotulo }))],
    [primeira],
  );
  const total = primeira?.total ?? 0;

  return (
    <Tela>
      <BarraDetalhe />
      <CabecalhoGrande titulo="Alertas" subtitulo={consulta.isPending ? ' ' : `${total} ${total === 1 ? 'alerta' : 'alertas'}${primeira ? ` · ${primeira.total_abertos} abertos na rede` : ''}`} />
      <View className="gap-3 pb-3">
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ paddingHorizontal: 20, gap: 8 }}>
          {(primeira?.opcoes.status ?? STATUS_PADRAO).map((o) => (
            <Chip key={o.valor} rotulo={o.rotulo} ativo={status === o.valor} onPress={() => setStatus(o.valor)} />
          ))}
        </ScrollView>
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ paddingHorizontal: 20, gap: 8 }}>
          <Chip rotulo="Todos os tipos" ativo={!tipo} onPress={() => setTipo('')} />
          {(primeira?.por_tipo ?? []).map((o) => (
            <Chip key={o.valor} rotulo={o.abertos ? `${o.rotulo} (${o.abertos})` : o.rotulo} ativo={tipo === o.valor} onPress={() => setTipo(o.valor)} />
          ))}
        </ScrollView>
        <View className="px-5">
          <Seletor rotulo="Loja" placeholder="Todas as lojas" itens={lojas} valor={loja} aoEscolher={setLoja} carregando={consulta.isPending && !primeira} />
        </View>
      </View>
      {consulta.isPending ? (
        <View className="px-5 pt-1">
          <EsqueletoLista quantos={5} />
        </View>
      ) : consulta.isError && !itens.length ? (
        <EstadoErro mensagem={mensagemDoErro(consulta.error)} aoTentar={() => void consulta.refetch()} />
      ) : (
        <FlatList
          data={itens}
          keyExtractor={(a) => String(a.id)}
          renderItem={({ item, index }) => (
            <Entrada indice={index < 10 ? index : 0}>
              <CartaoAlerta alerta={item} />
            </Entrada>
          )}
          contentContainerStyle={{ paddingHorizontal: 20, paddingTop: 4, paddingBottom: 40, gap: 12 }}
          onEndReachedThreshold={0.4}
          onEndReached={() => consulta.hasNextPage && !consulta.isFetchingNextPage && consulta.fetchNextPage()}
          refreshControl={
            <RefreshControl refreshing={consulta.isRefetching && !consulta.isFetchingNextPage} onRefresh={() => void consulta.refetch()} tintColor={marca[500]} colors={[marca[500]]} />
          }
          ListFooterComponent={consulta.isFetchingNextPage ? <ActivityIndicator color={marca[500]} style={{ marginVertical: 16 }} /> : undefined}
          ListEmptyComponent={
            <EstadoVazio
              icone="checkmark-circle-outline"
              titulo={status === 'abertos' ? 'Nenhum alerta aberto' : 'Nenhum alerta'}
              texto="Troque os filtros para ver outros alertas."
            />
          }
        />
      )}
    </Tela>
  );
}
