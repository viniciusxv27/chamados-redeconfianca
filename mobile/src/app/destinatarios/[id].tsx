/**
 * Status de cada destinatário de um comunicado — o painel do detalhe na web, que só o SUPERADMIN e
 * quem enviou veem: contagens (ciente, com dúvida, abriu sem marcar, não abriu), filtro por
 * situação, busca por nome/setor/cargo e a lista em páginas. A planilha (CSV) abre no portal.
 */
import { useLocalSearchParams } from 'expo-router';
import { useEffect, useMemo, useState } from 'react';
import { FlatList, RefreshControl, View } from 'react-native';

import { Avatar, BarraDetalhe, BotaoIcone, Campo, Cartao, Chip, EsqueletoLista, EstadoErro, EstadoVazio, Pressionavel, Selo, Tela, Texto } from '@/components/ui';
import { EsqueletoRodape } from '@/components/ui/esqueleto';
import { estado, fontes, marca, raios } from '@/constants/theme';
import { useDestinatarios } from '@/hooks/consultas';
import { useCores } from '@/providers/tema';
import { mensagemDoErro } from '@/services/api';
import type { SituacaoDestinatario } from '@/services/tipos';
import { comAlfa, dataHora } from '@/utils/formatar';
import { abrirNoPortal } from '@/utils/links';

const COR: Record<SituacaoDestinatario, string> = {
  ESTOU_CIENTE: estado.sucesso,
  ESTOU_COM_DUVIDA: estado.alerta,
  ABRIU: estado.info,
  NAO_ABRIU: '#6B7280',
};

