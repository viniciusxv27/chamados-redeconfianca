import { useState } from 'react';
import { View } from 'react-native';

import { TelaModulo } from '@/components/modulos/comum';
import { dataCurta, Faixa } from '@/components/modulos/ponto/pecas';
import { BotaoIcone, Cartao, Entrada, EstadoVazio, Selo, Texto } from '@/components/ui';
import { estado, fontes, marca } from '@/constants/theme';
import { useEscala } from '@/hooks/modulos/ponto';
import { useCores } from '@/providers/tema';
import type { DiaEscala } from '@/services/modulos/ponto';
import { comAlfa } from '@/utils/formatar';

const ROXO = '#8B5CF6';

/** Minha escala da semana (montada pela loja no portal; não vai para a Sólides). */
export default function MinhaEscala() {
  const [inicio, setInicio] = useState('');
  const { data, isPending, isError, error, refetch, isRefetching } = useEscala(inicio);
  return (
    <TelaModulo titulo="Escala" subtitulo="Minha semana" carregando={isPending} erro={isError ? error : undefined} aoAtualizar={() => void refetch()} atualizando={isRefetching}>
      {data ? (
        <>
          <View className="flex-row items-center gap-3">
            <BotaoIcone icone="chevron-back" rotulo="Semana anterior" onPress={() => setInicio(data.anterior)} />
            <View className="flex-1 items-center">
              <Texto variante="corpoForte">
                {dataCurta(data.semana_inicio)} a {dataCurta(data.semana_fim)}
              </Texto>
              {data.tem_escala ? (
                <Texto variante="legenda" tom={data.abaixo_da_meta ? 'perigo' : 'suave'}>
                  {formatarHoras(data.horas)} de {data.horas_meta}h na semana
                </Texto>
              ) : null}
            </View>
            <BotaoIcone icone="chevron-forward" rotulo="Próxima semana" onPress={() => setInicio(data.proxima)} />
          </View>
          {data.monta_escala ? <Faixa icone="people-outline" texto="A escala da equipe se monta no portal. Aqui aparece só a sua." cor={ROXO} /> : null}
          {data.tem_escala ? (
            data.dias.map((d, i) => (
              <Entrada key={d.data} indice={i}>
                <Dia dia={d} />
              </Entrada>
            ))
          ) : (
            <EstadoVazio icone="grid-outline" titulo="Sem escala nesta semana" texto="Quando a loja montar sua escala, ela aparece aqui." />
          )}
        </>
      ) : null}
    </TelaModulo>
  );
}

function formatarHoras(h: number) {
  const total = Math.round(h * 60);
  const m = total % 60;
  return `${Math.floor(total / 60)}h${m ? String(m).padStart(2, '0') : ''}`;
}

function Dia({ dia }: { dia: DiaEscala }) {
  const cores = useCores();
  const folga = dia.tipo === 'FOLGA' || dia.tipo === 'FERIADO';
  return (
    <Cartao style={{ padding: 14, flexDirection: 'row', alignItems: 'center', gap: 12, borderColor: dia.hoje ? marca[500] : undefined }}>
      <View style={{ width: 48, height: 48, borderRadius: 14, backgroundColor: comAlfa(folga ? estado.sucesso : ROXO, 0.12), alignItems: 'center', justifyContent: 'center' }}>
        <Texto variante="legenda" style={{ color: folga ? estado.sucesso : ROXO, fontFamily: fontes.negrito }}>
          {dia.nome}
        </Texto>
        <Texto variante="legenda" style={{ color: cores.texto2, fontSize: 11 }}>
          {dataCurta(dia.data)}
        </Texto>
      </View>
      <View className="flex-1 gap-1">
        {folga ? (
          <Selo texto={dia.tipo === 'FERIADO' ? 'Feriado' : 'Folga'} cor={estado.sucesso} />
        ) : dia.tipo ? (
          <>
            <Texto variante="corpoForte" style={{ fontVariant: ['tabular-nums'] }}>
              {dia.entrada ?? '—'} às {dia.saida ?? '—'}
            </Texto>
            {dia.saida_almoco || dia.volta_almoco ? (
              <Texto variante="legenda" tom="suave">
                Intervalo {dia.saida_almoco ?? '—'} às {dia.volta_almoco ?? '—'}
              </Texto>
            ) : null}
          </>
        ) : (
          <Texto variante="legenda" tom="fraco">
            Não preenchido
          </Texto>
        )}
        {dia.observacao ? (
          <Texto variante="legenda" tom="fraco">
            {dia.observacao}
          </Texto>
        ) : null}
      </View>
      {dia.horas ? (
        <Texto variante="legenda" tom="suave">
          {formatarHoras(dia.horas)}
        </Texto>
      ) : null}
    </Cartao>
  );
}
