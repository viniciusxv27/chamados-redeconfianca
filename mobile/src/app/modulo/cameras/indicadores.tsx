import { useMemo, useState } from 'react';
import { ScrollView, View } from 'react-native';

import { diaCurto, diaIso, Secao } from '@/components/modulos/cameras/pecas';
import { Indicador, LinhaValor, TelaModulo } from '@/components/modulos/comum';
import { BotaoIcone, Cartao, Chip, Entrada, EstadoVazio, Texto } from '@/components/ui';
import { Seletor } from '@/components/ui/seletor';
import { estado } from '@/constants/theme';
import { useIndicadoresCameras } from '@/hooks/modulos/cameras';
import { useCores } from '@/providers/tema';
import type { Indicadores, LinhaIndicador } from '@/services/modulos/cameras';
import { abrirNoPortal } from '@/utils/links';

type Atalho = 'hoje' | 'ontem' | '7' | '30';

const ATALHOS: { valor: Atalho; rotulo: string; de: number; ate: number }[] = [
  { valor: 'hoje', rotulo: 'Hoje', de: 0, ate: 0 },
  { valor: 'ontem', rotulo: 'Ontem', de: -1, ate: -1 },
  { valor: '7', rotulo: '7 dias', de: -6, ate: 0 },
  { valor: '30', rotulo: '30 dias', de: -29, ate: 0 },
];

const COR_ENTRADAS = '#3B82F6';
const COR_ATENDIMENTOS = '#F39C12';

const MATINAL: Record<string, { rotulo: string; cor: string }> = {
  sim: { rotulo: 'Sim', cor: estado.sucesso },
  nao: { rotulo: 'Não', cor: estado.perigo },
  sem_equipe: { rotulo: 'Sem equipe', cor: '#6B7280' },
  sem_medicao: { rotulo: '—', cor: '#6B7280' },
};

function variacao(v: number | null | undefined) {
  return v == null ? undefined : `${v >= 0 ? '+' : ''}${v}% vs período anterior`;
}

/** Os números da aba Indicadores da web: entradas pela porta, matinal e tempo na mesa — do dia ou do período. */
export default function IndicadoresCameras() {
  const [atalho, setAtalho] = useState<Atalho>('hoje');
  const [loja, setLoja] = useState(0);
  const periodo = ATALHOS.find((a) => a.valor === atalho) ?? ATALHOS[0];
  const filtro = useMemo(() => ({ de: diaIso(periodo.de), ate: diaIso(periodo.ate), loja: loja || null }), [periodo, loja]);
  const { data, isPending, error, refetch, isRefetching } = useIndicadoresCameras(filtro);
  const lojas = useMemo(() => [{ id: 0, nome: 'Todas as lojas' }, ...(data?.lojas ?? []).map((o) => ({ id: Number(o.valor), nome: o.rotulo }))], [data]);

  return (
    <TelaModulo
      titulo="Indicadores"
      subtitulo={data ? (data.um_dia ? diaCurto(data.de) : `${diaCurto(data.de)} a ${diaCurto(data.ate)}`) : 'Medidos pela câmera'}
      carregando={isPending}
      erro={error}
      aoAtualizar={refetch}
      atualizando={isRefetching}
      direita={data ? <BotaoIcone icone="open-outline" rotulo="Abrir no portal" onPress={() => abrirNoPortal(data.caminho_web)} /> : undefined}>
      <View className="flex-row flex-wrap gap-2">
        {ATALHOS.map((a) => (
          <Chip key={a.valor} rotulo={a.rotulo} ativo={atalho === a.valor} onPress={() => setAtalho(a.valor)} />
        ))}
      </View>
      <Seletor rotulo="Loja" placeholder="Todas as lojas" itens={lojas} valor={loja} aoEscolher={setLoja} carregando={!data} />
      {data ? <Conteudo dados={data} /> : null}
    </TelaModulo>
  );
}

