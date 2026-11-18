import { useState } from 'react';
import { ScrollView, View } from 'react-native';
import Animated, { FadeIn, LinearTransition } from 'react-native-reanimated';

import { abrirArquivo, Indicador, TelaModulo } from '@/components/modulos/comum';
import { COR_SITUACAO, dataCurta } from '@/components/modulos/cursos/pecas';
import { BotaoIcone, Campo, Cartao, Chip, Entrada, EstadoVazio, Icone, Pressionavel, Selo, Texto } from '@/components/ui';
import { estado, fontes, marca, raios } from '@/constants/theme';
import { useQuadroCursos } from '@/hooks/modulos/cursos';
import { useCores } from '@/providers/tema';
import { mensagemDoErro } from '@/services/api';
import type { LojaQuadro, PessoaQuadro, SituacaoCurso } from '@/services/modulos/cursos';
import { comAlfa, dataHora } from '@/utils/formatar';
import { abrirNoPortal } from '@/utils/links';

type Filtro = '' | SituacaoCurso;

const FILTROS: { valor: Filtro; rotulo: string }[] = [
  { valor: '', rotulo: 'Todos' },
  { valor: 'CONFERIR', rotulo: 'Em conferência' },
  { valor: 'NAO_FEZ', rotulo: 'Não fez' },
  { valor: 'RECUSADO', rotulo: 'Recusados' },
  { valor: 'APROVADO', rotulo: 'Aprovados' },
];

const SEM_ACENTO = /[̀-ͯ]/g;
const normal = (s: string) => s.normalize('NFD').replace(SEM_ACENTO, '').toLowerCase();

