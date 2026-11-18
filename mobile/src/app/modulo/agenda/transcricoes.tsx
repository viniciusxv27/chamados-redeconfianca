import { type Href, router } from 'expo-router';
import { useMemo } from 'react';
import { FlatList, RefreshControl, View } from 'react-native';

import { duracao } from '@/components/modulos/agenda/datas';
import { COR_STATUS_TRANSCRICAO } from '@/components/modulos/agenda/pecas';
import { BarraDetalhe, BotaoIcone, CabecalhoGrande, Cartao, Entrada, EsqueletoLista, EstadoErro, EstadoVazio, Icone, Selo, Tela, Texto } from '@/components/ui';
import { marca } from '@/constants/theme';
import { useTranscricoes } from '@/hooks/modulos/agenda';
import { useCores } from '@/providers/tema';
import { mensagemDoErro } from '@/services/api';
import type { TranscricaoResumo } from '@/services/modulos/agenda';
import { comAlfa, tempoRelativo } from '@/utils/formatar';
import { abrirNoPortal } from '@/utils/links';
import { EsqueletoRodape } from '@/components/ui/esqueleto';

/** Atas de reunião que a pessoa pode ver (as dela, as compartilhadas com ela; o SUPERADMIN vê todas). */
export default function Transcricoes() {
  const { data, isPending, isError, error, refetch, isRefetching, fetchNextPage, hasNextPage, isFetchingNextPage } = useTranscricoes();
  const itens = useMemo(() => data?.pages.flatMap((p) => p.resultados) ?? [], [data]);
  const total = data?.pages[0]?.total;

  return (
    <Tela>
      <BarraDetalhe direita={<BotaoIcone icone="open-outline" rotulo="Abrir as transcrições no portal" onPress={() => abrirNoPortal('/agenda/transcricoes/')} />} />
      <FlatList<TranscricaoResumo>
        data={isPending || (isError && !itens.length) ? [] : itens}
        keyExtractor={(t) => String(t.id)}
        ListHeaderComponent={
          <View className="gap-3 pb-3">
            <CabecalhoGrande titulo="Atas de reunião" subtitulo={total != null ? `${total} transcriç${total === 1 ? 'ão' : 'ões'}` : 'Transcrições'} />
            <View className="px-5">
              <Cartao style={{ padding: 14, flexDirection: 'row', gap: 10, alignItems: 'center' }}>
                <Icone nome="mic-outline" tamanho={18} cor={marca[500]} />
                <Texto variante="legenda" tom="suave" className="flex-1">
                  Para gravar ou enviar o áudio de uma reunião, use o portal no computador.
                </Texto>
              </Cartao>
            </View>
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
            <CartaoTranscricao t={item} />
          </Entrada>
        )}
        ListEmptyComponent={!isPending && !isError ? <EstadoVazio icone="mic-off-outline" titulo="Nenhuma ata ainda" texto="As transcrições suas e as compartilhadas com você aparecem aqui." /> : undefined}
        ListFooterComponent={isFetchingNextPage ? <EsqueletoRodape /> : undefined}
        contentContainerStyle={{ paddingBottom: 40, gap: 12 }}
        onEndReachedThreshold={0.4}
        onEndReached={() => hasNextPage && !isFetchingNextPage && fetchNextPage()}
        refreshControl={<RefreshControl refreshing={isRefetching} onRefresh={() => void refetch()} tintColor={marca[500]} colors={[marca[500]]} />}
      />
    </Tela>
  );
}

function CartaoTranscricao({ t }: { t: TranscricaoResumo }) {
  const cores = useCores();
  const cor = COR_STATUS_TRANSCRICAO[t.status] ?? '#6B7280';
  const andamento = t.status === 'processing' ? (t.aguardando_nova_tentativa ? 'Nova tentativa em breve' : t.etapa_rotulo) : null;
  return (
    <Cartao
      onPress={() => router.push({ pathname: '/modulo/agenda/transcricao/[id]', params: { id: String(t.id) } } as unknown as Href)}
      acessibilidade={`${t.titulo}, ${t.status_nome}`}
      style={{ padding: 16, gap: 8 }}>
      <View className="flex-row items-center gap-2">
        <Selo texto={t.status_nome} cor={cor} />
        <Texto variante="legenda" tom="fraco" className="flex-1" numberOfLines={1}>
          {[tempoRelativo(t.criada_em), duracao(t.duracao_segundos), t.sou_dono ? null : t.dono].filter(Boolean).join(' · ')}
        </Texto>
      </View>
      <Texto variante="corpoForte" numberOfLines={2}>
        {t.titulo}
      </Texto>
      {t.status === 'processing' ? (
        <View className="gap-1.5">
          <View style={{ height: 6, borderRadius: 3, backgroundColor: cores.superficie3, overflow: 'hidden' }}>
            <View style={{ width: `${Math.max(3, t.progresso_pct)}%`, height: '100%', backgroundColor: cor }} />
          </View>
          {andamento ? (
            <Texto variante="legenda" tom="fraco">
              {`${andamento} · ${t.progresso_pct}%`}
            </Texto>
          ) : null}
        </View>
      ) : null}
      {t.status === 'error' && t.erro ? (
        <Texto variante="legenda" tom="perigo" numberOfLines={2}>
          {t.erro}
        </Texto>
      ) : null}
      {t.resumo ? (
        <Texto variante="legenda" tom="suave" numberOfLines={3}>
          {t.resumo}
        </Texto>
      ) : null}
      {t.status === 'completed' ? (
        <View className="flex-row flex-wrap gap-2">
          <Contagem icone="flash-outline" texto={`${t.acoes} ações`} />
          <Contagem icone="hammer-outline" texto={`${t.decisoes} decisões`} />
          {t.tarefas ? <Contagem icone="checkbox-outline" texto={`${t.tarefas} tarefas`} /> : null}
        </View>
      ) : null}
    </Cartao>
  );
}

function Contagem({ icone, texto }: { icone: Parameters<typeof Icone>[0]['nome']; texto: string }) {
  const cores = useCores();
  return (
    <View className="flex-row items-center gap-1" style={{ paddingHorizontal: 8, paddingVertical: 3, borderRadius: 999, backgroundColor: comAlfa(cores.texto3, 0.12) }}>
      <Icone nome={icone} tamanho={12} cor={cores.texto2} />
      <Texto variante="legenda" tom="suave" style={{ fontSize: 12 }}>
        {texto}
      </Texto>
    </View>
  );
}
