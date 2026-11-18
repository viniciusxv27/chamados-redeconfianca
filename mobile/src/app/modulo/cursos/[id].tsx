import * as DocumentPicker from 'expo-document-picker';
import * as Haptics from 'expo-haptics';
import * as ImagePicker from 'expo-image-picker';
import { useLocalSearchParams } from 'expo-router';
import * as WebBrowser from 'expo-web-browser';
import { useState } from 'react';
import { Alert, Platform, View } from 'react-native';
import Animated, { FadeIn, FadeInDown, FadeOut, LinearTransition } from 'react-native-reanimated';

import { TelaDetalhe } from '@/components/modulos/contracheque/tela-detalhe';
import { abrirArquivo } from '@/components/modulos/comum';
import { COR_SITUACAO, dataCurta, ICONE_SITUACAO, mesReferencia, seloDoCurso } from '@/components/modulos/cursos/pecas';
import { Botao, Cartao, Icone, type NomeIcone, Pressionavel, Selo, Texto } from '@/components/ui';
import { estado, fontes, marca, raios } from '@/constants/theme';
import { useCurso, useEnviarComprovante } from '@/hooks/modulos/cursos';
import { useCores } from '@/providers/tema';
import { mensagemDoErro } from '@/services/api';
import type { ArquivoComprovante, CursoDetalhe, EnvioCurso } from '@/services/modulos/cursos';
import { comAlfa, dataHora, tamanhoArquivo } from '@/utils/formatar';

export default function DetalheCurso() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const numero = Number(id);
  const consulta = useCurso(numero);
  const { data } = consulta;

  return (
    <TelaDetalhe
      titulo={data?.titulo ?? 'Curso'}
      caminhoWeb={data?.caminho_web}
      carregando={consulta.isPending}
      erro={consulta.error}
      aoAtualizar={consulta.refetch}
      atualizando={consulta.isRefetching}>
      {data ? <Conteudo curso={data} recarregar={consulta.refetch} /> : null}
    </TelaDetalhe>
  );
}

function prazoEmPalavras(c: CursoDetalhe) {
  if (c.entregue) return `Prazo ${dataCurta(c.prazo)}`;
  if (c.dias > 1) return `Prazo ${dataCurta(c.prazo)} · faltam ${c.dias} dias`;
  if (c.dias === 1) return `Prazo ${dataCurta(c.prazo)} · vence amanhã`;
  if (c.dias === 0) return `Prazo ${dataCurta(c.prazo)} · vence hoje`;
  return `Prazo ${dataCurta(c.prazo)} · venceu há ${-c.dias} dia${c.dias === -1 ? '' : 's'}`;
}

