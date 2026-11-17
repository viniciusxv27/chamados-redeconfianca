import * as DocumentPicker from 'expo-document-picker';
import * as Haptics from 'expo-haptics';
import * as ImagePicker from 'expo-image-picker';
import { useLocalSearchParams } from 'expo-router';
import { type ComponentRef, useRef, useState } from 'react';
import { Alert, KeyboardAvoidingView, Platform, ScrollView, View } from 'react-native';
import Animated, { FadeIn } from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { EsqueletoBloco } from '@/components/modulos/comum';
import { BarraProgresso, diaPorExtenso, Faixa, SeloExecucao } from '@/components/modulos/checklists/pecas';
import { CartaoTarefa, type Origem, pendencia, respondida, type Valor } from '@/components/modulos/checklists/tarefa';
import { Avatar, BarraDetalhe, Botao, BotaoIcone, CabecalhoGrande, Cartao, Entrada, EstadoErro, Tela, Texto } from '@/components/ui';
import { estado } from '@/constants/theme';
import { useEnviarChecklist, useExcluirEvidencia, useExecucao, useSubirEvidencias } from '@/hooks/modulos/checklists';
import { useCores } from '@/providers/tema';
import { mensagemDoErro } from '@/services/api';
import type { ArquivoLocal, Evidencia, Execucao, Resposta, Tarefa } from '@/services/modulos/checklists';
import { dataHora } from '@/utils/formatar';
import { abrirNoPortal } from '@/utils/links';

/**
 * Rascunho do preenchimento, por checklist: fica na memória do app enquanto ele está aberto
 * (sair da tela e voltar não perde nada). As fotos não dependem dele: sobem na hora.
 */
const rascunhos = new Map<number, Record<number, Partial<Valor>>>();

function valorDe(t: Tarefa, r?: Partial<Valor>): Valor {
  return {
    feita: r?.feita ?? t.feita,
    resposta: r && 'resposta' in r ? (r.resposta ?? null) : t.resposta,
    situacao: r && 'situacao' in r ? (r.situacao ?? null) : t.situacao,
    observacao: r?.observacao ?? t.observacao,
  };
}

const EXTENSOES = /\.(jpe?g|png|webp|heic|heif|gif|pdf|docx?|xlsx?|pptx?|txt|mp4|mov)$/i;

/** Um checklist: preencher item a item (dono, enquanto aberto) ou acompanhar o que foi feito. */
export default function ChecklistDetalhe() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const execId = Number(id || 0);
  const consulta = useExecucao(execId);

  if (consulta.isPending) {
    return (
      <Tela>
        <BarraDetalhe />
        <View className="gap-4 p-5">
          <EsqueletoBloco linhas={4} />
          <EsqueletoBloco linhas={5} />
          <EsqueletoBloco linhas={5} />
        </View>
      </Tela>
    );
  }
  if (consulta.isError || !consulta.data) {
    return (
      <Tela>
        <BarraDetalhe />
        <EstadoErro mensagem={mensagemDoErro(consulta.error)} aoTentar={() => void consulta.refetch()} />
      </Tela>
    );
  }
  return <Detalhe execucao={consulta.data} atualizar={() => void consulta.refetch()} />;
}

