import * as Haptics from 'expo-haptics';
import { type Href, router } from 'expo-router';
import { useState } from 'react';
import { Alert, Platform, View } from 'react-native';

import { faixaDeHorario, isoDoDia, rotuloDoDia } from '@/components/modulos/agenda/datas';
import { Folha } from '@/components/modulos/agenda/pecas';
import { TelaModulo } from '@/components/modulos/comum';
import { Botao, Campo, Cartao, Entrada, EstadoVazio, Icone, Selo, Texto } from '@/components/ui';
import { estado } from '@/constants/theme';
import { useConvites, useResponderConvite, useResponderSolicitacao } from '@/hooks/modulos/agenda';
import { useCores } from '@/providers/tema';
import { mensagemDoErro } from '@/services/api';
import type { ConviteEvento, Resposta, SolicitacaoReuniao } from '@/services/modulos/agenda';

type Pendente = { tipo: 'convite'; id: number; titulo: string } | { tipo: 'solicitacao'; id: number; titulo: string };

/** Convites de evento e pedidos de reunião esperando resposta. */
export default function Convites() {
  const { data, isPending, error, refetch, isRefetching } = useConvites();
  const responderConvite = useResponderConvite();
  const responderSolicitacao = useResponderSolicitacao();
  const [recusando, setRecusando] = useState<Pendente | null>(null);
  const [observacao, setObservacao] = useState('');
  const total = (data?.convites.length ?? 0) + (data?.solicitacoes.length ?? 0);

  function responder(p: Pendente, resposta: Resposta, obs?: string) {
    const opcoes = {
      onSuccess: (r: { mensagem: string }) => {
        if (Platform.OS !== 'web') void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
        setRecusando(null);
        setObservacao('');
        if (p.tipo === 'solicitacao' && resposta === 'aceitar') Alert.alert('Reunião marcada', r.mensagem);
      },
      onError: (e: unknown) => Alert.alert('Não deu certo', mensagemDoErro(e)),
    };
    if (p.tipo === 'convite') responderConvite.mutate({ id: p.id, resposta, observacao: obs }, opcoes);
    else responderSolicitacao.mutate({ id: p.id, resposta, observacao: obs }, opcoes);
  }

  const ocupado = responderConvite.isPending || responderSolicitacao.isPending;

  return (
    <TelaModulo
      titulo="Convites"
      subtitulo={data ? (total ? `${total} para responder` : 'Tudo respondido') : 'Agenda'}
      carregando={isPending}
      erro={data ? undefined : error}
      aoAtualizar={() => void refetch()}
      atualizando={isRefetching}>
      {data && !total ? <EstadoVazio icone="checkmark-done-outline" titulo="Nenhum convite pendente" texto="Quando alguém marcar algo com você, aparece aqui." /> : null}

      {data?.solicitacoes.length ? (
        <Texto variante="rotulo" tom="suave">
          Pedidos de reunião
        </Texto>
      ) : null}
      {data?.solicitacoes.map((s, i) => (
        <Entrada key={`s${s.id}`} indice={i}>
          <CartaoSolicitacao
            sol={s}
            ocupado={ocupado}
            aoAceitar={() => responder({ tipo: 'solicitacao', id: s.id, titulo: s.titulo }, 'aceitar')}
            aoRecusar={() => setRecusando({ tipo: 'solicitacao', id: s.id, titulo: s.titulo })}
          />
        </Entrada>
      ))}

      {data?.convites.length ? (
        <Texto variante="rotulo" tom="suave">
          Convites para eventos
        </Texto>
      ) : null}
      {data?.convites.map((c, i) => (
        <Entrada key={`c${c.id}`} indice={i + (data?.solicitacoes.length ?? 0)}>
          <CartaoConvite
            conv={c}
            ocupado={ocupado}
            aoAceitar={() => responder({ tipo: 'convite', id: c.id, titulo: c.evento.titulo }, 'aceitar')}
            aoRecusar={() => setRecusando({ tipo: 'convite', id: c.id, titulo: c.evento.titulo })}
          />
        </Entrada>
      ))}

      <Folha aberta={!!recusando} titulo={recusando ? `Recusar "${recusando.titulo}"` : ''} aoFechar={() => setRecusando(null)}>
        <View className="gap-4 px-5 pt-2">
          <Campo rotulo="Quer explicar? (opcional)" placeholder="Ex.: estou em outra reunião" value={observacao} onChangeText={setObservacao} multiline maxLength={2000} />
          <Botao titulo="Recusar" variante="perigo" icone="close" carregando={ocupado} onPress={() => recusando && responder(recusando, 'recusar', observacao.trim())} />
        </View>
      </Folha>
    </TelaModulo>
  );
}

