import * as Haptics from 'expo-haptics';
import { type Href, router, useLocalSearchParams } from 'expo-router';
import { Alert, Platform, ScrollView, View } from 'react-native';

import { dataDoDia, faixaDeHorario, isoDoDia, rotuloDoDia } from '@/components/modulos/agenda/datas';
import { ICONE_TIPO, Secao } from '@/components/modulos/agenda/pecas';
import { EsqueletoBloco } from '@/components/modulos/comum';
import { Avatar, BarraDetalhe, Botao, BotaoIcone, Cartao, EstadoErro, Icone, Pressionavel, Selo, Tela, Texto } from '@/components/ui';
import { estado } from '@/constants/theme';
import { useEvento, useExcluirEvento, useResponderConvite } from '@/hooks/modulos/agenda';
import { useCores } from '@/providers/tema';
import { mensagemDoErro } from '@/services/api';
import type { DetalheEvento, StatusConvite } from '@/services/modulos/agenda';
import { comAlfa } from '@/utils/formatar';
import { abrirNoPortal } from '@/utils/links';

const COR_CONVITE: Record<StatusConvite, string> = { pending: estado.alerta, accepted: estado.sucesso, rejected: estado.perigo };

function vibrar(tipo: Haptics.NotificationFeedbackType) {
  if (Platform.OS !== 'web') void Haptics.notificationAsync(tipo);
}

/** Detalhe do evento: horário, local/link, participantes, descrição; responder convite; editar/excluir (dono). */
export default function DetalheDoEvento() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const numero = Number(id);
  const { data, isPending, isError, error, refetch } = useEvento(numero);

  return (
    <Tela>
      <BarraDetalhe
        titulo="Evento"
        direita={
          data?.pode_editar ? (
            <BotaoIcone
              icone="create-outline"
              rotulo="Editar evento"
              onPress={() => router.push({ pathname: '/modulo/agenda/evento', params: { id: String(data.id) } } as unknown as Href)}
            />
          ) : undefined
        }
      />
      {isPending ? (
        <View className="gap-4 p-5">
          <EsqueletoBloco linhas={4} />
          <EsqueletoBloco linhas={3} />
        </View>
      ) : isError || !data ? (
        <EstadoErro mensagem={mensagemDoErro(error)} aoTentar={() => void refetch()} />
      ) : (
        <Conteudo ev={data} />
      )}
    </Tela>
  );
}

function Conteudo({ ev }: { ev: DetalheEvento }) {
  const cores = useCores();
  const dia = isoDoDia(new Date(ev.inicio));
  return (
    <ScrollView contentContainerStyle={{ padding: 20, paddingTop: 8, gap: 16, paddingBottom: 40 }}>
      <Cartao style={{ overflow: 'hidden' }}>
        <View style={{ height: 6, backgroundColor: ev.cor }} />
        <View className="gap-3 p-4">
          <View className="flex-row flex-wrap items-center gap-2">
            <View className="flex-row items-center gap-1.5" style={{ paddingHorizontal: 10, paddingVertical: 4, borderRadius: 999, backgroundColor: comAlfa(ev.cor, 0.14) }}>
              <Icone nome={ICONE_TIPO[ev.tipo] ?? 'calendar-outline'} tamanho={14} cor={ev.cor} />
              <Texto variante="legenda" style={{ color: ev.cor }}>
                {ev.tipo_nome}
              </Texto>
            </View>
            {ev.privado ? <Selo texto="Privado" cor="#6B7280" ponto={false} /> : null}
            {ev.recorrente ? <Selo texto="Toda semana" cor={estado.info} ponto={false} /> : null}
          </View>
          <Texto variante="titulo2" selectable>
            {ev.titulo}
          </Texto>
          <Linha icone="calendar-outline" texto={rotuloDoDia(dia)} />
          <Linha icone="time-outline" texto={faixaDeHorario(ev.inicio, ev.fim, ev.dia_inteiro)} />
          {ev.recorrente && ev.recorrencia_ate ? <Linha icone="repeat" texto={`Repete até ${dataDoDia(ev.recorrencia_ate).toLocaleDateString('pt-BR')}`} /> : null}
          {ev.local ? <Linha icone="location-outline" texto={ev.local} /> : null}
          {!ev.sou_dono && ev.dono ? <Linha icone="person-outline" texto={`Marcado por ${ev.dono}`} /> : null}
        </View>
      </Cartao>

      {ev.pode_responder && ev.meu_convite ? <ResponderConvite conviteId={ev.meu_convite.id} dono={ev.dono} /> : null}
      {ev.meu_convite && !ev.pode_responder ? (
        <Cartao style={{ padding: 14, flexDirection: 'row', alignItems: 'center', gap: 10 }}>
          <Icone nome={ev.meu_convite.status === 'accepted' ? 'checkmark-circle' : 'close-circle'} tamanho={20} cor={COR_CONVITE[ev.meu_convite.status]} />
          <Texto variante="legenda" tom="suave" className="flex-1">
            {ev.meu_convite.status === 'accepted' ? 'Você aceitou este convite.' : 'Você recusou este convite.'}
          </Texto>
        </Cartao>
      ) : null}

      {ev.link ? (
        <Botao
          titulo={ev.link_do_portal ? 'Entrar na sala da reunião' : 'Abrir o link da reunião'}
          icone={ev.link_do_portal ? 'videocam' : 'link'}
          onPress={() => ev.link && void abrirNoPortal(ev.link)}
        />
      ) : null}

      {ev.descricao ? (
        <Secao icone="document-text-outline" titulo="Descrição">
          <Texto selectable>{ev.descricao}</Texto>
        </Secao>
      ) : null}

      <Secao icone="people-outline" titulo={ev.participantes.length ? `Participantes (${ev.participantes.length})` : 'Participantes'}>
        {ev.dono_pessoa ? <Pessoa nome={ev.dono_pessoa.nome} detalhe="Organizador" /> : null}
        {ev.participantes.map((p) => (
          <Pessoa key={p.id} nome={p.nome} detalhe={p.observacao ?? p.setor ?? undefined} selo={{ texto: p.status_nome, cor: COR_CONVITE[p.status] ?? '#6B7280' }} />
        ))}
        {!ev.participantes.length ? (
          <Texto variante="legenda" tom="fraco">
            Ninguém convidado.
          </Texto>
        ) : null}
      </Secao>

      {ev.transcricoes.length ? (
        <Secao icone="mic-outline" titulo="Atas desta reunião" cor="#8B5CF6">
          {ev.transcricoes.map((t) => (
            <Pressionavel
              key={t.id}
              onPress={() => router.push({ pathname: '/modulo/agenda/transcricao/[id]', params: { id: String(t.id) } } as unknown as Href)}
              style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}
              accessibilityLabel={`Abrir a ata ${t.titulo}`}>
              <Texto className="flex-1" numberOfLines={1}>
                {t.titulo}
              </Texto>
              <Texto variante="legenda" tom="fraco">
                {t.status_nome}
              </Texto>
              <Icone nome="chevron-forward" tamanho={16} cor={cores.texto3} />
            </Pressionavel>
          ))}
        </Secao>
      ) : null}

      {ev.tarefa_id ? (
        <Botao titulo="Ver a tarefa no portal" variante="secundario" icone="checkbox-outline" onPress={() => void abrirNoPortal('/users/tasks/')} />
      ) : null}

      {ev.pode_editar ? <Excluir ev={ev} /> : null}
    </ScrollView>
  );
}