/** Quadro do gestor (cursos/views.py:gestao), só leitura: aprovar e cobrar ficam no portal. */
export default function QuadroCursos() {
  const [curso, setCurso] = useState(0);
  const [filtro, setFiltro] = useState<Filtro>('');
  const [busca, setBusca] = useState('');
  const [abertas, setAbertas] = useState<Record<string, boolean>>({});
  const consulta = useQuadroCursos(curso);
  const { data } = consulta;
  const [abrindo, setAbrindo] = useState<number | null>(null);
  const [erroAbrir, setErroAbrir] = useState<string | null>(null);

  const termo = normal(busca.trim());
  const lojas = (data?.lojas ?? [])
    .map((l) => {
      const daLoja = termo && normal(l.nome).includes(termo);
      const pessoas = l.pessoas.filter((p) => (!filtro || p.situacao === filtro) && (!termo || daLoja || normal(p.nome).includes(termo)));
      return { ...l, visiveis: pessoas };
    })
    .filter((l) => l.visiveis.length > 0);
  const filtrando = !!filtro || !!termo;

  async function abrirComprovante(pessoa: PessoaQuadro) {
    setErroAbrir(null);
    setAbrindo(pessoa.id);
    try {
      // O link assinado vale 5 min: busca o quadro de novo antes de abrir.
      const { data: novo } = await consulta.refetch();
      const url = novo?.lojas.flatMap((l) => l.pessoas).find((p) => p.id === pessoa.id)?.envio?.url;
      if (!url) setErroAbrir('O arquivo não está disponível. Tente pelo portal.');
      else await abrirArquivo(url);
    } catch (e) {
      setErroAbrir(mensagemDoErro(e));
    } finally {
      setAbrindo(null);
    }
  }

  const t = data?.totais;
  return (
    <TelaModulo
      titulo="Quadro das lojas"
      subtitulo={data?.curso ? `${data.curso.titulo} · prazo ${dataCurta(data.curso.prazo)}` : 'Quem fez e quem não fez'}
      carregando={consulta.isPending}
      erro={consulta.error}
      aoAtualizar={consulta.refetch}
      atualizando={consulta.isRefetching}
      esqueleto="painel"
      direita={data ? <BotaoIcone icone="open-outline" rotulo="Abrir no portal" onPress={() => abrirNoPortal(data.caminho_web)} /> : undefined}>
      {data && t ? (
        <>
          {data.cursos.length > 1 ? (
            <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ marginHorizontal: -20 }} contentContainerStyle={{ paddingHorizontal: 20, gap: 8 }}>
              {data.cursos.map((c) => (
                <Chip key={c.id} rotulo={c.titulo.length > 28 ? `${c.titulo.slice(0, 27)}…` : c.titulo} ativo={data.curso?.id === c.id} onPress={() => setCurso(c.id)} />
              ))}
            </ScrollView>
          ) : null}

          {data.curso ? (
            <>
              <View className="flex-row flex-wrap gap-4">
                <Indicador rotulo="Entregaram" valor={`${data.percentual}%`} detalhe={`${t.entregues} de ${t.pessoas}`} icone="pie-chart-outline" cor={marca[500]} />
                <Indicador rotulo="Em conferência" valor={String(t.conferir)} icone="time-outline" cor={estado.info} />
                <Indicador rotulo="Não fizeram" valor={String(t.nao_fez)} icone="hourglass-outline" cor={estado.alerta} />
                <Indicador rotulo="Recusados" valor={String(t.recusados)} icone="arrow-undo-outline" cor={estado.perigo} />
              </View>

              <Cartao style={{ padding: 18, gap: 4 }}>
                <View className="flex-row items-center gap-2">
                  <Icone nome="information-circle-outline" tamanho={18} cor={marca[500]} />
                  <Texto variante="corpoForte" className="flex-1">
                    Aprovar e cobrar no portal
                  </Texto>
                </View>
                <Texto variante="legenda" tom="suave">
                  Aqui é só consulta. Para aprovar comprovantes, publicar curso ou cobrar as lojas, use o portal.
                </Texto>
              </Cartao>

              <Campo icone="search" placeholder="Buscar pessoa ou loja" value={busca} onChangeText={setBusca} autoCorrect={false} />

              <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ marginHorizontal: -20 }} contentContainerStyle={{ paddingHorizontal: 20, gap: 8 }}>
                {FILTROS.map((f) => (
                  <Chip key={f.valor || 'todos'} rotulo={f.rotulo} ativo={filtro === f.valor} onPress={() => setFiltro(f.valor)} />
                ))}
              </ScrollView>

              {erroAbrir ? (
                <Texto variante="legenda" tom="perigo">
                  {erroAbrir}
                </Texto>
              ) : null}

              {lojas.length === 0 ? (
                <EstadoVazio icone="people-outline" titulo={filtrando ? 'Ninguém neste filtro' : 'Ninguém cobrado'} texto={filtrando ? 'Troque o filtro ou a busca.' : 'Este curso ainda não tem pessoas cobradas.'} />
              ) : (
                lojas.map((l, i) => (
                  <Entrada key={l.nome} indice={i}>
                    <CartaoLoja
                      loja={l}
                      pessoas={l.visiveis}
                      aberta={filtrando || (abertas[l.nome] ?? l.conferir > 0)}
                      aoAlternar={() => setAbertas((a) => ({ ...a, [l.nome]: !(a[l.nome] ?? l.conferir > 0) }))}
                      abrindo={abrindo}
                      aoAbrirComprovante={(p) => void abrirComprovante(p)}
                    />
                  </Entrada>
                ))
              )}
            </>
          ) : (
            <EstadoVazio icone="school-outline" titulo="Nenhum curso publicado" texto="Quando um curso for publicado no portal, o quadro aparece aqui." />
          )}
        </>
      ) : null}
    </TelaModulo>
  );
}

