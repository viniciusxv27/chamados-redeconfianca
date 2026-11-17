import * as DocumentPicker from 'expo-document-picker';
import * as Haptics from 'expo-haptics';
import * as ImagePicker from 'expo-image-picker';
import { router, useLocalSearchParams, type Href } from 'expo-router';
import { useState } from 'react';
import { Alert, KeyboardAvoidingView, Platform, ScrollView, View } from 'react-native';
import Animated, { FadeIn, FadeOut, LinearTransition } from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { EsqueletoBloco } from '@/components/modulos/comum';
import { CampoData, CampoHora, dataDoIso, EscolhaEmChips, isoDaData } from '@/components/modulos/ponto/campos';
import { Faixa } from '@/components/modulos/ponto/pecas';
import { Botao, BotaoIcone, Campo, EstadoErro, Icone, type NomeIcone, Pressionavel, Segmentado, Tela, Texto } from '@/components/ui';
import { estado, fontes, marca, raios } from '@/constants/theme';
import { useCorrigirSolicitacao, useEnviarSolicitacao, useFormularioPonto, useRegistradoNoDia, useSolicitacao } from '@/hooks/modulos/ponto';
import { useCores } from '@/providers/tema';
import { mensagemDoErro } from '@/services/api';
import type { ArquivoLocal, CamposSolicitacao, Solicitacao, TipoSolicitacao } from '@/services/modulos/ponto';
import { tamanhoArquivo } from '@/utils/formatar';

const TIPOS: { valor: TipoSolicitacao; rotulo: string }[] = [
  { valor: 'AJUSTE', rotulo: 'Ajuste de ponto' },
  { valor: 'ATESTADO', rotulo: 'Atestado' },
  { valor: 'DECLARACAO', rotulo: 'Declaração' },
];

const EXTENSOES = /\.(pdf|jpe?g|png|webp|heic|heif)$/i;

/**
 * Nova solicitação ao DP (ou correção de uma devolvida, com ?corrigir=<id>).
 * As regras são as do portal (solicitacoes_dp/servicos.py:validar): o servidor confere tudo
 * e devolve as mesmas mensagens; aqui só se adianta o óbvio.
 */
export default function NovaSolicitacao() {
  const params = useLocalSearchParams<{ tipo?: string; data?: string; corrigir?: string }>();
  const idCorrigir = Number(params.corrigir || 0);
  const original = useSolicitacao(idCorrigir);
  if (idCorrigir && original.isPending) {
    return (
      <Tela>
        <Topo titulo="Corrigir" />
        <View className="gap-4 p-5">
          <EsqueletoBloco linhas={4} />
          <EsqueletoBloco linhas={3} />
        </View>
      </Tela>
    );
  }
  if (idCorrigir && (original.isError || !original.data)) {
    return (
      <Tela>
        <Topo titulo="Corrigir" />
        <EstadoErro mensagem={mensagemDoErro(original.error)} aoTentar={() => void original.refetch()} />
      </Tela>
    );
  }
  return <Formulario key={idCorrigir || 'nova'} original={idCorrigir ? original.data : undefined} tipoInicial={params.tipo} dataInicial={params.data} />;
}

function Topo({ titulo, rotulo = 'Solicitação ao DP' }: { titulo: string; rotulo?: string }) {
  const insets = useSafeAreaInsets();
  return (
    <View className="flex-row items-center justify-between px-5 pb-3" style={{ paddingTop: insets.top + 10 }}>
      <View className="flex-1">
        <Texto variante="rotulo" tom="fraco">
          {rotulo}
        </Texto>
        <Texto variante="titulo2">{titulo}</Texto>
      </View>
      <BotaoIcone icone="close" rotulo="Fechar" onPress={() => router.back()} />
    </View>
  );
}

function horasDe(sol: Solicitacao | undefined, rotulos: string[]) {
  const horas: Record<number, string> = {};
  for (const m of sol?.marcacoes ?? []) {
    const i = rotulos.indexOf(m.rotulo);
    if (i >= 0) horas[i] = m.hora;
  }
  return horas;
}