function Conteudo({ curso: c, recarregar }: { curso: CursoDetalhe; recarregar: () => Promise<{ data?: CursoDetalhe }> }) {
  const cores = useCores();
  const selo = seloDoCurso(c);
  const [abrindo, setAbrindo] = useState<number | null>(null);
  const [erroAbrir, setErroAbrir] = useState<string | null>(null);

  async function abrirEnvio(envio: EnvioCurso) {
    setErroAbrir(null);
    setAbrindo(envio.id);
    try {
      // O link assinado vale 5 min: pede o detalhe de novo antes de abrir.
      const { data } = await recarregar();
      const url = data?.envios.find((e) => e.id === envio.id)?.url;
      if (!url) setErroAbrir('O arquivo não está disponível. Tente pelo portal.');
      else await abrirArquivo(url);
    } catch (e) {
      setErroAbrir(mensagemDoErro(e));
    } finally {
      setAbrindo(null);
    }
  }

  return (
    <>
      <Animated.View entering={FadeInDown.duration(320)}>
        <Cartao style={{ padding: 18, gap: 12, borderLeftWidth: 4, borderLeftColor: selo.cor }}>
          <View className="flex-row items-center justify-between gap-3">
            <Texto variante="rotulo" tom="fraco" className="flex-1" numberOfLines={1}>
              {c.tipo_nome}
            </Texto>
            <Selo texto={selo.texto} cor={selo.cor} />
          </View>
          <Texto variante="titulo2" selectable>
            {c.titulo}
          </Texto>
          {c.competencia ? (
            <Texto variante="legenda" tom="fraco">
              Referente a {mesReferencia(c.competencia)}
            </Texto>
          ) : null}
          <View className="flex-row items-center gap-1.5">
            <Icone nome="calendar-outline" tamanho={15} cor={c.atrasado ? estado.perigo : cores.texto3} />
            <Texto variante="legenda" tom={c.atrasado ? 'perigo' : 'suave'}>
              {prazoEmPalavras(c)}
            </Texto>
          </View>
          {c.orientacoes ? (
            <View style={{ backgroundColor: cores.superficie2, borderRadius: raios.md, padding: 14 }}>
              <Texto tom="suave" selectable>
                {c.orientacoes}
              </Texto>
            </View>
          ) : null}
          {c.link ? (
            <Botao
              titulo="Abrir o curso na Vivo"
              icone="open-outline"
              onPress={() => void WebBrowser.openBrowserAsync(c.link ?? '', { presentationStyle: WebBrowser.WebBrowserPresentationStyle.PAGE_SHEET })}
            />
          ) : null}
        </Cartao>
      </Animated.View>

      <Situacao curso={c} />

      {c.pode_enviar ? <EnviarComprovante curso={c} /> : null}

      {c.envios.length ? (
        <Animated.View entering={FadeInDown.delay(120).duration(320)}>
          <Cartao style={{ padding: 18, gap: 12 }}>
            <View className="flex-row items-center gap-2">
              <Icone nome="attach-outline" tamanho={18} cor={cores.texto2} />
              <Texto variante="rotulo" tom="suave">
                {c.envios.length === 1 ? 'Comprovante enviado' : 'Comprovantes enviados'}
              </Texto>
            </View>
            {c.envios.map((e) => (
              <LinhaEnvio key={e.id} envio={e} abrindo={abrindo === e.id} aoAbrir={() => void abrirEnvio(e)} />
            ))}
            {erroAbrir ? (
              <Texto variante="legenda" tom="perigo">
                {erroAbrir}
              </Texto>
            ) : null}
          </Cartao>
        </Animated.View>
      ) : null}
    </>
  );
}

/** O recado do momento: na fila, recusado ou aprovado (meus_cursos.html). */
function Situacao({ curso: c }: { curso: CursoDetalhe }) {
  const envio = c.ultimo_envio;
  let texto: string | null = null;
  if (c.situacao === 'CONFERIR') texto = 'Comprovante enviado. Está na fila do gestor: você é avisado quando ele aprovar.';
  else if (c.situacao === 'RECUSADO') texto = `Comprovante recusado. ${envio?.observacao ? `${envio.observacao} ` : ''}Envie outro arquivo.`;
  else if (c.situacao === 'APROVADO') texto = 'Comprovante aprovado. Este curso está em dia.';
  else if (c.atrasado) texto = c.trava_portal ? 'O prazo venceu. O portal fica travado até o comprovante chegar.' : 'O prazo venceu. Envie o comprovante assim que fizer o curso.';
  if (!texto) return null;
  const cor = c.atrasado && c.situacao === 'NAO_FEZ' ? estado.perigo : COR_SITUACAO[c.situacao];
  return (
    <Animated.View entering={FadeInDown.delay(60).duration(320)}>
      <View style={{ flexDirection: 'row', gap: 10, padding: 16, borderRadius: raios.lg, backgroundColor: comAlfa(cor, 0.1), borderWidth: 1, borderColor: comAlfa(cor, 0.3) }}>
        <Icone nome={c.atrasado && c.situacao === 'NAO_FEZ' ? 'warning-outline' : ICONE_SITUACAO[c.situacao]} tamanho={20} cor={cor} />
        <Texto variante="legenda" style={{ color: cor, flex: 1, fontFamily: fontes.seminegrito }}>
          {texto}
        </Texto>
      </View>
    </Animated.View>
  );
}