function CartaoLoja({
  loja: l,
  pessoas,
  aberta,
  aoAlternar,
  abrindo,
  aoAbrirComprovante,
}: {
  loja: LojaQuadro;
  pessoas: PessoaQuadro[];
  aberta: boolean;
  aoAlternar: () => void;
  abrindo: number | null;
  aoAbrirComprovante: (p: PessoaQuadro) => void;
}) {
  const cores = useCores();
  const cor = l.percentual >= 100 ? estado.sucesso : l.percentual >= 50 ? estado.alerta : estado.perigo;
  return (
    <Cartao style={{ overflow: 'hidden' }}>
      <Pressionavel onPress={aoAlternar} escala={0.99} accessibilityLabel={`${l.nome}: ${l.feitos} de ${l.total}`} accessibilityState={{ expanded: aberta }} style={{ padding: 18, gap: 10 }}>
        <View className="flex-row items-center gap-3">
          <View className="flex-1">
            <Texto variante="corpoForte" numberOfLines={1}>
              {l.geral ? 'Sem loja (Geral)' : l.nome}
            </Texto>
            <Texto variante="legenda" tom="suave">
              {l.feitos} de {l.total} entregaram{l.faltam ? ` · faltam ${l.faltam}` : ''}
            </Texto>
          </View>
          {l.conferir > 0 ? <Selo texto={`${l.conferir} p/ conferir`} cor={estado.info} /> : null}
          <Icone nome={aberta ? 'chevron-up' : 'chevron-down'} tamanho={18} cor={cores.texto3} />
        </View>
        <View style={{ height: 6, borderRadius: 3, backgroundColor: comAlfa(cor, 0.15), overflow: 'hidden' }}>
          <View style={{ width: `${Math.min(100, l.percentual)}%`, height: 6, borderRadius: 3, backgroundColor: cor }} />
        </View>
      </Pressionavel>
      {aberta ? (
        <Animated.View entering={FadeIn.duration(200)} layout={LinearTransition} style={{ borderTopWidth: 1, borderTopColor: cores.borda }}>
          {pessoas.map((p, i) => (
            <LinhaPessoa key={p.id} pessoa={p} primeira={i === 0} abrindo={abrindo === p.id} aoAbrir={() => aoAbrirComprovante(p)} />
          ))}
        </Animated.View>
      ) : null}
    </Cartao>
  );
}

function LinhaPessoa({ pessoa: p, primeira, abrindo, aoAbrir }: { pessoa: PessoaQuadro; primeira: boolean; abrindo: boolean; aoAbrir: () => void }) {
  const cores = useCores();
  const cor = COR_SITUACAO[p.situacao];
  const conteudo = (
    <>
      <View style={{ width: 4, alignSelf: 'stretch', borderRadius: 2, backgroundColor: cor }} />
      <View className="flex-1 gap-0.5">
        <Texto variante="legenda" numberOfLines={1} style={{ fontFamily: fontes.seminegrito, color: cores.texto }}>
          {p.nome}
        </Texto>
        <Texto variante="legenda" tom="fraco" style={{ fontSize: 11 }}>
          {p.envio ? `Enviado em ${dataHora(p.envio.enviado_em)}` : 'Sem comprovante'}
        </Texto>
      </View>
      <View className="items-end gap-1">
        <Selo texto={p.situacao === 'CONFERIR' ? 'Conferir' : p.situacao_nome} cor={cor} ponto={false} />
        {p.envio?.url ? (
          <Texto variante="legenda" tom="marca" style={{ fontSize: 11 }}>
            {abrindo ? 'Abrindo…' : 'Ver comprovante'}
          </Texto>
        ) : null}
      </View>
    </>
  );
  const estilo = { flexDirection: 'row' as const, alignItems: 'center' as const, gap: 12, paddingHorizontal: 18, paddingVertical: 12, borderTopWidth: primeira ? 0 : 1, borderTopColor: cores.bordaSuave, minHeight: 56, borderRadius: raios.sm };
  if (p.envio?.url) {
    return (
      <Pressionavel onPress={aoAbrir} disabled={abrindo} escala={0.99} accessibilityLabel={`Ver comprovante de ${p.nome}`} style={estilo}>
        {conteudo}
      </Pressionavel>
    );
  }
  return <View style={estilo}>{conteudo}</View>;
}