function Quando({ inicio, fim, diaInteiro = false }: { inicio: string; fim: string; diaInteiro?: boolean }) {
  const cores = useCores();
  return (
    <View className="flex-row items-center gap-2">
      <Icone nome="time-outline" tamanho={15} cor={cores.texto3} />
      <Texto variante="legenda" tom="suave" className="flex-1">
        {`${rotuloDoDia(isoDoDia(new Date(inicio)))} · ${faixaDeHorario(inicio, fim, diaInteiro)}`}
      </Texto>
    </View>
  );
}

function Botoes({ ocupado, aoAceitar, aoRecusar }: { ocupado: boolean; aoAceitar: () => void; aoRecusar: () => void }) {
  return (
    <View className="flex-row gap-3 pt-1">
      <Botao titulo="Recusar" variante="secundario" icone="close" tamanho="sm" larguraTotal={false} style={{ flex: 1 }} desativado={ocupado} onPress={aoRecusar} />
      <Botao titulo="Aceitar" icone="checkmark" tamanho="sm" larguraTotal={false} style={{ flex: 1 }} desativado={ocupado} onPress={aoAceitar} />
    </View>
  );
}

function CartaoConvite({ conv, ocupado, aoAceitar, aoRecusar }: { conv: ConviteEvento; ocupado: boolean; aoAceitar: () => void; aoRecusar: () => void }) {
  const ev = conv.evento;
  return (
    <Cartao style={{ padding: 16, gap: 8 }}>
      <View className="flex-row items-center gap-2">
        <View style={{ width: 10, height: 10, borderRadius: 5, backgroundColor: ev.cor }} />
        <Texto variante="legenda" tom="fraco" className="flex-1">
          {ev.dono ? `${ev.tipo_nome} · de ${ev.dono}` : ev.tipo_nome}
        </Texto>
      </View>
      <Texto
        variante="corpoForte"
        onPress={() => router.push({ pathname: '/modulo/agenda/[id]', params: { id: String(ev.id) } } as unknown as Href)}
        accessibilityRole="link">
        {ev.titulo}
      </Texto>
      <Quando inicio={ev.inicio} fim={ev.fim} diaInteiro={ev.dia_inteiro} />
      {ev.local ? (
        <Texto variante="legenda" tom="fraco" numberOfLines={1}>
          {ev.local}
        </Texto>
      ) : null}
      <Botoes ocupado={ocupado} aoAceitar={aoAceitar} aoRecusar={aoRecusar} />
    </Cartao>
  );
}

function CartaoSolicitacao({ sol, ocupado, aoAceitar, aoRecusar }: { sol: SolicitacaoReuniao; ocupado: boolean; aoAceitar: () => void; aoRecusar: () => void }) {
  return (
    <Cartao style={{ padding: 16, gap: 8 }}>
      <View className="flex-row items-center gap-2">
        <Selo texto={sol.tipo_nome} cor={estado.info} ponto={false} />
        <Texto variante="legenda" tom="fraco" className="flex-1" numberOfLines={1}>
          {sol.solicitante ? `pedido de ${sol.solicitante.nome}` : ''}
        </Texto>
      </View>
      <Texto variante="corpoForte">{sol.titulo}</Texto>
      <Quando inicio={sol.inicio} fim={sol.fim} />
      {sol.descricao ? (
        <Texto variante="legenda" tom="suave" numberOfLines={4}>
          {sol.descricao}
        </Texto>
      ) : null}
      {sol.local ? (
        <Texto variante="legenda" tom="fraco" numberOfLines={1}>
          {sol.local}
        </Texto>
      ) : null}
      <Texto variante="legenda" tom="fraco">
        Aceitar marca a reunião na agenda dos dois.
      </Texto>
      <Botoes ocupado={ocupado} aoAceitar={aoAceitar} aoRecusar={aoRecusar} />
    </Cartao>
  );
}