function LinhaEnvio({ envio: e, abrindo, aoAbrir }: { envio: EnvioCurso; abrindo: boolean; aoAbrir: () => void }) {
  const cores = useCores();
  const cor = e.status === 'APROVADO' ? estado.sucesso : e.status === 'RECUSADO' ? estado.perigo : estado.info;
  return (
    <Pressionavel
      onPress={aoAbrir}
      disabled={!e.url || abrindo}
      accessibilityLabel={`Abrir ${e.nome}`}
      style={{ flexDirection: 'row', alignItems: 'center', gap: 12, padding: 12, borderRadius: raios.md, backgroundColor: cores.superficie2 }}>
      <Icone nome="document-outline" tamanho={20} cor={marca[500]} />
      <View className="flex-1 gap-1">
        <Texto variante="legenda" numberOfLines={1} style={{ fontFamily: fontes.seminegrito }}>
          {e.nome}
        </Texto>
        <Texto variante="legenda" tom="fraco" style={{ fontSize: 11 }}>
          {dataHora(e.enviado_em)}
          {e.tamanho ? ` · ${tamanhoArquivo(e.tamanho)}` : ''}
        </Texto>
        {e.status === 'RECUSADO' && e.observacao ? (
          <Texto variante="legenda" tom="perigo" style={{ fontSize: 11 }}>
            {e.observacao}
          </Texto>
        ) : null}
      </View>
      <View className="items-end gap-1">
        <Selo texto={e.status_nome} cor={cor} ponto={false} />
        <Texto variante="legenda" tom="marca" style={{ fontSize: 11 }}>
          {abrindo ? 'Abrindo…' : e.url ? 'Abrir' : 'Sem arquivo'}
        </Texto>
      </View>
    </Pressionavel>
  );
}

