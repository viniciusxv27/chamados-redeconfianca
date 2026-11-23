/**
 * Agenda de outra pessoa — /agenda/usuario/<id>/ e /agenda/disponibilidade/<id>/ do portal juntos:
 * o dia escolhido com os compromissos (para quem pode ver a agenda completa: SUPERADMIN e hierarquia
 * maior no mesmo setor) ou só os horários ocupados, os horários livres (blocos de 30 min das 8h às
 * 18h) e o pedido de reunião (/agenda/solicitar/<id>/), que avisa a pessoa.
 */
import * as Haptics from 'expo-haptics';
import { type Href, router, useLocalSearchParams } from 'expo-router';
import { useState } from 'react';
import { Alert, Platform, ScrollView, View } from 'react-native';

import { dataDoIso, diaCurto, estaNoDia, horaCurta, horaValida, isoDaData, isoDoDia, momento, rotuloDoDia, segundaDa, somarDias, dataDoDia } from '@/components/modulos/agenda/datas';
import { CampoData, CampoHora, CartaoEvento, Folha } from '@/components/modulos/agenda/pecas';
import { EsqueletoBloco, TelaModulo } from '@/components/modulos/comum';
import { Avatar, Botao, BotaoIcone, Campo, Cartao, Chip, Entrada, EstadoVazio, Icone, Pressionavel, Texto } from '@/components/ui';
import { estado, fontes, marca, raios } from '@/constants/theme';
import { useDisponibilidade, useSemana, useSolicitarReuniao } from '@/hooks/modulos/agenda';
import { useCores } from '@/providers/tema';
import { mensagemDoErro } from '@/services/api';
import { comAlfa } from '@/utils/formatar';
import { abrirNoPortal } from '@/utils/links';

