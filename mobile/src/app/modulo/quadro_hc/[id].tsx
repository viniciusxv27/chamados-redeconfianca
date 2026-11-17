/**
 * Loja do Quadro de HC: previsto × real × diferença (por função), a composição,
 * as pessoas (status, admissão, férias/afastado, marcas) e as vagas em aberto.
 * Chega com os filtros de item da rede (busca, cargo, rápido) e dá para ver todos.
 */
import { useLocalSearchParams } from 'expo-router';
import { useMemo, useState } from 'react';
import { RefreshControl, ScrollView, View } from 'react-native';

import { abrirPessoa, abrirPrevisto } from '@/components/modulos/quadro_hc/navegar';
import { BarraComposicao, CartaoPessoa, CartaoVaga, NumerosHC, SeloHC, TabelaComparacao, corDoGrupo, dataCurta } from '@/components/modulos/quadro_hc/pecas';
import { BarraDetalhe, Botao, BotaoIcone, Cartao, Entrada, EsqueletoLista, EstadoErro, EstadoVazio, Pressionavel, Selo, Tela, Texto } from '@/components/ui';
import { estado, marca } from '@/constants/theme';
import { useLojaHC } from '@/hooks/modulos/quadro_hc';
import { mensagemDoErro } from '@/services/api';
import type { FiltroQuadro, LojaDetalhe, PessoaHC, Rapido } from '@/services/modulos/quadro_hc';
import { abrirNoPortal } from '@/utils/links';

const ROTULO_RAPIDO: Record<Rapido, string> = {
  alerta: 'com alerta',
  vaga: 'vagas',
  novo: 'novos',
  experiencia: 'experiência em 7 dias',
  demissao: 'demissão no cadastro',
  ajustado: 'ajustados',
};

export default function LojaHC() {
  const params = useLocalSearchParams<{ id: string; busca?: string; cargo?: string; rapido?: Rapido }>();
  const id = Number(params.id);
  const filtroRecebido = useMemo<FiltroQuadro>(
    () => ({ busca: params.busca || undefined, cargo: params.cargo || undefined, rapido: params.rapido || '' }),
    [params.busca, params.cargo, params.rapido],
  );
  const temFiltro = Boolean(filtroRecebido.busca || filtroRecebido.cargo || filtroRecebido.rapido);
  const [verTodos, setVerTodos] = useState(false);
  const filtro = verTodos ? {} : filtroRecebido;
  const { data, isPending, isError, error, refetch, isRefetching, isPlaceholderData } = useLojaHC(id, filtro);

  return (
    <Tela>
      <BarraDetalhe titulo={data?.loja.nome ?? 'Loja'} direita={<BotaoIcone icone="open-outline" rotulo="Abrir no portal" onPress={() => abrirNoPortal('/quadro-hc/')} />} />
      {isPending ? (
        <View className="px-5 pt-2">
          <EsqueletoLista quantos={5} />
        </View>
      ) : isError || !data ? (
        <EstadoErro mensagem={mensagemDoErro(error)} aoTentar={() => void refetch()} />
      ) : (
        <ScrollView
          contentContainerStyle={{ padding: 20, paddingTop: 8, gap: 16, paddingBottom: 40 }}
          refreshControl={<RefreshControl refreshing={isRefetching && !isPlaceholderData} onRefresh={() => void refetch()} tintColor={marca[500]} colors={[marca[500]]} />}>
          <Cabecalho dados={data} />
          <PainelHC dados={data} />
          {temFiltro ? (
            <Cartao style={{ padding: 12, flexDirection: 'row', alignItems: 'center', gap: 10 }}>
              <Texto variante="legenda" tom="suave" className="flex-1">
                {verTodos ? 'Mostrando todos da loja.' : `Filtro da rede: ${descreverFiltro(filtroRecebido)}.`}
              </Texto>
              <Pressionavel onPress={() => setVerTodos(!verTodos)} accessibilityRole="button" hitSlop={8}>
                <Texto variante="legenda" tom="marca">
                  {verTodos ? 'Voltar ao filtro' : 'Ver todos'}
                </Texto>
              </Pressionavel>
            </Cartao>
          ) : null}
          <Pessoas dados={data} />
        </ScrollView>
      )}
    </Tela>
  );
}

function descreverFiltro(f: FiltroQuadro) {
  return [f.busca ? `"${f.busca}"` : '', f.cargo ?? '', f.rapido ? ROTULO_RAPIDO[f.rapido] : ''].filter(Boolean).join(' · ');
}