function Conteudo({ dados: d }: { dados: Indicadores }) {
  return (
    <>
      <View className="flex-row flex-wrap gap-3">
        <Indicador
          rotulo="Entradas pela porta"
          valor={d.total_entradas == null ? '—' : String(d.total_entradas)}
          detalhe={[d.total_clientes != null ? `≈ ${d.total_clientes} clientes (sem a equipe)` : null, variacao(d.anterior?.entradas)].filter(Boolean).join(' · ') || undefined}
          icone="enter-outline"
          cor={COR_ENTRADAS}
        />
        <Indicador
          rotulo="Matinal"
          valor={d.matinal_medidos ? `${d.matinal_dias}/${d.matinal_medidos}` : '—'}
          detalhe={d.matinal_medidas ? `${d.matinal_com} de ${d.matinal_medidas} lojas fizeram` : 'dias com matinal / dias medidos'}
          icone="people-outline"
          cor={estado.sucesso}
        />
        <Indicador
          rotulo="Atendimento na mesa (mediana)"
          valor={d.mediana_geral}
          detalhe={d.total_atendimentos ? `${d.total_atendimentos} atendimentos · média ${d.media_geral}` : 'sem atendimento medido'}
          icone="time-outline"
          cor={COR_ATENDIMENTOS}
        />
        <Indicador
          rotulo="Lojas com medição"
          valor={`${d.lojas_com_medicao}/${d.total_lojas}`}
          detalhe={d.lojas_com_medicao ? `câmeras analisando em ${d.cobertura_media}% do horário` : undefined}
          icone="videocam-outline"
          cor="#6366F1"
        />
      </View>

      {d.por_hora.length ? <PorHora dados={d} /> : null}

      {d.linhas.length === 0 ? (
        <EstadoVazio icone="storefront-outline" titulo="Nenhuma loja cadastrada" />
      ) : (
        d.linhas.map((l, i) => (
          <Entrada key={l.loja.id} indice={i}>
            <CartaoLoja linha={l} umDia={d.um_dia} maximo={d.atendimento_maximo_min} />
          </Entrada>
        ))
      )}

      <Texto variante="legenda" tom="fraco">
        “Sem medição” é diferente de zero: a câmera daquela área não estava analisando. Clientes desconta as entradas da equipe no ponto e continua sendo estimativa.
      </Texto>
    </>
  );
}

function PorHora({ dados: d }: { dados: Indicadores }) {
  return (
    <Secao icone="time-outline" titulo="Ao longo do dia (soma do período)">
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 6, alignItems: 'flex-end' }}>
        {d.por_hora.map((h) => (
          <View key={h.hora} style={{ alignItems: 'center', gap: 4 }} accessibilityLabel={`${h.hora}h: ${h.entradas} entradas, ${h.atendimentos} atendimentos`}>
            <View style={{ height: 90, flexDirection: 'row', alignItems: 'flex-end', gap: 2 }}>
              <View style={{ width: 8, height: Math.max(2, h.pe * 0.9), borderRadius: 3, backgroundColor: COR_ENTRADAS }} />
              <View style={{ width: 8, height: Math.max(2, h.pa * 0.9), borderRadius: 3, backgroundColor: COR_ATENDIMENTOS }} />
            </View>
            <Texto variante="legenda" tom="fraco" style={{ fontSize: 11 }}>
              {h.hora}h
            </Texto>
          </View>
        ))}
      </ScrollView>
      <View className="flex-row gap-4">
        <Legenda cor={COR_ENTRADAS} texto="Entradas" />
        <Legenda cor={COR_ATENDIMENTOS} texto="Atendimentos na mesa" />
      </View>
    </Secao>
  );
}

function Legenda({ cor, texto }: { cor: string; texto: string }) {
  return (
    <View className="flex-row items-center gap-1.5">
      <View style={{ width: 10, height: 10, borderRadius: 3, backgroundColor: cor }} />
      <Texto variante="legenda" tom="suave">
        {texto}
      </Texto>
    </View>
  );
}

