import { View } from 'react-native';

import { Indicador, LinhaValor, TelaModulo } from '@/components/modulos/comum';
import { dataCurta, Faixa } from '@/components/modulos/ponto/pecas';
import { Cartao, Entrada, EstadoVazio, Selo, Texto } from '@/components/ui';
import { estado } from '@/constants/theme';
import { useFerias } from '@/hooks/modulos/ponto';
import { useCores } from '@/providers/tema';
import type { Lancamento, PeriodoFerias } from '@/services/modulos/ponto';

const VERDE_AGUA = '#14B8A6';

/** Minhas férias: a mesma conta da tela /ferias/ do portal (períodos aquisitivos pela CLT). */
export default function MinhasFerias() {
  const { data, isPending, isError, error, refetch, isRefetching } = useFerias();
  return (
    <TelaModulo titulo="Férias" subtitulo="Ponto" carregando={isPending} erro={isError ? error : undefined} aoAtualizar={() => void refetch()} atualizando={isRefetching}>
      {data ? (
        !data.disponivel ? (
          <EstadoVazio
            icone="sunny-outline"
            titulo={data.motivo === 'sem_vinculo' ? 'Sem vínculo com a Sólides' : 'Férias indisponíveis agora'}
            texto={data.motivo === 'sem_vinculo' ? 'Fale com o RH para ligar seu cadastro ao ponto.' : 'O sistema de ponto não respondeu. Tente de novo em instantes.'}
          />
        ) : (
          <>
            {data.em_gozo ? (
              <Faixa icone="sunny" texto={`De férias até ${dataCurta(data.em_gozo.fim)} · volta em ${dataCurta(data.volta_em)}`} cor={VERDE_AGUA} />
            ) : null}
            {data.dias_vencidos ? <Faixa icone="warning-outline" texto={`${data.dias_vencidos} dias de férias vencidos. Combine com seu gestor e o DP.`} cor={estado.perigo} /> : null}
            <Entrada indice={0}>
              <View className="flex-row flex-wrap gap-3">
                <Indicador icone="wallet-outline" rotulo="Saldo de dias" valor={String(data.saldo_total ?? 0)} cor={VERDE_AGUA} detalhe="Períodos já completos" />
                <Indicador
                  icone="airplane-outline"
                  rotulo="Próximas férias"
                  valor={data.proxima ? dataCurta(data.proxima.inicio) : '—'}
                  cor={estado.info}
                  detalhe={data.proxima ? `${data.proxima.dias} dias · ${data.proxima.status.toLowerCase()}` : 'Nada programado'}
                />
              </View>
            </Entrada>
            {data.programadas && data.programadas.length > 1 ? <Lista titulo="Programadas" itens={data.programadas} /> : null}
            {data.periodos?.length ? (
              <View className="gap-3">
                <Texto variante="titulo3">Períodos aquisitivos</Texto>
                {data.periodos.map((p, i) => (
                  <Entrada key={p.numero} indice={i + 1}>
                    <Periodo p={p} />
                  </Entrada>
                ))}
              </View>
            ) : null}
            {data.passadas?.length ? <Lista titulo="Férias já tiradas" itens={data.passadas} /> : null}
            <Texto variante="legenda" tom="fraco" className="text-center">
              Conta para acompanhar: o cálculo oficial é do DP.
            </Texto>
          </>
        )
      ) : null}
    </TelaModulo>
  );
}

function Periodo({ p }: { p: PeriodoFerias }) {
  const cores = useCores();
  const situacao = p.sem_dados
    ? { texto: 'Sem histórico', cor: cores.texto3 }
    : p.vencido
      ? { texto: 'Vencido', cor: estado.perigo }
      : p.vencendo
        ? { texto: `Vence em ${p.dias_para_vencer} dias`, cor: estado.alerta }
        : !p.completo
          ? { texto: 'Em aquisição', cor: estado.info }
          : p.saldo
            ? { texto: 'Com saldo', cor: VERDE_AGUA }
            : { texto: 'Gozado', cor: estado.sucesso };
  return (
    <Cartao style={{ paddingHorizontal: 16, paddingVertical: 10 }}>
      <View className="flex-row items-center gap-2 py-1">
        <Texto variante="corpoForte" className="flex-1">
          {dataCurta(p.inicio, true)} a {dataCurta(p.fim, true)}
        </Texto>
        <Selo texto={situacao.texto} cor={situacao.cor} />
      </View>
      {!p.sem_dados ? (
        <>
          <LinhaValor rotulo="Gozados / direito" valor={`${p.gozados} / ${p.direito} dias`} />
          <LinhaValor rotulo="Saldo" valor={`${p.saldo} dias`} destaque />
          {p.completo ? <LinhaValor rotulo="Tirar até" valor={dataCurta(p.concessivo_fim, true)} /> : null}
        </>
      ) : null}
    </Cartao>
  );
}

function Lista({ titulo, itens }: { titulo: string; itens: Lancamento[] }) {
  return (
    <Cartao style={{ paddingHorizontal: 16, paddingVertical: 10 }}>
      <Texto variante="rotulo" tom="suave" className="py-1">
        {titulo}
      </Texto>
      {itens.map((l) => (
        <LinhaValor key={`${l.inicio}-${l.fim}`} rotulo={`${dataCurta(l.inicio, true)} a ${dataCurta(l.fim, true)}`} valor={`${l.dias} dias`} />
      ))}
    </Cartao>
  );
}