function Linha({ icone, texto }: { icone: Parameters<typeof Icone>[0]['nome']; texto: string }) {
  const cores = useCores();
  return (
    <View className="flex-row items-start gap-2.5">
      <Icone nome={icone} tamanho={17} cor={cores.texto3} style={{ marginTop: 2 }} />
      <Texto className="flex-1" selectable>
        {texto}
      </Texto>
    </View>
  );
}

function Pessoa({ nome, detalhe, selo }: { nome: string; detalhe?: string; selo?: { texto: string; cor: string } }) {
  return (
    <View className="flex-row items-center gap-3">
      <Avatar nome={nome} tamanho={34} />
      <View className="flex-1">
        <Texto variante="corpoForte" numberOfLines={1}>
          {nome}
        </Texto>
        {detalhe ? (
          <Texto variante="legenda" tom="fraco" numberOfLines={2}>
            {detalhe}
          </Texto>
        ) : null}
      </View>
      {selo ? <Selo texto={selo.texto} cor={selo.cor} /> : null}
    </View>
  );
}

function ResponderConvite({ conviteId, dono }: { conviteId: number; dono: string | null }) {
  const responder = useResponderConvite();
  const enviar = (resposta: 'aceitar' | 'recusar') =>
    responder.mutate(
      { id: conviteId, resposta },
      {
        onSuccess: () => vibrar(Haptics.NotificationFeedbackType.Success),
        onError: (e) => Alert.alert('Não deu certo', mensagemDoErro(e)),
      },
    );
  return (
    <Cartao style={{ padding: 16, gap: 12, borderColor: comAlfa(estado.alerta, 0.5) }}>
      <View className="flex-row items-center gap-2">
        <Icone nome="mail-unread-outline" tamanho={20} cor={estado.alerta} />
        <Texto variante="corpoForte" className="flex-1">
          {dono ? `${dono.split(' ')[0]} convidou você` : 'Você foi convidado'}
        </Texto>
      </View>
      <View className="flex-row gap-3">
        <Botao
          titulo="Recusar"
          variante="secundario"
          icone="close"
          larguraTotal={false}
          style={{ flex: 1 }}
          desativado={responder.isPending}
          onPress={() => Alert.alert('Recusar o convite?', 'O organizador é avisado.', [{ text: 'Voltar', style: 'cancel' }, { text: 'Recusar', style: 'destructive', onPress: () => enviar('recusar') }])}
        />
        <Botao titulo="Aceitar" icone="checkmark" larguraTotal={false} style={{ flex: 1 }} carregando={responder.isPending} onPress={() => enviar('aceitar')} />
      </View>
    </Cartao>
  );
}

function Excluir({ ev }: { ev: DetalheEvento }) {
  const excluir = useExcluirEvento(ev.id);
  const apagar = (serie: boolean) =>
    excluir.mutate(serie, {
      onSuccess: () => {
        vibrar(Haptics.NotificationFeedbackType.Success);
        router.back();
      },
      onError: (e) => Alert.alert('Não deu para excluir', mensagemDoErro(e)),
    });
  function perguntar() {
    if (ev.recorrente) {
      Alert.alert('Excluir evento que se repete', 'Este evento faz parte de uma série semanal.', [
        { text: 'Voltar', style: 'cancel' },
        { text: 'Só este', onPress: () => apagar(false) },
        { text: 'A série inteira', style: 'destructive', onPress: () => apagar(true) },
      ]);
      return;
    }
    Alert.alert('Excluir este evento?', ev.participantes.length ? 'Os convidados deixam de ver o evento.' : undefined, [
      { text: 'Voltar', style: 'cancel' },
      { text: 'Excluir', style: 'destructive', onPress: () => apagar(false) },
    ]);
  }
  return <Botao titulo="Excluir evento" variante="perigo" icone="trash-outline" carregando={excluir.isPending} onPress={perguntar} style={{ marginTop: 4 }} />;
}