export default function AgendaDaPessoa() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const usuario = Number(id);
  const cores = useCores();
  const [hoje] = useState(() => isoDoDia(new Date()));
  const [agoraMs] = useState(() => Date.now());
  const [dia, setDia] = useState(hoje);
  const [pedindo, setPedindo] = useState<{ inicio: string; fim: string } | null>(null);
  const segunda = segundaDa(dia);
  const semana = useSemana(segunda, somarDias(segunda, 6), usuario);
  const livres = useDisponibilidade(usuario, dia);
  const d = livres.data;
  const s = semana.data && semana.data.inicio === segunda ? semana.data : undefined;
  const pessoa = d?.pessoa ?? s?.pessoa ?? null;
  const eventosDoDia = (s?.eventos ?? []).filter((e) => estaNoDia(e, dia));
  const ocupadosDoDia = (s?.ocupados ?? []).filter((o) => estaNoDia(o, dia));
  const livresDoDia = d && d.data === dia ? d.livres : null;

  return (
    <TelaModulo
      titulo={pessoa?.nome ?? 'Agenda'}
      subtitulo={pessoa?.setor ?? (s?.completa ? 'Agenda completa' : 'Horários ocupados e livres')}
      carregando={livres.isPending && !d}
      erro={d ? undefined : livres.error}
      aoAtualizar={() => (void semana.refetch(), void livres.refetch())}
      atualizando={semana.isRefetching || livres.isRefetching}
      direita={d ? <BotaoIcone icone="open-outline" rotulo="Abrir no portal" onPress={() => abrirNoPortal(s?.url_web ?? d.url_web)} /> : undefined}>
      {/* Semana */}
      <View className="flex-row items-center gap-2">
        <BotaoIcone icone="chevron-back" rotulo="Semana anterior" onPress={() => setDia(somarDias(dia, -7))} />
        <View className="flex-1 flex-row gap-1.5">
          {Array.from({ length: 7 }, (_, i) => somarDias(segunda, i)).map((x) => {
            const ativo = x === dia;
            return (
              <Pressionavel
                key={x}
                onPress={() => setDia(x)}
                escala={0.92}
                accessibilityState={{ selected: ativo }}
                accessibilityLabel={rotuloDoDia(x)}
                style={{ flex: 1, alignItems: 'center', paddingVertical: 8, borderRadius: raios.md, backgroundColor: ativo ? marca[500] : cores.superficie, borderWidth: 1.5, borderColor: ativo ? marca[500] : x === hoje ? comAlfa(marca[500], 0.6) : cores.bordaSuave }}>
                <Texto variante="rotulo" style={{ color: ativo ? '#FFFFFF' : cores.texto3 }}>
                  {diaCurto(x)}
                </Texto>
                <Texto variante="corpoForte" style={{ color: ativo ? '#FFFFFF' : cores.texto, fontFamily: fontes.negrito }}>
                  {String(dataDoDia(x).getDate())}
                </Texto>
              </Pressionavel>
            );
          })}
        </View>
        <BotaoIcone icone="chevron-forward" rotulo="Próxima semana" onPress={() => setDia(somarDias(dia, 7))} />
      </View>
      <Texto variante="titulo3">{dia === hoje ? `Hoje · ${rotuloDoDia(dia)}` : rotuloDoDia(dia)}</Texto>

      {/* Compromissos do dia: completos ou só ocupado */}
      {!s ? (
        <EsqueletoBloco linhas={2} />
      ) : s.completa ? (
        eventosDoDia.length ? (
          eventosDoDia.map((ev, i) => (
            <Entrada key={ev.id} indice={i}>
              <CartaoEvento ev={ev} agora={agoraMs} onPress={() => router.push({ pathname: '/modulo/agenda/[id]', params: { id: String(ev.id) } } as unknown as Href)} />
            </Entrada>
          ))
        ) : (
          <EstadoVazio icone="sunny-outline" titulo="Nada marcado" texto="Nenhum compromisso neste dia." />
        )
      ) : (
        <Cartao style={{ padding: 14, gap: 8 }}>
          <Texto variante="legenda" tom="fraco">
            Você vê só quando a pessoa está ocupada, sem os detalhes.
          </Texto>
          {ocupadosDoDia.length ? (
            ocupadosDoDia.map((o, i) => (
              <View key={i} className="flex-row items-center gap-2">
                <Icone nome="remove-circle-outline" tamanho={16} cor={estado.perigo} />
                <Texto variante="corpo">
                  Ocupado · {horaCurta(o.inicio)}–{horaCurta(o.fim)}
                </Texto>
              </View>
            ))
          ) : (
            <Texto variante="corpo">Nenhum horário ocupado neste dia.</Texto>
          )}
        </Cartao>
      )}

      {/* Horários livres */}
      {d && !d.sou_eu ? (
        <View className="gap-3">
          <View className="flex-row items-center gap-2">
            <Icone nome="time-outline" tamanho={18} cor={estado.sucesso} />
            <Texto variante="corpoForte" className="flex-1">
              Horários livres
            </Texto>
            <Chip rotulo="Outro horário" ativo={false} onPress={() => setPedindo({ inicio: '', fim: '' })} />
          </View>
          {livresDoDia == null ? (
            <EsqueletoBloco linhas={2} />
          ) : livresDoDia.length ? (
            <View className="flex-row flex-wrap gap-2">
              {livresDoDia.map((l) => (
                <Pressionavel
                  key={l.inicio}
                  onPress={() => setPedindo(l)}
                  vibrar
                  escala={0.94}
                  accessibilityLabel={`Pedir reunião às ${horaCurta(l.inicio)}`}
                  style={{ paddingHorizontal: 12, height: 36, justifyContent: 'center', borderRadius: raios.pilula, backgroundColor: comAlfa(estado.sucesso, 0.12), borderWidth: 1, borderColor: comAlfa(estado.sucesso, 0.35) }}>
                  <Texto variante="legenda" style={{ color: estado.sucesso, fontFamily: fontes.seminegrito }}>
                    {horaCurta(l.inicio)}–{horaCurta(l.fim)}
                  </Texto>
                </Pressionavel>
              ))}
            </View>
          ) : (
            <Texto variante="legenda" tom="fraco">
              Sem horário livre entre 8h e 18h neste dia.
            </Texto>
          )}
          <Texto variante="legenda" tom="fraco">
            Toque num horário para pedir uma reunião, chamada ou horário com {pessoa?.nome?.split(' ')[0] ?? 'a pessoa'}.
          </Texto>
        </View>
      ) : null}

      {pedindo && d ? <FolhaPedir usuario={usuario} nome={pessoa?.nome ?? ''} tipos={d.tipos} dia={dia} horario={pedindo} aoFechar={() => setPedindo(null)} /> : null}
    </TelaModulo>
  );
}