function CartaoLoja({ linha: l, umDia, maximo }: { linha: LinhaIndicador; umDia: boolean; maximo: number }) {
  const matinal = l.matinal_aviso
    ? l.matinal_aviso
    : umDia
      ? l.matinal_dias
        ? `Sim${l.matinais[0]?.inicio ? ` · ${l.matinais[0].inicio}, ${l.matinais[0].duracao}, ${l.matinais[0].pessoas} pessoas` : ''}`
        : 'Não'
      : `${l.matinal_dias} de ${l.matinal_medidos} dias`;
  const corMatinal = l.matinal_aviso ? undefined : l.matinal_dias ? estado.sucesso : estado.perigo;
  return (
    <Cartao style={{ padding: 16, gap: 4 }}>
      <View className="flex-row items-center justify-between gap-2">
        <Texto variante="corpoForte" className="flex-1" numberOfLines={2}>
          {l.loja.nome}
        </Texto>
        <Texto variante="legenda" tom="fraco">
          {l.cobertura ? `cobertura ${l.cobertura}%` : 'sem medição'}
        </Texto>
      </View>
      {!l.tem_ponto ? (
        <Texto variante="legenda" tom="fraco">
          Sem ponto do Tangerino para esta equipe
        </Texto>
      ) : null}
      <LinhaValor
        rotulo="Entradas"
        valor={l.entradas_aviso ?? `${l.entradas ?? '—'}${l.parcial_entradas ? ` (parcial · ${l.cobertura_porta}%)` : ''}`}
        destaque={!l.entradas_aviso}
      />
      {l.clientes != null ? <LinhaValor rotulo="Clientes (sem a equipe)" valor={`${l.clientes} (−${l.entradas_equipe ?? 0} equipe)`} /> : null}
      {l.pico ? <LinhaValor rotulo="Pico" valor={l.pico} /> : null}
      <LinhaValor rotulo="Matinal" valor={matinal} cor={corMatinal} />
      {l.matinal_sem_equipe ? <LinhaValor rotulo="Dias sem equipe no horário" valor={String(l.matinal_sem_equipe)} /> : null}
      <LinhaValor
        rotulo="Atendimentos na mesa"
        valor={l.mesas_aviso ?? `${l.atendimentos ?? '—'}${l.parcial_mesas ? ` (parcial · ${l.cobertura_mesa}%)` : ''}`}
        destaque={!l.mesas_aviso}
      />
      {!l.mesas_aviso && l.atendimentos ? (
        <>
          <LinhaValor rotulo="Mediana · média" valor={`${l.mediana} · ${l.media}`} />
          <LinhaValor rotulo="Mais longo" valor={l.maior} />
        </>
      ) : null}
      {l.longos ? <LinhaValor rotulo={`Ocupação longa (+${maximo} min, fora da média)`} valor={String(l.longos)} /> : null}
      {l.por_mesa.length > 1
        ? l.por_mesa.map((m) => <LinhaValor key={m.nome} rotulo={m.nome} valor={`${m.quantos} · mediana ${m.mediana}`} />)
        : null}
      {l.por_dia.length > 1 ? <DiaADia linha={l} /> : null}
    </Cartao>
  );
}

function DiaADia({ linha: l }: { linha: LinhaIndicador }) {
  const cores = useCores();
  return (
    <View className="mt-2 gap-1" style={{ borderTopWidth: 1, borderTopColor: cores.bordaSuave, paddingTop: 10 }}>
      <Texto variante="rotulo" tom="fraco">
        Dia a dia
      </Texto>
      {l.por_dia.map((dia) => {
        const m = dia.matinal ? MATINAL[dia.matinal] : null;
        return (
          <View key={dia.data} className="flex-row items-center gap-2 py-1">
            <Texto variante="legenda" style={{ width: 46, color: cores.texto }}>
              {diaCurto(dia.data)}
            </Texto>
            <Texto variante="legenda" tom="suave" className="flex-1" numberOfLines={1}>
              {dia.cobertura
                ? [
                    `${dia.entradas ?? '—'} entr.`,
                    dia.clientes != null ? `${dia.clientes} cli.` : null,
                    `${dia.atendimentos ?? '—'} atend.`,
                    dia.atendimentos ? dia.mediana : null,
                    dia.equipe != null ? `${dia.equipe} no ponto` : null,
                  ]
                    .filter(Boolean)
                    .join(' · ')
                : 'sem medição'}
            </Texto>
            {m ? (
              <Texto variante="legenda" style={{ color: m.cor }}>
                {m.rotulo}
              </Texto>
            ) : null}
          </View>
        );
      })}
    </View>
  );
}