function Cabecalho({ dados }: { dados: LojaDetalhe }) {
  const l = dados.loja;
  return (
    <View className="gap-2">
      <Texto variante="titulo1">{l.nome}</Texto>
      <Texto variante="legenda" tom="suave">
        {[l.carteira, dados.coordenacao ? `Coordenação ${dados.coordenacao}` : '', l.adabas].filter(Boolean).join(' · ')}
      </Texto>
      <View className="flex-row flex-wrap gap-1.5">
        {l.total_previsto ? <SeloHC loja={l} /> : null}
        {l.sem_gerente ? <Selo texto="Sem gerente" cor={estado.perigo} ponto={false} /> : null}
        {l.vagas ? <Selo texto={`${l.vagas} ${l.vagas === 1 ? 'vaga' : 'vagas'} em aberto`} cor={estado.alerta} ponto={false} /> : null}
      </View>
    </View>
  );
}

function PainelHC({ dados }: { dados: LojaDetalhe }) {
  const l = dados.loja;
  return (
    <Cartao style={{ padding: 16, gap: 14 }}>
      <NumerosHC real={l.total} previsto={l.total_previsto} diferenca={l.diferenca} />
      <BarraComposicao resumo={l.resumo} total={l.total} />
      <View className="flex-row flex-wrap gap-x-3 gap-y-1.5">
        {l.resumo.map((g) => (
          <View key={g.grupo} className="flex-row items-center gap-1.5">
            <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: corDoGrupo(g.grupo) }} />
            <Texto variante="legenda" tom="suave">
              {g.n} {g.grupo}
            </Texto>
          </View>
        ))}
      </View>
      {l.comparacao.length ? (
        <TabelaComparacao linhas={l.comparacao} />
      ) : (
        <Texto variante="legenda" tom="suave">
          Esta loja ainda não tem HC previsto (o quadro ideal por função).
        </Texto>
      )}
      {dados.pode_gerenciar ? (
        <Botao titulo={l.total_previsto ? 'Editar HC previsto' : 'Cadastrar HC previsto'} variante="secundario" tamanho="sm" icone="create-outline" onPress={() => abrirPrevisto(l.id)} />
      ) : null}
    </Cartao>
  );
}

function Pessoas({ dados }: { dados: LojaDetalhe }) {
  // Agrupadas por função, na ordem que o servidor já manda (a da planilha do quadro).
  const grupos = useMemo(() => {
    const mapa = new Map<string, { p: PessoaHC; i: number }[]>();
    dados.pessoas.forEach((p, i) => mapa.set(p.grupo, [...(mapa.get(p.grupo) ?? []), { p, i }]));
    return [...mapa.entries()];
  }, [dados.pessoas]);
  const vazio = !dados.pessoas.length && !dados.vagas.length && !dados.vagas_previstas.length;
  if (vazio) {
    return <EstadoVazio icone={dados.filtrando ? 'search-outline' : 'people-outline'} titulo={dados.filtrando ? 'Ninguém neste filtro' : 'Ninguém nesta loja'} />;
  }
  return (
    <View className="gap-5">
      {grupos.map(([grupo, pessoas]) => (
        <View key={grupo} className="gap-2.5">
          <View className="flex-row items-center gap-2">
            <View style={{ width: 10, height: 10, borderRadius: 5, backgroundColor: corDoGrupo(grupo) }} />
            <Texto variante="rotulo" tom="suave">
              {grupo} · {pessoas.length}
            </Texto>
          </View>
          {pessoas.map(({ p, i }) => (
            <Entrada key={p.id} indice={i}>
              <CartaoPessoa pessoa={p} onPress={() => abrirPessoa(p.id)} />
            </Entrada>
          ))}
        </View>
      ))}
      {dados.vagas.length || dados.vagas_previstas.length ? (
        <View className="gap-2.5">
          <Texto variante="rotulo" tom="suave">
            Vagas em aberto · {dados.vagas.length + dados.vagas_previstas.length}
          </Texto>
          {dados.vagas.map((v) => (
            <CartaoVaga
              key={`v${v.id}`}
              titulo={v.cargo}
              detalhe={[v.observacao, `Aberta ${dataCurta(v.aberta_em) ?? ''}${v.aberta_por ? ` por ${v.aberta_por}` : ''}`].filter(Boolean).join(' · ')}
            />
          ))}
          {dados.vagas_previstas.map((v, i) => (
            <CartaoVaga key={`p${i}`} titulo={v.grupo} detalhe={`Pelo HC previsto: ${v.real} de ${v.previsto}`} />
          ))}
        </View>
      ) : null}
    </View>
  );
}