function Detalhe({ execucao: e, atualizar }: { execucao: Execucao; atualizar: () => void }) {
  const cores = useCores();
  const insets = useSafeAreaInsets();
  const rolagem = useRef<ComponentRef<typeof ScrollView>>(null);
  const posicoes = useRef(new Map<number, number>());
  const [rascunho, setRascunho] = useState<Record<number, Partial<Valor>>>(() => rascunhos.get(e.id) ?? {});
  const [avisos, setAvisos] = useState<Record<number, string>>({});
  const [enviado, setEnviado] = useState<string | null>(null);
  const subir = useSubirEvidencias(e.id);
  const excluir = useExcluirEvidencia(e.id);
  const envio = useEnviarChecklist(e.id);

  const editavel = e.pode_preencher;
  const valores = new Map(e.tarefas.map((t) => [t.id, valorDe(t, rascunho[t.id])]));
  const valor = (t: Tarefa) => valores.get(t.id) ?? valorDe(t);
  const prontas = e.tarefas.filter((t) => respondida(t, valor(t))).length;
  const obrigatorias = e.tarefas.filter((t) => t.obrigatoria);
  const faltamObrigatorias = obrigatorias.filter((t) => !respondida(t, valor(t))).length;
  const progresso = e.tarefas.length ? Math.round((prontas / e.tarefas.length) * 100) : 0;

  function mudar(t: Tarefa, parcial: Partial<Valor>) {
    const novo = { ...rascunho, [t.id]: { ...rascunho[t.id], ...parcial } };
    rascunhos.set(e.id, novo);
    setRascunho(novo);
    if (avisos[t.id]) setAvisos(({ [t.id]: _, ...resto }) => resto);
  }

  async function escolherArquivos(origem: Origem): Promise<ArquivoLocal[]> {
    if (origem === 'arquivo') {
      const r = await DocumentPicker.getDocumentAsync({ multiple: true, copyToCacheDirectory: true });
      if (r.canceled) return [];
      return r.assets.map((a) => ({ uri: a.uri, nome: a.name, tipo: a.mimeType ?? 'application/octet-stream' }));
    }
    const camera = origem === 'camera';
    const permissao = camera ? await ImagePicker.requestCameraPermissionsAsync() : await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!permissao.granted) {
      Alert.alert('Sem permissão', camera ? 'Libere a câmera nas configurações para fotografar.' : 'Libere as fotos nas configurações para anexar.');
      return [];
    }
    const opcoes: ImagePicker.ImagePickerOptions = { mediaTypes: ['images'], quality: 0.6, allowsMultipleSelection: !camera, selectionLimit: 10 };
    const r = camera ? await ImagePicker.launchCameraAsync(opcoes) : await ImagePicker.launchImageLibraryAsync(opcoes);
    if (r.canceled) return [];
    return r.assets.map((a, i) => ({
      uri: a.uri,
      nome: a.fileName && EXTENSOES.test(a.fileName) ? a.fileName : `evidencia-${Date.now()}-${i}.jpg`,
      tipo: a.mimeType ?? 'image/jpeg',
    }));
  }

  async function anexar(t: Tarefa, origem: Origem) {
    const arquivos = await escolherArquivos(origem);
    if (!arquivos.length) return;
    subir.mutate(
      { tarefaId: t.id, arquivos },
      {
        onSuccess: () => {
          if (avisos[t.id]) setAvisos(({ [t.id]: _, ...resto }) => resto);
          if (Platform.OS !== 'web') void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
        },
        onError: (erro) => Alert.alert('Não deu para enviar', mensagemDoErro(erro)),
      },
    );
  }

  function excluirEvidencia(ev: Evidencia) {
    if (!ev.id) return;
    const evId = ev.id;
    const vai = () => excluir.mutate(evId, { onError: (erro) => Alert.alert('Não deu para excluir', mensagemDoErro(erro)) });
    if (Platform.OS === 'web') return vai();
    Alert.alert('Excluir evidência?', ev.nome, [
      { text: 'Cancelar', style: 'cancel' },
      { text: 'Excluir', style: 'destructive', onPress: vai },
    ]);
  }

  function mandar() {
    const novos: Record<number, string> = {};
    for (const t of e.tarefas) {
      const p = pendencia(t, valor(t));
      if (p) novos[t.id] = p;
    }
    setAvisos(novos);
    const primeira = e.tarefas.find((t) => novos[t.id]);
    if (primeira) {
      if (Platform.OS !== 'web') void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning);
      const y = posicoes.current.get(primeira.id);
      if (y != null) rolagem.current?.scrollTo({ y: Math.max(0, y - 12), animated: true });
      return;
    }
    const respostas: Resposta[] = e.tarefas.map((t) => {
      const v = valor(t);
      return { id: t.id, feita: v.feita, resposta: v.resposta, situacao: v.situacao ?? '', observacao: v.observacao };
    });
    const vai = () =>
      envio.mutate(respostas, {
        onSuccess: (r) => {
          rascunhos.delete(e.id);
          setRascunho({});
          setEnviado(r.mensagem);
          rolagem.current?.scrollTo({ y: 0, animated: true });
          if (Platform.OS !== 'web') void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
        },
        onError: () => {
          if (Platform.OS !== 'web') void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
        },
      });
    if (Platform.OS === 'web') return vai();
    Alert.alert('Enviar para aprovação?', 'Depois de enviado, o checklist não pode mais ser alterado.', [
      { text: 'Revisar', style: 'cancel' },
      { text: 'Enviar', onPress: vai },
    ]);
  }

  const responsavel = e.responsavel;
  return (
    <Tela>
      <BarraDetalhe
        titulo={e.checklist}
        direita={<BotaoIcone icone="open-outline" rotulo="Abrir no portal" onPress={() => void abrirNoPortal(e.url_web)} />}
      />
      <KeyboardAvoidingView className="flex-1" behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        <ScrollView ref={rolagem} contentContainerStyle={{ paddingBottom: 32 }} keyboardShouldPersistTaps="handled" keyboardDismissMode="on-drag">
          <CabecalhoGrande titulo={e.checklist} subtitulo={`${diaPorExtenso(e.data, true)} · ${e.periodo_nome}`} />
          <View className="gap-4 px-5">
            {enviado ? (
              <Animated.View entering={FadeIn}>
                <Faixa icone="checkmark-circle" texto={enviado} cor={estado.sucesso} />
              </Animated.View>
            ) : null}

            <Entrada indice={0}>
              <Cartao style={{ padding: 16, gap: 12 }}>
                <View className="flex-row items-center gap-2">
                  <SeloExecucao e={e} />
                  <Texto variante="legenda" tom="fraco" className="ml-auto" style={{ fontVariant: ['tabular-nums'] }}>
                    {editavel ? `${prontas}/${e.tarefas.length} respondidas` : `${e.feitas}/${e.total} feitas`}
                  </Texto>
                </View>
                <BarraProgresso valor={editavel ? progresso : e.progresso} altura={8} />
                {e.setor ? (
                  <Texto variante="legenda" tom="suave">
                    {e.setor}
                  </Texto>
                ) : null}
                {!e.sou_executor && responsavel ? (
                  <View className="flex-row items-center gap-3">
                    <Avatar nome={responsavel.nome} foto={responsavel.foto} tamanho={32} />
                    <View className="flex-1">
                      <Texto variante="legenda" tom="fraco">
                        Responsável
                      </Texto>
                      <Texto variante="corpoForte" numberOfLines={1}>
                        {responsavel.nome}
                      </Texto>
                    </View>
                  </View>
                ) : null}
                {e.enviado_em ? (
                  <Texto variante="legenda" tom="fraco">
                    Enviado em {dataHora(e.enviado_em)}
                  </Texto>
                ) : null}
                {e.descricao ? (
                  <Texto variante="legenda" tom="suave">
                    {e.descricao}
                  </Texto>
                ) : null}
              </Cartao>
            </Entrada>

            {editavel && e.atrasado ? (
              <Faixa icone="time-outline" texto="Este checklist é de um dia que já passou, mas ainda pode ser enviado." cor={estado.alerta} />
            ) : null}
            {editavel ? (
              <Texto variante="legenda" tom="fraco">
                Fotos e arquivos sobem na hora. As respostas vão para o portal quando você enviar.
              </Texto>
            ) : e.sou_executor && e.status === 'awaiting_approval' && !enviado ? (
              <Faixa icone="hourglass-outline" texto="Enviado. Agora é com a aprovação." cor={estado.alerta} />
            ) : null}

            {e.tarefas.map((t, i) => (
              <Entrada key={t.id} indice={i + 1}>
                <View onLayout={(ev) => posicoes.current.set(t.id, ev.nativeEvent.layout.y)}>
                  <CartaoTarefa
                    tarefa={t}
                    numero={i + 1}
                    valor={valor(t)}
                    editavel={editavel}
                    aviso={avisos[t.id]}
                    aoMudar={(p) => mudar(t, p)}
                    aoAnexar={(origem) => void anexar(t, origem)}
                    aoExcluir={excluirEvidencia}
                    subindo={subir.isPending && subir.variables?.tarefaId === t.id}
                    excluindo={excluir.isPending ? (excluir.variables ?? null) : null}
                  />
                </View>
              </Entrada>
            ))}

            {!e.tarefas.length ? (
              <Texto tom="suave" className="text-center">
                Este checklist ainda não tem tarefas.
              </Texto>
            ) : null}

            {envio.isError ? (
              <Animated.View entering={FadeIn}>
                <Faixa icone="alert-circle" texto={mensagemDoErro(envio.error)} cor={estado.perigo} />
              </Animated.View>
            ) : null}
            {!editavel ? <Botao titulo="Atualizar" icone="refresh" variante="secundario" onPress={atualizar} /> : null}
          </View>
        </ScrollView>

        {editavel ? (
          <View style={{ paddingHorizontal: 20, paddingTop: 12, paddingBottom: Math.max(insets.bottom, 16), borderTopWidth: 1, borderTopColor: cores.borda, backgroundColor: cores.superficie, gap: 6 }}>
            <Texto variante="legenda" tom={faltamObrigatorias ? 'suave' : 'sucesso'} className="text-center">
              {faltamObrigatorias
                ? `Falta${faltamObrigatorias > 1 ? 'm' : ''} ${faltamObrigatorias} obrigatória${faltamObrigatorias > 1 ? 's' : ''} de ${obrigatorias.length}`
                : 'Tudo pronto para enviar'}
            </Texto>
            <Botao
              titulo={envio.isPending ? 'Enviando…' : 'Enviar para aprovação'}
              icone="paper-plane"
              tamanho="lg"
              onPress={mandar}
              carregando={envio.isPending}
              desativado={subir.isPending}
            />
          </View>
        ) : null}
      </KeyboardAvoidingView>
    </Tela>
  );
}

