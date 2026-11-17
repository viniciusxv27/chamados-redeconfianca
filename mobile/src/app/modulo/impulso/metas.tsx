import { useEffect, useMemo, useState } from 'react';
import { ActivityIndicator, FlatList, RefreshControl, ScrollView, View } from 'react-native';

import { CartaoConvite } from '@/components/modulos/impulso/cartao-convite';
import { CartaoMeta } from '@/components/modulos/impulso/cartao-meta';
import { COR_STATUS_META, ROTULO_STATUS } from '@/components/modulos/impulso/comum';
import { BarraDetalhe, BotaoIcone, Botao, CabecalhoGrande, Campo, Chip, Entrada, EsqueletoLista, EstadoErro, EstadoVazio, Tela, Texto } from '@/components/ui';
import { Seletor } from '@/components/ui/seletor';
import { marca } from '@/constants/theme';
import { useKanban, useMaisConcluidas } from '@/hooks/modulos/impulso';
import { mensagemDoErro } from '@/services/api';
import type { CardMeta, Coluna, FiltroKanban, StatusMeta } from '@/services/modulos/impulso';
import { abrirNoPortal } from '@/utils/links';

type Linha = { tipo: 'titulo'; chave: string; titulo: string; n: number } | { tipo: 'meta'; meta: CardMeta };

const ORDEM: StatusMeta[] = ['A_FAZER', 'EM_ANDAMENTO', 'ENTREGUE', 'CONCLUIDA'];

function mesAtual() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
}