function EnviarComprovante({ curso: c }: { curso: CursoDetalhe }) {
  const cores = useCores();
  const enviar = useEnviarComprovante(c.id);
  const [arquivo, setArquivo] = useState<ArquivoComprovante | null>(null);
  const [aviso, setAviso] = useState<string | null>(null);
  const limiteMb = Math.round(c.tamanho_maximo / 1024 / 1024);

  function escolher(a: ArquivoComprovante) {
    enviar.reset();
    setAviso(null);
    if ((a.tamanho ?? 0) > c.tamanho_maximo) {
      Alert.alert('Arquivo grande demais', `O limite é ${limiteMb} MB. Tire um print ou uma foto da tela do certificado.`);
      return;
    }
    setArquivo(a);
  }

  async function foto(camera: boolean) {
    const permissao = camera ? await ImagePicker.requestCameraPermissionsAsync() : await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!permissao.granted) {
      Alert.alert('Sem permissão', camera ? 'Libere a câmera nas configurações para fotografar.' : 'Libere as fotos nas configurações para anexar.');
      return;
    }
    const opcoes: ImagePicker.ImagePickerOptions = { mediaTypes: ['images'], quality: 0.7 };
    const r = camera ? await ImagePicker.launchCameraAsync(opcoes) : await ImagePicker.launchImageLibraryAsync(opcoes);
    if (r.canceled || !r.assets[0]) return;
    const a = r.assets[0];
    escolher({ uri: a.uri, nome: a.fileName || `comprovante-${Date.now()}.jpg`, tipo: a.mimeType ?? 'image/jpeg', tamanho: a.fileSize });
  }

  async function documento() {
    // A web aceita qualquer arquivo (cursos/views.py: sem trava de tipo, só tamanho).
    const r = await DocumentPicker.getDocumentAsync({ copyToCacheDirectory: true, type: '*/*' });
    if (r.canceled || !r.assets[0]) return;
    const a = r.assets[0];
    escolher({ uri: a.uri, nome: a.name, tipo: a.mimeType ?? 'application/octet-stream', tamanho: a.size });
  }

  function mandar() {
    if (!arquivo) return;
    enviar.mutate(arquivo, {
      onSuccess: (d) => {
        if (Platform.OS !== 'web') void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
        setArquivo(null);
        setAviso(d.mensagem ?? 'Comprovante enviado.');
      },
      onError: () => {
        if (Platform.OS !== 'web') void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
      },
    });
  }

  return (
    <Animated.View entering={FadeInDown.delay(90).duration(320)}>
      <Cartao style={{ padding: 18, gap: 14 }}>
        <View className="flex-row items-center gap-2">
          <Icone nome="cloud-upload-outline" tamanho={18} cor={cores.texto2} />
          <Texto variante="rotulo" tom="suave">
            {c.entregue ? 'Enviar outro comprovante' : 'Enviar o comprovante'}
          </Texto>
        </View>
        <Texto variante="legenda" tom="suave">
          Foto ou print do certificado, ou o PDF. Até {limiteMb} MB.
        </Texto>

        {arquivo ? (
          <Animated.View entering={FadeIn} exiting={FadeOut} layout={LinearTransition} className="flex-row items-center gap-3 rounded-md px-3 py-3" style={{ backgroundColor: cores.superficie2 }}>
            <Icone nome={arquivo.tipo.startsWith('image/') ? 'image-outline' : 'document-outline'} tamanho={22} cor={marca[500]} />
            <View className="flex-1">
              <Texto variante="legenda" numberOfLines={1} style={{ fontFamily: fontes.seminegrito }}>
                {arquivo.nome}
              </Texto>
              {arquivo.tamanho ? (
                <Texto variante="legenda" tom="fraco" style={{ fontSize: 11 }}>
                  {tamanhoArquivo(arquivo.tamanho)}
                </Texto>
              ) : null}
            </View>
            <Pressionavel onPress={() => setArquivo(null)} hitSlop={10} accessibilityLabel={`Tirar ${arquivo.nome}`} disabled={enviar.isPending}>
              <Icone nome="close-circle" tamanho={22} cor={cores.texto3} />
            </Pressionavel>
          </Animated.View>
        ) : (
          <View className="flex-row gap-3">
            {Platform.OS !== 'web' ? <BotaoAnexo icone="camera-outline" rotulo="Câmera" onPress={() => void foto(true)} /> : null}
            <BotaoAnexo icone="images-outline" rotulo="Fotos" onPress={() => void foto(false)} />
            <BotaoAnexo icone="document-attach-outline" rotulo="Arquivo" onPress={() => void documento()} />
          </View>
        )}

        {arquivo ? <Botao titulo={enviar.isPending ? 'Enviando…' : 'Enviar comprovante'} icone="paper-plane" tamanho="lg" onPress={mandar} carregando={enviar.isPending} /> : null}

        {enviar.isError ? (
          <Texto variante="legenda" tom="perigo">
            {mensagemDoErro(enviar.error)}
          </Texto>
        ) : null}
        {aviso ? (
          <Animated.View entering={FadeIn} className="flex-row items-center gap-2">
            <Icone nome="checkmark-circle" tamanho={18} cor={estado.sucesso} />
            <Texto variante="legenda" tom="sucesso" className="flex-1">
              {aviso}
            </Texto>
          </Animated.View>
        ) : null}
      </Cartao>
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
      style={{ flex: 1, height: 72, borderRadius: raios.md, borderWidth: 1.5, borderStyle: 'dashed', borderColor: cores.borda, alignItems: 'center', justifyContent: 'center', gap: 6, backgroundColor: cores.superficie }}>
      <Icone nome={icone} tamanho={22} cor={marca[500]} />
      <Texto variante="legenda" tom="suave">
        {rotulo}
      </Texto>
    </Pressionavel>
  );
}