function Formulario({ original, tipoInicial, dataInicial }: { original?: Solicitacao; tipoInicial?: string; dataInicial?: string }) {
  const cores = useCores();
  const insets = useSafeAreaInsets();
  const form = useFormularioPonto();
  const enviar = useEnviarSolicitacao();
  const corrigir = useCorrigirSolicitacao(original?.id ?? 0);
  const envio = original ? corrigir : enviar;

  const tipoValido = TIPOS.some((t) => t.valor === tipoInicial) ? (tipoInicial as TipoSolicitacao) : 'AJUSTE';
  const [tipo, setTipo] = useState<TipoSolicitacao>(original?.tipo ?? tipoValido);
  const [data, setData] = useState(dataDoIso(original?.data ?? dataInicial));
  const [dataFim, setDataFim] = useState(dataDoIso(original?.data_fim));
  const [horaInicio, setHoraInicio] = useState(original?.hora_inicio ?? '');
  const [horaFim, setHoraFim] = useState(original?.hora_fim ?? '');
  const [motivo, setMotivo] = useState(original?.motivo_ajuste ?? '');
  const [especie, setEspecie] = useState(original?.especie ?? '');
  const [justificativa, setJustificativa] = useState(original?.justificativa ?? '');
  const [comentario, setComentario] = useState('');
  const [horas, setHoras] = useState<Record<number, string> | null>(null);
  const [extras, setExtras] = useState(false);
  const [anexos, setAnexos] = useState<ArquivoLocal[]>([]);
  const [remover, setRemover] = useState<number[]>([]);
  const [erros, setErros] = useState<Record<string, string>>({});

  const rotulos = form.data?.rotulos_marcacao ?? [];
  // As horas da correção dependem dos rótulos do formulário: lidas quando ele chega.
  const horasAtuais = horas ?? horasDe(original, rotulos);
  const mostrarExtras = extras || !!horasAtuais[4] || !!horasAtuais[5];
  const dataIso = isoDaData(data);
  const registrado = useRegistradoNoDia(tipo === 'AJUSTE' && dataIso ? dataIso : null);
  const maxAnexos = form.data?.anexos_max ?? 5;
  const maxMb = form.data?.tamanho_max_mb ?? 15;
  const ficam = (original?.anexos_lista ?? []).filter((a) => !remover.includes(a.id)).length;

  function limpar(campo: string) {
    if (erros[campo]) setErros(({ [campo]: _, ...resto }) => resto);
  }

  async function adicionarFoto(camera: boolean) {
    const permissao = camera ? await ImagePicker.requestCameraPermissionsAsync() : await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!permissao.granted) {
      Alert.alert('Sem permissão', camera ? 'Libere a câmera nas configurações para fotografar.' : 'Libere as fotos nas configurações para anexar.');
      return;
    }
    const opcoes: ImagePicker.ImagePickerOptions = { mediaTypes: ['images'], quality: 0.7, allowsMultipleSelection: !camera, selectionLimit: Math.max(1, maxAnexos - ficam - anexos.length) };
    const r = camera ? await ImagePicker.launchCameraAsync(opcoes) : await ImagePicker.launchImageLibraryAsync(opcoes);
    if (r.canceled) return;
    juntar(
      r.assets.map((a, i) => {
        const nome = a.fileName && EXTENSOES.test(a.fileName) ? a.fileName : `foto-${Date.now()}-${i}.jpg`;
        return { uri: a.uri, nome, tipo: a.mimeType ?? 'image/jpeg', tamanho: a.fileSize };
      }),
    );
  }

  async function adicionarArquivo() {
    const r = await DocumentPicker.getDocumentAsync({ multiple: true, copyToCacheDirectory: true, type: ['application/pdf', 'image/*'] });
    if (r.canceled) return;
    juntar(r.assets.map((a) => ({ uri: a.uri, nome: a.name, tipo: a.mimeType ?? 'application/octet-stream', tamanho: a.size })));
  }

  function juntar(novos: ArquivoLocal[]) {
    const recusados = novos.filter((a) => !EXTENSOES.test(a.nome) || (a.tamanho ?? 0) > maxMb * 1024 * 1024);
    if (recusados.length) Alert.alert('Arquivo não aceito', `Envie PDF ou foto (JPG, PNG, WEBP, HEIC) de até ${maxMb} MB.`);
    setAnexos((atual) => [...atual, ...novos.filter((a) => !recusados.includes(a))].slice(0, Math.max(0, maxAnexos - ficam)));
    limpar('anexos');
  }

  function validar() {
    const e: Record<string, string> = {};
    if (!dataIso) e.data = 'Informe a data (DD/MM/AAAA).';
    if (justificativa.trim().length < 10) e.justificativa = 'Explique o que aconteceu (pelo menos uma frase).';
    if (tipo === 'AJUSTE') {
      if (!motivo) e.motivo = 'Diga o que aconteceu.';
      const preenchidas = Object.values(horasAtuais).filter(Boolean);
      if (!preenchidas.length) e.horas = 'Informe os horários certos do dia.';
      else if (preenchidas.some((h) => !/^\d{2}:\d{2}$/.test(h))) e.horas = 'Use horários no formato 08:00.';
    } else {
      if (!especie) e.especie = 'Escolha o tipo do documento.';
      if (dataFim && !isoDaData(dataFim)) e.data_fim = 'Data final inválida (DD/MM/AAAA).';
      if (!!horaInicio !== !!horaFim) e.horas_doc = 'Informe as duas horas (das / até as) ou nenhuma.';
      if (!anexos.length && !ficam) e.anexos = 'Anexe o documento (foto ou PDF).';
    }
    setErros(e);
    return Object.keys(e).length === 0;
  }

  function mandar() {
    if (!validar()) {
      if (Platform.OS !== 'web') void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning);
      return;
    }
    const campos: CamposSolicitacao = {
      tipo,
      data: dataIso,
      justificativa: justificativa.trim(),
      ...(tipo === 'AJUSTE'
        ? { motivo_ajuste: motivo, horas: horasAtuais }
        : { especie, data_fim: isoDaData(dataFim) || undefined, hora_inicio: horaInicio || undefined, hora_fim: horaFim || undefined }),
      anexos,
      comentario: original ? comentario.trim() : undefined,
      remover: original ? remover : undefined,
    };
    envio.mutate(campos, {
      onSuccess: (sol) => {
        if (Platform.OS !== 'web') void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
        if (original) router.back();
        else router.replace({ pathname: '/modulo/ponto/[id]', params: { id: String(sol.id) } } as unknown as Href);
      },
      onError: () => {
        if (Platform.OS !== 'web') void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
      },
    });
  }

  const especies = tipo === 'ATESTADO' ? form.data?.especies.ATESTADO : tipo === 'DECLARACAO' ? form.data?.especies.DECLARACAO : undefined;

  return (
    <Tela>
      <Topo titulo={original ? 'Corrigir e reenviar' : 'Nova solicitação'} rotulo={original ? `Solicitação #${original.id}` : 'Solicitação ao DP'} />
      {form.isPending ? (
        <View className="gap-4 p-5">
          <EsqueletoBloco linhas={4} />
        </View>
      ) : form.isError ? (
        <EstadoErro mensagem={mensagemDoErro(form.error)} aoTentar={() => void form.refetch()} />
      ) : (
        <KeyboardAvoidingView className="flex-1" behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
          <ScrollView contentContainerStyle={{ padding: 20, gap: 18, paddingBottom: 40 }} keyboardShouldPersistTaps="handled">
            {original?.resposta && original.status === 'DEVOLVIDA' ? <Faixa icone="arrow-undo-outline" texto={`O DP pediu: ${original.resposta}`} cor={estado.alerta} /> : null}
            {!original ? (
              <Segmentado
                opcoes={TIPOS}
                valor={tipo}
                aoMudar={(v) => {
                  setTipo(v);
                  setErros({});
                }}
              />
            ) : null}

            <Animated.View key={tipo} entering={FadeIn.duration(220)} layout={LinearTransition} className="gap-4">
              {tipo === 'AJUSTE' ? (
                <>
                  <CampoData
                    rotulo="Dia do ajuste"
                    valor={data}
                    aoMudar={(v) => (setData(v), limpar('data'))}
                    erro={erros.data}
                    ajuda={`Dos últimos ${form.data?.dias_para_tras_ajuste ?? 90} dias.`}
                  />
                  {dataIso ? (
                    <Texto variante="legenda" tom="suave">
                      {registrado.isPending
                        ? 'Conferindo o que o relógio registrou…'
                        : registrado.data == null
                          ? 'O espelho do ponto ainda não tem este dia.'
                          : registrado.data.length
                            ? `O relógio registrou: ${registrado.data.join(' · ')}`
                            : 'O relógio não registrou nenhuma batida neste dia.'}
                    </Texto>
                  ) : null}
                  <EscolhaEmChips rotulo="O que aconteceu?" opcoes={form.data?.motivos ?? []} valor={motivo} aoEscolher={(v) => (setMotivo(v), limpar('motivo'))} erro={erros.motivo} />
                  <View className="gap-2">
                    <Texto variante="legenda" tom="suave" style={{ fontFamily: fontes.seminegrito }}>
                      Os horários certos do dia
                    </Texto>
                    {rotulos.map((r, i) =>
                      i < 4 || mostrarExtras ? (
                        <View key={r} className="flex-row items-center gap-3">
                          <Texto variante="legenda" tom="suave" style={{ width: 132 }}>
                            {r}
                          </Texto>
                          <View className="flex-1">
                            <CampoHora
                              valor={horasAtuais[i] ?? ''}
                              aoMudar={(v) => {
                                setHoras({ ...horasAtuais, [i]: v });
                                limpar('horas');
                              }}
                            />
                          </View>
                        </View>
                      ) : null,
                    )}
                    {!mostrarExtras ? <Botao titulo="Mais um par de horários" icone="add" variante="fantasma" tamanho="sm" larguraTotal={false} onPress={() => setExtras(true)} /> : null}
                    {erros.horas ? (
                      <Texto variante="legenda" tom="perigo">
                        {erros.horas}
                      </Texto>
                    ) : null}
                  </View>
                </>
              ) : (
                <>
                  <EscolhaEmChips rotulo="Tipo do documento" opcoes={especies ?? []} valor={especie} aoEscolher={(v) => (setEspecie(v), limpar('especie'))} erro={erros.especie} />
                  <CampoData rotulo={tipo === 'ATESTADO' ? 'Primeiro dia' : 'Dia'} valor={data} aoMudar={(v) => (setData(v), limpar('data'))} erro={erros.data} />
                  <CampoData rotulo="Até (se for mais de um dia)" valor={dataFim} aoMudar={(v) => (setDataFim(v), limpar('data_fim'))} erro={erros.data_fim} atalhos={false} />
                  <View className="gap-1.5">
                    <Texto variante="legenda" tom="suave" style={{ fontFamily: fontes.seminegrito }}>
                      Só algumas horas? (opcional)
                    </Texto>
                    <View className="flex-row gap-3">
                      <View className="flex-1">
                        <CampoHora valor={horaInicio} aoMudar={(v) => (setHoraInicio(v), limpar('horas_doc'))} />
                      </View>
                      <View className="flex-1">
                        <CampoHora valor={horaFim} aoMudar={(v) => (setHoraFim(v), limpar('horas_doc'))} />
                      </View>
                    </View>
                    {erros.horas_doc ? (
                      <Texto variante="legenda" tom="perigo">
                        {erros.horas_doc}
                      </Texto>
                    ) : null}
                  </View>
                </>
              )}

              <Campo
                rotulo="Justificativa"
                placeholder={tipo === 'AJUSTE' ? 'Conte o que aconteceu no dia.' : 'Conte o motivo do documento.'}
                value={justificativa}
                onChangeText={(v) => (setJustificativa(v), limpar('justificativa'))}
                erro={erros.justificativa}
                multiline
                maxLength={2000}
              />
              {original ? <Campo rotulo="Recado para o DP (opcional)" placeholder="O que você corrigiu" value={comentario} onChangeText={setComentario} multiline maxLength={1000} /> : null}
            </Animated.View>

            <View className="gap-2">
              <Texto variante="legenda" tom="suave" style={{ fontFamily: fontes.seminegrito }}>
                {tipo === 'AJUSTE' ? 'Comprovante (opcional)' : 'Documento (foto ou PDF)'} · {ficam + anexos.length}/{maxAnexos}
              </Texto>
              {(original?.anexos_lista ?? []).map((a) => {
                const sai = remover.includes(a.id);
                return (
                  <ItemArquivo
                    key={`o-${a.id}`}
                    nome={a.nome}
                    detalhe={sai ? 'Será retirado' : tamanhoArquivo(a.tamanho)}
                    imagem={a.imagem}
                    riscado={sai}
                    icone={sai ? 'arrow-undo-outline' : 'close-circle'}
                    rotuloAcao={sai ? `Manter ${a.nome}` : `Retirar ${a.nome}`}
                    aoAgir={() => setRemover((atual) => (sai ? atual.filter((x) => x !== a.id) : [...atual, a.id]))}
                  />
                );
              })}
              {anexos.map((a, i) => (
                <ItemArquivo
                  key={`${a.uri}-${i}`}
                  nome={a.nome}
                  detalhe={a.tamanho ? tamanhoArquivo(a.tamanho) : undefined}
                  imagem={a.tipo.startsWith('image/')}
                  icone="close-circle"
                  rotuloAcao={`Remover ${a.nome}`}
                  aoAgir={() => setAnexos((atual) => atual.filter((_, j) => j !== i))}
                />
              ))}
              {ficam + anexos.length < maxAnexos ? (
                <View className="flex-row gap-2">
                  {Platform.OS !== 'web' ? <BotaoAnexo icone="camera-outline" rotulo="Câmera" onPress={() => adicionarFoto(true)} /> : null}
                  <BotaoAnexo icone="images-outline" rotulo="Fotos" onPress={() => adicionarFoto(false)} />
                  <BotaoAnexo icone="document-attach-outline" rotulo="PDF" onPress={adicionarArquivo} />
                </View>
              ) : null}
              {erros.anexos ? (
                <Texto variante="legenda" tom="perigo">
                  {erros.anexos}
                </Texto>
              ) : null}
            </View>

            {envio.isError ? (
              <Animated.View entering={FadeIn}>
                <Faixa icone="alert-circle" texto={mensagemDoErro(envio.error)} cor={estado.perigo} />
              </Animated.View>
            ) : null}
          </ScrollView>

          <View style={{ paddingHorizontal: 20, paddingTop: 12, paddingBottom: Math.max(insets.bottom, 16), borderTopWidth: 1, borderTopColor: cores.borda, backgroundColor: cores.superficie }}>
            <Botao titulo={envio.isPending ? 'Enviando…' : original ? 'Reenviar ao DP' : 'Enviar ao DP'} icone="paper-plane" tamanho="lg" onPress={mandar} carregando={envio.isPending} />
          </View>
        </KeyboardAvoidingView>
      )}
    </Tela>
  );
}