export default function Destinatarios() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const numero = Number(id);
  const cores = useCores();
  const [situacao, setSituacao] = useState<'' | SituacaoDestinatario>('');
  const [digitado, setDigitado] = useState('');
  const [busca, setBusca] = useState('');
  useEffect(() => {
    const t = setTimeout(() => setBusca(digitado.trim()), 400);
    return () => clearTimeout(t);
  }, [digitado]);

  const { data, isPending, isError, error, refetch, isRefetching, fetchNextPage, hasNextPage, isFetchingNextPage } = useDestinatarios(numero, situacao, busca);
  const itens = useMemo(() => data?.pages.flatMap((p) => p.resultados) ?? [], [data]);
  const primeira = data?.pages[0];
  const c = primeira?.contagem;
  const pct = (n: number) => (c?.total ? Math.round((n / c.total) * 100) : 0);

  const cabecalho = (
    <View style={{ gap: 14, paddingBottom: 4 }}>
      {c ? (
        <>
          <View className="flex-row flex-wrap gap-2">
            <Contagem rotulo="Cientes" valor={c.ciente} pct={pct(c.ciente)} cor={COR.ESTOU_CIENTE} ativo={situacao === 'ESTOU_CIENTE'} aoTocar={() => setSituacao(situacao === 'ESTOU_CIENTE' ? '' : 'ESTOU_CIENTE')} />
            <Contagem rotulo="Com dúvida" valor={c.duvida} pct={pct(c.duvida)} cor={COR.ESTOU_COM_DUVIDA} ativo={situacao === 'ESTOU_COM_DUVIDA'} aoTocar={() => setSituacao(situacao === 'ESTOU_COM_DUVIDA' ? '' : 'ESTOU_COM_DUVIDA')} />
            <Contagem rotulo="Só abriu" valor={c.abriu} pct={pct(c.abriu)} cor={COR.ABRIU} ativo={situacao === 'ABRIU'} aoTocar={() => setSituacao(situacao === 'ABRIU' ? '' : 'ABRIU')} />
            <Contagem rotulo="Não abriu" valor={c.nao_abriu} pct={pct(c.nao_abriu)} cor={COR.NAO_ABRIU} ativo={situacao === 'NAO_ABRIU'} aoTocar={() => setSituacao(situacao === 'NAO_ABRIU' ? '' : 'NAO_ABRIU')} />
          </View>
          {/* Barra empilhada: quanto da lista está em cada situação. */}
          {c.total ? (
            <View style={{ flexDirection: 'row', height: 8, borderRadius: 4, overflow: 'hidden', backgroundColor: cores.superficie3 }}>
              {(['ESTOU_CIENTE', 'ESTOU_COM_DUVIDA', 'ABRIU', 'NAO_ABRIU'] as const).map((k) => {
                const n = { ESTOU_CIENTE: c.ciente, ESTOU_COM_DUVIDA: c.duvida, ABRIU: c.abriu, NAO_ABRIU: c.nao_abriu }[k];
                return n ? <View key={k} style={{ flex: n, backgroundColor: COR[k] }} /> : null;
              })}
            </View>
          ) : null}
        </>
      ) : null}
      <View className="flex-row flex-wrap gap-2">
        {(primeira?.situacoes ?? [{ valor: '' as const, rotulo: 'Todos' }]).map((s) => (
          <Chip key={s.valor || 'todos'} rotulo={s.rotulo} ativo={situacao === s.valor} onPress={() => setSituacao(s.valor)} />
        ))}
      </View>
      <Campo icone="search" placeholder="Buscar nome, setor ou cargo…" value={digitado} onChangeText={setDigitado} autoCorrect={false} returnKeyType="search" />
      {primeira ? (
        <Texto variante="legenda" tom="fraco">
          {primeira.total === 1 ? '1 pessoa' : `${primeira.total} pessoas`}
          {c && primeira.total !== c.total ? ` de ${c.total}` : ''}
        </Texto>
      ) : null}
    </View>
  );

  return (
    <Tela>
      <BarraDetalhe
        titulo="Status dos destinatários"
        direita={primeira ? <BotaoIcone icone="download-outline" rotulo="Baixar a planilha no portal" onPress={() => abrirNoPortal(primeira.url_csv)} /> : undefined}
      />
      {isPending ? (
        <View style={{ paddingHorizontal: 20 }}>
          <EsqueletoLista quantos={5} />
        </View>
      ) : isError && !itens.length ? (
        <EstadoErro mensagem={mensagemDoErro(error)} aoTentar={refetch} />
      ) : (
        <FlatList
          data={itens}
          keyExtractor={(x) => String(x.pessoa.id)}
          ListHeaderComponent={cabecalho}
          keyboardShouldPersistTaps="handled"
          renderItem={({ item }) => (
            <Cartao style={{ padding: 14, flexDirection: 'row', alignItems: 'center', gap: 12 }}>
              <Avatar nome={item.pessoa.nome} foto={item.pessoa.foto} tamanho={40} />
              <View className="flex-1 gap-0.5">
                <Texto variante="corpoForte" numberOfLines={1}>
                  {item.pessoa.nome}
                </Texto>
                <Texto variante="legenda" tom="fraco" numberOfLines={1}>
                  {[item.pessoa.cargo, item.pessoa.setor].filter(Boolean).join(' · ') || '—'}
                </Texto>
                {item.respondido_em || item.aberto_em ? (
                  <Texto variante="legenda" tom="fraco" numberOfLines={1} style={{ fontSize: 12 }}>
                    {item.respondido_em ? `Marcou em ${dataHora(item.respondido_em)}` : `Abriu em ${dataHora(item.aberto_em)}`}
                  </Texto>
                ) : null}
              </View>
              <Selo texto={item.situacao_nome} cor={COR[item.situacao]} />
            </Cartao>
          )}
          contentContainerStyle={{ paddingHorizontal: 20, paddingTop: 8, paddingBottom: 40, gap: 10 }}
          onEndReachedThreshold={0.5}
          onEndReached={() => hasNextPage && !isFetchingNextPage && fetchNextPage()}
          refreshControl={<RefreshControl refreshing={isRefetching && !isFetchingNextPage} onRefresh={() => void refetch()} tintColor={marca[500]} colors={[marca[500]]} />}
          ListFooterComponent={isFetchingNextPage ? <EsqueletoRodape /> : undefined}
          ListEmptyComponent={<EstadoVazio icone="people-outline" titulo="Ninguém aqui" texto={busca || situacao ? 'Tente outro filtro ou outra busca.' : 'O comunicado não tem destinatários.'} />}
        />
      )}
    </Tela>
  );
}

function Contagem({ rotulo, valor, pct, cor, ativo, aoTocar }: { rotulo: string; valor: number; pct: number; cor: string; ativo: boolean; aoTocar: () => void }) {
  const cores = useCores();
  return (
    <Pressionavel
      onPress={aoTocar}
      vibrar
      escala={0.96}
      accessibilityRole="button"
      accessibilityState={{ selected: ativo }}
      accessibilityLabel={`${valor} ${rotulo}`}
      style={{
        flexBasis: '47%',
        flexGrow: 1,
        padding: 14,
        gap: 2,
        borderRadius: raios.lg,
        borderWidth: 1.5,
        borderColor: ativo ? cor : cores.bordaSuave,
        backgroundColor: ativo ? comAlfa(cor, 0.1) : cores.superficie,
      }}>
      <Texto style={{ fontFamily: fontes.negrito, fontSize: 22, lineHeight: 28, color: cor }}>{String(valor)}</Texto>
      <Texto variante="legenda" tom="suave" numberOfLines={1}>
        {rotulo} · {pct}%
      </Texto>
    </Pressionavel>
  );
}
