import { router, type Href } from 'expo-router';
import { useState } from 'react';
import { Alert, View } from 'react-native';

import { AZUL_PONTO, Batidas, diaPorExtenso, Faixa, saldo } from '@/components/modulos/ponto/pecas';
import { LinhaValor, TelaModulo } from '@/components/modulos/comum';
import { Botao, Cartao, Entrada, EstadoVazio, Selo, Segmentado, Texto } from '@/components/ui';
import { estado, fontes } from '@/constants/theme';
import { useCancelarAjusteSolides, useMarcacoes } from '@/hooks/modulos/ponto';
import { useCores } from '@/providers/tema';
import { mensagemDoErro } from '@/services/api';
import type { AjusteSolides, DiaPonto } from '@/services/modulos/ponto';
import { tempoRelativo } from '@/utils/formatar';

type Recorte = 'semana' | 'periodo';

const COR_AJUSTE: Record<AjusteSolides['status'], string> = {
  PENDENTE: estado.info,
  APLICADA: estado.sucesso,
  RECUSADA: estado.perigo,
  ERRO: estado.alerta,
  CANCELADA: '#6B7280',
};

/** Marcações do período (o mesmo recorte do "Meu ponto" do portal: semana atual + 28 dias). */
export default function MarcacoesDoPeriodo() {
  const { data, isPending, isError, error, refetch, isRefetching } = useMarcacoes();
  const [recorte, setRecorte] = useState<Recorte>('semana');
  const dias = (data?.dias ?? []).filter((d) => recorte === 'periodo' || d.dia >= (data?.inicio_semana ?? ''));
  const totais = recorte === 'semana' ? data?.semana : data?.periodo;

  return (
    <TelaModulo titulo="Marcações" subtitulo="Meu ponto" carregando={isPending} erro={isError ? error : undefined} aoAtualizar={() => void refetch()} atualizando={isRefetching}>
      {data ? (
        <>
          {data.erro ? <Faixa icone="alert-circle-outline" texto={`O ponto não respondeu: ${data.erro}`} cor={estado.perigo} /> : null}
          {!data.vinculado ? <Faixa icone="link-outline" texto="Seu usuário ainda não está ligado ao ponto da Sólides. Fale com o RH." cor={estado.alerta} /> : null}
          <Segmentado
            opcoes={[
              { valor: 'semana', rotulo: 'Esta semana' },
              { valor: 'periodo', rotulo: 'Últimos 28 dias' },
            ]}
            valor={recorte}
            aoMudar={setRecorte}
          />
          {totais ? (
            <Cartao style={{ paddingHorizontal: 16, paddingVertical: 8 }}>
              <LinhaValor rotulo="Trabalhado" valor={totais.feito ?? '—'} destaque />
              {totais.previsto ? <LinhaValor rotulo="Previsto (sem contar hoje)" valor={totais.previsto} /> : null}
              {totais.diferenca_segundos != null ? (
                <LinhaValor rotulo="Saldo" valor={saldo(totais.diferenca_segundos)} destaque cor={totais.diferenca_segundos < 0 ? estado.perigo : estado.sucesso} />
              ) : null}
              {data.jornada ? <LinhaValor rotulo="Jornada" valor={`${data.jornada.nome} · ${data.jornada.horas_semana}h/sem.`} /> : null}
            </Cartao>
          ) : null}

          {data.ajustes_solides.ligado && data.ajustes_solides.pedidos.length ? <PedidosSolides pedidos={data.ajustes_solides.pedidos} /> : null}

          {dias.length ? (
            dias.map((d, i) => (
              <Entrada key={d.dia} indice={i < 8 ? i : 0}>
                <Dia dia={d} hoje={d.dia === data.hoje} />
              </Entrada>
            ))
          ) : (
            <EstadoVazio icone="calendar-outline" titulo="Sem dias para mostrar" />
          )}
        </>
      ) : null}
    </TelaModulo>
  );
}