function ItemArquivo({
  nome,
  detalhe,
  imagem,
  riscado,
  icone,
  rotuloAcao,
  aoAgir,
}: {
  nome: string;
  detalhe?: string;
  imagem: boolean;
  riscado?: boolean;
  icone: NomeIcone;
  rotuloAcao: string;
  aoAgir: () => void;
}) {
  const cores = useCores();
  return (
    <Animated.View entering={FadeIn} exiting={FadeOut} layout={LinearTransition} className="flex-row items-center gap-3 rounded-md px-3 py-2.5" style={{ backgroundColor: cores.superficie, opacity: riscado ? 0.6 : 1 }}>
      <Icone nome={imagem ? 'image-outline' : 'document-outline'} tamanho={20} cor={marca[500]} />
      <View className="flex-1">
        <Texto variante="legenda" numberOfLines={1} style={riscado ? { textDecorationLine: 'line-through' } : undefined}>
          {nome}
        </Texto>
        {detalhe ? (
          <Texto variante="legenda" tom="fraco" style={{ fontSize: 11 }}>
            {detalhe}
          </Texto>
        ) : null}
      </View>
      <Pressionavel onPress={aoAgir} hitSlop={10} accessibilityLabel={rotuloAcao}>
        <Icone nome={icone} tamanho={20} cor={cores.texto3} />
      </Pressionavel>
    </Animated.View>
  );
}

function BotaoAnexo({ icone, rotulo, onPress }: { icone: NomeIcone; rotulo: string; onPress: () => void }) {
  const cores = useCores();
  return (
    <Pressionavel
      onPress={onPress}
      vibrar
      escala={0.95}
      accessibilityLabel={`Anexar: ${rotulo}`}
      style={{ flex: 1, height: 64, borderRadius: raios.md, borderWidth: 1.5, borderStyle: 'dashed', borderColor: cores.borda, alignItems: 'center', justifyContent: 'center', gap: 4, backgroundColor: cores.superficie }}>
      <Icone nome={icone} tamanho={20} cor={marca[500]} />
      <Texto variante="legenda" tom="suave">
        {rotulo}
      </Texto>
    </Pressionavel>
  );
}