/** Kanban do Confiar no celular: uma coluna por vez, os mesmos filtros do portal. */
export default function MetasImpulso() {
  const [status, setStatus] = useState<StatusMeta>('A_FAZER');
  const [mes, setMes] = useState(mesAtual());
  const [texto, setTexto] = useState('');
  const [busca, setBusca] = useState('');
  const [colaborador, setColaborador] = useState<number | null>(null);
  const [criadasPorMim, setCriadasPorMim] = useState(false);
  const [mostrarMais, setMostrarMais] = useState(false);

  useEffect(() => {
    const t = setTimeout(() => setBusca(texto.trim()), 350);
    return () => clearTimeout(t);
  }, [texto]);

  const filtro = useMemo<FiltroKanban>(() => ({ mes, q: busca || undefined, colaborador, criadasPorMim }), [mes, busca, colaborador, criadasPorMim]);
  const { data, isPending, isError, error, refetch, isRefetching, isPlaceholderData } = useKanban(filtro);

  // Filtro novo recomeça o "Mostrar mais" (ajuste durante o render, sem efeito).
  const [filtroDoMais, setFiltroDoMais] = useState(filtro);
  if (filtroDoMais !== filtro) {
    setFiltroDoMais(filtro);
    setMostrarMais(false);
  }

  const coluna = data?.colunas.find((c) => c.status === status);
  const inicioMais = coluna?.status === 'CONCLUIDA' ? coluna.metas.length : 0;
  const mais = useMaisConcluidas(filtro, inicioMais, mostrarMais && status === 'CONCLUIDA' && !!coluna?.restantes);
  const extras = useMemo(() => mais.data?.pages.flatMap((p) => p.metas) ?? [], [mais.data]);
  const restantes = mais.data ? (mais.data.pages[mais.data.pages.length - 1]?.restantes ?? 0) : (coluna?.restantes ?? 0);

  const linhas = useMemo(() => montarLinhas(coluna, extras), [coluna, extras]);
  const gestor = !!data?.is_gestor;

  const cabecalho = (
    <View className="gap-3 pb-3">
      <CabecalhoGrande titulo="Metas" subtitulo={gestor ? 'Minhas e da equipe' : 'Confiar'} />
      <View className="gap-3 px-5">
        <Campo icone="search" placeholder="Buscar pessoa (dono ou gestor)" value={texto} onChangeText={setTexto} returnKeyType="search" autoCorrect={false} clearButtonMode="while-editing" />
        {gestor && data?.filtros.colaboradores ? (
          <Seletor
            rotulo="Pessoa"
            placeholder="Toda a equipe"
            itens={[{ id: 0, nome: 'Toda a equipe' }, ...data.filtros.colaboradores]}
            valor={colaborador ?? 0}
            aoEscolher={(id) => setColaborador(id || null)}
          />
        ) : null}
      </View>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ paddingHorizontal: 20, gap: 8 }}>
        <Chip rotulo="Criadas por mim" ativo={criadasPorMim} onPress={() => setCriadasPorMim(!criadasPorMim)} />
        {(data?.filtros.meses ?? [{ valor: mes, rotulo: 'Este mês' }]).slice(0, 6).map((m) => (
          <Chip key={m.valor} rotulo={m.valor === mesAtual() ? 'Este mês' : m.rotulo.replace(' de ', '/')} ativo={mes === m.valor} onPress={() => setMes(m.valor)} />
        ))}
        <Chip rotulo="Todos os meses" ativo={mes === ''} onPress={() => setMes('')} />
      </ScrollView>
      {data?.convites.length ? (
        <View className="gap-3 px-5">
          {data.convites.map((c) => (
            <CartaoConvite key={c.id} convite={c} />
          ))}
        </View>
      ) : null}
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ paddingHorizontal: 20, gap: 8 }}>
        {ORDEM.map((s) => {
          const c = data?.colunas.find((x) => x.status === s);
          return <AbaStatus key={s} status={s} total={c?.total} ativo={status === s} onPress={() => setStatus(s)} />;
        })}
      </ScrollView>
    </View>
  );

  return (
    <Tela>
      <BarraDetalhe direita={<BotaoIcone icone="open-outline" rotulo="Abrir o Kanban no portal" onPress={() => abrirNoPortal('/impulso/metas/')} />} />
      {isPending ? (
        <View>
          {cabecalho}
          <View className="px-5">
            <EsqueletoLista quantos={4} />
          </View>
        </View>
      ) : isError && !data ? (
        <EstadoErro mensagem={mensagemDoErro(error)} aoTentar={() => void refetch()} />
      ) : (
        <FlatList<Linha>
          data={linhas}
          keyExtractor={(l) => (l.tipo === 'titulo' ? `t-${l.chave}` : `m-${l.meta.id}`)}
          ListHeaderComponent={cabecalho}
          renderItem={({ item, index }) =>
            item.tipo === 'titulo' ? (
              <View className="flex-row items-center justify-between px-5 pb-1 pt-2">
                <Texto variante="rotulo" tom="suave">
                  {item.titulo}
                </Texto>
                <Texto variante="legenda" tom="fraco">
                  {item.n}
                </Texto>
              </View>
            ) : (
              <Entrada indice={index < 10 ? index : 0} style={{ paddingHorizontal: 20 }}>
                <CartaoMeta meta={item.meta} mostrarDono={gestor || item.meta.em_parceria} />
              </Entrada>
            )
          }
          contentContainerStyle={{ paddingBottom: 48, gap: 12 }}
          style={{ opacity: isPlaceholderData ? 0.6 : 1 }}
          refreshControl={<RefreshControl refreshing={isRefetching && !isPlaceholderData} onRefresh={() => void refetch()} tintColor={marca[500]} colors={[marca[500]]} />}
          keyboardDismissMode="on-drag"
          ListEmptyComponent={
            <EstadoVazio
              icone={busca ? 'search-outline' : 'checkmark-done-circle-outline'}
              titulo={busca ? 'Ninguém com esse nome' : `Nada em "${ROTULO_STATUS[status]}"`}
              texto={mes ? 'Neste mês não há metas nesta coluna. Troque o mês para ver outras.' : 'Quando houver metas nesta coluna, elas aparecem aqui.'}
            />
          }
          ListFooterComponent={
            status === 'CONCLUIDA' && restantes > 0 ? (
              <View className="px-5 pt-2">
                {mais.isFetching ? (
                  <ActivityIndicator color={marca[500]} style={{ marginVertical: 12 }} />
                ) : (
                  <Botao
                    titulo={`Mostrar mais (${restantes})`}
                    variante="secundario"
                    icone="chevron-down"
                    onPress={() => (mostrarMais ? void mais.fetchNextPage() : setMostrarMais(true))}
                  />
                )}
              </View>
            ) : undefined
          }
        />
      )}
    </Tela>
  );
}

/** "A fazer" vem repartido por vencimento (atrasadas, hoje, amanhã…), como no portal. */
function montarLinhas(coluna: Coluna | undefined, extras: CardMeta[]): Linha[] {
  if (!coluna) return [];
  const metas = [...coluna.metas, ...extras];
  if (!coluna.grupos?.length) return metas.map((meta) => ({ tipo: 'meta', meta }));
  const porId = new Map(metas.map((m) => [m.id, m]));
  const linhas: Linha[] = [];
  for (const g of coluna.grupos) {
    linhas.push({ tipo: 'titulo', chave: g.chave, titulo: g.titulo, n: g.ids.length });
    for (const id of g.ids) {
      const meta = porId.get(id);
      if (meta) linhas.push({ tipo: 'meta', meta });
    }
  }
  return linhas;
}

function AbaStatus({ status, total, ativo, onPress }: { status: StatusMeta; total?: number; ativo: boolean; onPress: () => void }) {
  const cor = COR_STATUS_META[status];
  return (
    <View>
      <Chip rotulo={`${ROTULO_STATUS[status]}${total != null ? `  ${total}` : ''}`} ativo={ativo} onPress={onPress} />
      {ativo ? <View style={{ height: 3, borderRadius: 2, marginTop: 4, marginHorizontal: 14, backgroundColor: cor }} /> : null}
      {!ativo ? <View style={{ height: 3, marginTop: 4 }} /> : null}
    </View>
  );
}