function Dia({ dia, hoje }: { dia: DiaPonto; hoje: boolean }) {
  const cores = useCores();
  const negativo = (dia.diferenca_segundos ?? 0) < 0 && !hoje;
  return (
    <Cartao style={{ padding: 14, gap: 10, opacity: dia.sem_marcacao && dia.fim_de_semana ? 0.7 : 1 }}>
      <View className="flex-row items-center gap-2">
        <Texto variante="corpoForte" className="flex-1" style={{ textTransform: 'capitalize' }}>
          {hoje ? 'Hoje' : diaPorExtenso(dia.dia)}
        </Texto>
        <Texto variante="corpoForte" style={{ fontVariant: ['tabular-nums'] }}>
          {dia.horas}
        </Texto>
      </View>
      <Batidas eventos={dia.eventos} vazio={dia.fim_de_semana ? 'Fim de semana' : 'Sem batidas'} />
      <View className="flex-row flex-wrap items-center gap-2">
        {dia.aberto ? <Selo texto="Saída em aberto" cor={estado.alerta} /> : null}
        {dia.previsto ? (
          <Texto variante="legenda" tom="fraco">
            Previsto {dia.previsto}
          </Texto>
        ) : null}
        {dia.diferenca_segundos != null && !hoje && dia.previsto ? (
          <Texto variante="legenda" style={{ color: negativo ? estado.perigo : estado.sucesso, fontFamily: fontes.seminegrito }}>
            {saldo(dia.diferenca_segundos)}
          </Texto>
        ) : null}
        <View className="ml-auto">
          <Botao
            titulo="Pedir ajuste"
            tamanho="sm"
            variante="fantasma"
            icone="create-outline"
            larguraTotal={false}
            onPress={() => router.push({ pathname: '/modulo/ponto/nova', params: { tipo: 'AJUSTE', data: dia.dia } } as unknown as Href)}
          />
        </View>
      </View>
      {dia.eventos.some((e) => e.editado) ? (
        <Texto variante="legenda" style={{ color: cores.texto3 }}>
          Batida com lápis: editada na Sólides.
        </Texto>
      ) : null}
    </Cartao>
  );
}

/** Pedidos de ajuste feitos pelo portal para a Sólides (só aparecem com a função ligada). */
function PedidosSolides({ pedidos }: { pedidos: AjusteSolides[] }) {
  const cancelar = useCancelarAjusteSolides();
  function confirmar(p: AjusteSolides) {
    Alert.alert('Cancelar o pedido?', `${p.tipo_nome} em ${diaPorExtenso(p.dia)} (${p.resumo}).`, [
      { text: 'Voltar', style: 'cancel' },
      {
        text: 'Cancelar pedido',
        style: 'destructive',
        onPress: () => cancelar.mutate(p.id, { onError: (e) => Alert.alert('Não deu para cancelar', mensagemDoErro(e)) }),
      },
    ]);
  }
  return (
    <Cartao style={{ padding: 16, gap: 12 }}>
      <Texto variante="rotulo" tom="suave">
        Pedidos de ajuste na Sólides
      </Texto>
      {pedidos.map((p) => (
        <View key={p.id} className="gap-1.5">
          <View className="flex-row items-center gap-2">
            <Texto variante="corpoForte" className="flex-1" numberOfLines={1}>
              {p.tipo_nome} · {p.resumo}
            </Texto>
            <Selo texto={p.status_nome} cor={COR_AJUSTE[p.status] ?? AZUL_PONTO} />
          </View>
          <Texto variante="legenda" tom="fraco">
            {diaPorExtenso(p.dia)} · pedido {tempoRelativo(p.criado_em)}
          </Texto>
          {p.resposta ? (
            <Texto variante="legenda" tom="suave">
              Resposta: {p.resposta}
            </Texto>
          ) : null}
          {p.pode_cancelar ? (
            <Botao titulo="Cancelar pedido" tamanho="sm" variante="secundario" larguraTotal={false} carregando={cancelar.isPending && cancelar.variables === p.id} onPress={() => confirmar(p)} />
          ) : null}
        </View>
      ))}
    </Cartao>
  );
}
