/**
 * Solicitações de reunião — o /agenda/solicitacoes/ do portal: as recebidas (aceitar cria o evento
 * na agenda dos dois; recusar pode levar observação) e as enviadas (cancelar enquanto pendente).
 */
import * as Haptics from 'expo-haptics';
import { type Href, router } from 'expo-router';
import { useState } from 'react';
import { Alert, Platform, View } from 'react-native';

import { faixaDeHorario, isoDoDia, rotuloDoDia } from '@/components/modulos/agenda/datas';
import { Folha } from '@/components/modulos/agenda/pecas';
import { TelaModulo } from '@/components/modulos/comum';
import { Avatar, Botao, BotaoIcone, Campo, Cartao, Entrada, EstadoVazio, Segmentado, Selo, Texto } from '@/components/ui';
import { estado, marca } from '@/constants/theme';
import { useCancelarSolicitacao, useResponderSolicitacao, useSolicitacoes } from '@/hooks/modulos/agenda';
import { mensagemDoErro } from '@/services/api';
import type { SolicitacaoCompleta, StatusSolicitacao } from '@/services/modulos/agenda';

const COR: Record<StatusSolicitacao, string> = { pending: estado.alerta, accepted: estado.sucesso, rejected: estado.perigo, cancelled: '#6B7280' };

function vibrar() {
  if (Platform.OS !== 'web') void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
}

export default function Solicitacoes() {
  const [aba, setAba] = useState<'recebidas' | 'enviadas'>('recebidas');
  const { data, isPending, error, refetch, isRefetching } = useSolicitacoes(aba);
  const responder = useResponderSolicitacao();
  const cancelar = useCancelarSolicitacao();
  const [recusando, setRecusando] = useState<SolicitacaoCompleta | null>(null);
  const [observacao, setObservacao] = useState('');

  function aceitar(s: SolicitacaoCompleta) {
    responder.mutate(
      { id: s.id, resposta: 'aceitar' },
      { onSuccess: (r) => (vibrar(), Alert.alert('Reunião marcada', r.mensagem)), onError: (e) => Alert.alert('Não deu certo', mensagemDoErro(e)) },
    );
  }

  function recusar() {
    if (!recusando) return;
    responder.mutate(
      { id: recusando.id, resposta: 'recusar', observacao },
      {
        onSuccess: () => (vibrar(), setRecusando(null), setObservacao('')),
        onError: (e) => Alert.alert('Não deu certo', mensagemDoErro(e)),
      },
    );
  }

  function confirmarCancelar(s: SolicitacaoCompleta) {
    Alert.alert('Cancelar a solicitação?', `${s.para?.nome ?? 'A pessoa'} será avisada.`, [
      { text: 'Voltar', style: 'cancel' },
      {
        text: 'Cancelar solicitação',
        style: 'destructive',
        onPress: () => cancelar.mutate(s.id, { onSuccess: () => vibrar(), onError: (e) => Alert.alert('Não deu certo', mensagemDoErro(e)) }),
      },
    ]);
  }

  return (
    <TelaModulo
      titulo="Solicitações"
      subtitulo="Pedidos de reunião, chamada e horário"
      carregando={isPending && !data}
      erro={data ? undefined : error}
      aoAtualizar={refetch}
      atualizando={isRefetching}
      direita={<BotaoIcone icone="add" rotulo="Pedir reunião" cor="#FFFFFF" fundo={marca[500]} onPress={() => router.push('/modulo/agenda/pessoas' as Href)} />}>
      <Segmentado
        opcoes={[
          { valor: 'recebidas' as const, rotulo: data?.pendentes_recebidas ? `Recebidas (${data.pendentes_recebidas})` : 'Recebidas' },
          { valor: 'enviadas' as const, rotulo: 'Enviadas' },
        ]}
        valor={aba}
        aoMudar={setAba}
      />
      {data && data.aba === aba ? (
        data.resultados.length ? (
          data.resultados.map((s, i) => {
            const quem = aba === 'enviadas' ? s.para : s.solicitante;
            return (
              <Entrada key={s.id} indice={i < 8 ? i : 0}>
                <Cartao style={{ padding: 16, gap: 10 }}>
                  <View className="flex-row items-center gap-3">
                    <Avatar nome={quem?.nome ?? '?'} tamanho={38} />
                    <View className="flex-1">
                      <Texto variante="corpoForte" numberOfLines={1}>
                        {s.titulo}
                      </Texto>
                      <Texto variante="legenda" tom="fraco" numberOfLines={1}>
                        {aba === 'enviadas' ? 'Para ' : 'De '}
                        {quem?.nome ?? '—'} · {s.tipo_nome}
                      </Texto>
                    </View>
                    <Selo texto={s.status_nome} cor={COR[s.status]} />
                  </View>
                  <Texto variante="legenda" tom="suave">
                    {rotuloDoDia(isoDoDia(new Date(s.inicio)))} · {faixaDeHorario(s.inicio, s.fim, false)}
                    {s.local ? ` · ${s.local}` : ''}
                  </Texto>
                  {s.descricao ? (
                    <Texto variante="legenda" tom="suave">
                      {s.descricao}
                    </Texto>
                  ) : null}
                  {s.resposta ? (
                    <Texto variante="legenda" tom="fraco">
                      Resposta: {s.resposta}
                    </Texto>
                  ) : null}
                  {s.pode_responder ? (
                    <View className="flex-row gap-3">
                      <Botao titulo="Recusar" variante="secundario" icone="close" onPress={() => setRecusando(s)} style={{ flex: 1 }} desativado={responder.isPending} />
                      <Botao titulo="Aceitar" icone="checkmark" onPress={() => aceitar(s)} style={{ flex: 1 }} carregando={responder.isPending && responder.variables?.id === s.id} />
                    </View>
                  ) : null}
                  {s.pode_cancelar ? <Botao titulo="Cancelar solicitação" variante="secundario" icone="close-circle-outline" onPress={() => confirmarCancelar(s)} carregando={cancelar.isPending && cancelar.variables === s.id} /> : null}
                  {s.evento_id ? (
                    <Botao
                      titulo="Ver na agenda"
                      variante="fantasma"
                      icone="calendar-outline"
                      onPress={() => router.push({ pathname: '/modulo/agenda/[id]', params: { id: String(s.evento_id) } } as unknown as Href)}
                    />
                  ) : null}
                </Cartao>
              </Entrada>
            );
          })
        ) : (
          <EstadoVazio
            icone="mail-open-outline"
            titulo={aba === 'enviadas' ? 'Nenhuma solicitação enviada' : 'Nenhuma solicitação recebida'}
            texto={aba === 'enviadas' ? 'Peça uma reunião vendo os horários livres de alguém.' : 'Quando alguém pedir uma reunião com você, aparece aqui.'}
          />
        )
      ) : null}

      <Folha aberta={!!recusando} titulo="Recusar a solicitação" aoFechar={() => setRecusando(null)}>
        <View style={{ paddingHorizontal: 20, gap: 12, paddingBottom: 8 }}>
          <Campo rotulo="Observação (opcional)" multiline value={observacao} onChangeText={setObservacao} placeholder="Ex.: sugiro outro horário…" />
          <Botao titulo="Recusar" variante="perigo" icone="close" onPress={recusar} carregando={responder.isPending} />
        </View>
      </Folha>
    </TelaModulo>
  );
}
