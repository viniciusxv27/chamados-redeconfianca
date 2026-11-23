/** Histórico da competência (/gestao-metas/<mês>/historico/): quem mudou o quê, quando e por onde. */
import { useLocalSearchParams } from 'expo-router';
import { useState } from 'react';
import { ScrollView, View } from 'react-native';

import { TelaModulo } from '@/components/modulos/comum';
import { Cartao, Chip, Entrada, EstadoVazio, Icone, Texto } from '@/components/ui';
import { marca } from '@/constants/theme';
import { useHistoricoMetas } from '@/hooks/modulos/gestao_metas';
import { useCores } from '@/providers/tema';
import { dataHora } from '@/utils/formatar';

const PILARES = [
  { valor: '', rotulo: 'Todos' },
  { valor: 'movel', rotulo: 'Móvel' },
  { valor: 'fixa', rotulo: 'Fixa' },
  { valor: 'sva', rotulo: 'SVA' },
  { valor: 'seguros', rotulo: 'Seguros' },
  { valor: 'smartphone', rotulo: 'Smartphone' },
  { valor: 'eletronicos', rotulo: 'Eletrônicos' },
  { valor: 'essenciais', rotulo: 'Essenciais' },
];

const CAMPOS: Record<string, string> = {
  meta: 'Meta',
  qtd_cns: 'CNs',
  decisao: 'Decisão',
  nova_meta: 'Nova meta',
  meta_vivo: 'Meta Vivo',
  status: 'Situação',
  divisao_consultores: 'Divisão entre CNs',
};

export default function HistoricoMetas() {
  const { codigo, pilar: inicial } = useLocalSearchParams<{ codigo: string; pilar?: string }>();
  const cores = useCores();
  const [pilar, setPilar] = useState(inicial ?? '');
  const { data, isPending, error, refetch, isRefetching } = useHistoricoMetas(codigo, pilar);

  return (
    <TelaModulo titulo="Histórico" subtitulo={`Competência ${codigo}`} carregando={isPending && !data} erro={data ? undefined : error} aoAtualizar={refetch} atualizando={isRefetching}>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ marginHorizontal: -20 }} contentContainerStyle={{ paddingHorizontal: 20, gap: 8 }}>
        {PILARES.map((p) => (
          <Chip key={p.valor || 'todos'} rotulo={p.rotulo} ativo={pilar === p.valor} onPress={() => setPilar(p.valor)} />
        ))}
      </ScrollView>
      {data?.length ? (
        data.map((h, i) => (
          <Entrada key={h.id} indice={i < 8 ? i : 0}>
            <Cartao style={{ padding: 14, gap: 6 }}>
              <View className="flex-row items-center gap-2">
                <Icone nome="create-outline" tamanho={16} cor={marca[500]} />
                <Texto variante="corpoForte" className="flex-1" numberOfLines={1}>
                  {[h.loja, h.pilar_nome].filter(Boolean).join(' · ') || 'Competência'}
                </Texto>
                <Texto variante="legenda" tom="fraco">
                  {dataHora(h.quando)}
                </Texto>
              </View>
              <Texto variante="legenda" tom="suave">
                {CAMPOS[h.campo] ?? h.campo}: {h.antes ?? '—'} → <Texto variante="legenda" style={{ color: cores.texto }}>{h.depois ?? '—'}</Texto>
              </Texto>
              <Texto variante="legenda" tom="fraco">
                {[h.por, h.origem].filter(Boolean).join(' · ')}
              </Texto>
            </Cartao>
          </Entrada>
        ))
      ) : data ? (
        <EstadoVazio icone="time-outline" titulo="Nada mudou ainda" texto="As mudanças nas metas aparecem aqui." />
      ) : null}
    </TelaModulo>
  );
}