/** O formulário de /agenda/solicitar/<id>/: título, tipo, quando, local e motivo. */
function FolhaPedir({
  usuario,
  nome,
  tipos,
  dia,
  horario,
  aoFechar,
}: {
  usuario: number;
  nome: string;
  tipos: { valor: string; rotulo: string }[];
  dia: string;
  horario: { inicio: string; fim: string };
  aoFechar: () => void;
}) {
  const solicitar = useSolicitarReuniao(usuario);
  const [titulo, setTitulo] = useState('');
  const [tipo, setTipo] = useState(tipos[0]?.valor ?? 'meeting');
  const [data, setData] = useState(dataDoIso(dia));
  const [inicio, setInicio] = useState(horario.inicio ? horaCurta(horario.inicio) : '');
  const [fim, setFim] = useState(horario.fim ? horaCurta(horario.fim) : '');
  const [local, setLocal] = useState('');
  const [descricao, setDescricao] = useState('');
  const [erros, setErros] = useState<Record<string, string>>({});

  function enviar() {
    const e: Record<string, string> = {};
    const iso = isoDaData(data);
    if (!titulo.trim()) e.titulo = 'Dê um título.';
    if (!iso) e.data = 'Data inválida.';
    if (!horaValida(inicio)) e.inicio = 'Hora inválida.';
    if (!horaValida(fim)) e.fim = 'Hora inválida.';
    else if (horaValida(inicio) && fim <= inicio) e.fim = 'O fim deve ser depois do início.';
    setErros(e);
    if (Object.keys(e).length) return;
    solicitar.mutate(
      { titulo: titulo.trim(), tipo, inicio: momento(iso, inicio), fim: momento(iso, fim), local: local.trim(), descricao: descricao.trim() },
      {
        onSuccess: (r) => {
          if (Platform.OS !== 'web') void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
          aoFechar();
          Alert.alert('Solicitação enviada', r.mensagem, [
            { text: 'Ok' },
            { text: 'Ver enviadas', onPress: () => router.push('/modulo/agenda/solicitacoes' as Href) },
          ]);
        },
        onError: (err) => {
          const campos = (err as { response?: { data?: { campos?: Record<string, string> } } }).response?.data?.campos;
          if (campos) setErros(campos);
          else Alert.alert('Não deu certo', mensagemDoErro(err));
        },
      },
    );
  }

  return (
    <Folha aberta titulo="Pedir reunião" aoFechar={aoFechar}>
      <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={{ paddingHorizontal: 20, paddingBottom: 12, gap: 14 }}>
        <View className="flex-row items-center gap-3">
          <Avatar nome={nome || '?'} tamanho={34} />
          <Texto variante="corpoForte" className="flex-1" numberOfLines={1}>
            Para {nome}
          </Texto>
        </View>
        <Campo rotulo="Título" icone="text-outline" value={titulo} onChangeText={setTitulo} erro={erros.titulo} placeholder="Ex.: Alinhar metas da loja" />
        <View className="flex-row flex-wrap gap-2">
          {tipos.map((t) => (
            <Chip key={t.valor} rotulo={t.rotulo} ativo={tipo === t.valor} onPress={() => setTipo(t.valor)} />
          ))}
        </View>
        <CampoData rotulo="Dia" valor={data} aoMudar={setData} erro={erros.data} />
        <View className="flex-row gap-3">
          <View className="flex-1">
            <CampoHora rotulo="Início" valor={inicio} aoMudar={setInicio} erro={erros.inicio} />
          </View>
          <View className="flex-1">
            <CampoHora rotulo="Fim" valor={fim} aoMudar={setFim} erro={erros.fim} />
          </View>
        </View>
        <Campo rotulo="Local sugerido (opcional)" icone="location-outline" value={local} onChangeText={setLocal} />
        <Campo rotulo="Motivo (opcional)" multiline value={descricao} onChangeText={setDescricao} />
        <Botao titulo="Enviar solicitação" icone="paper-plane-outline" onPress={enviar} carregando={solicitar.isPending} />
      </ScrollView>
    </Folha>
  );
}
