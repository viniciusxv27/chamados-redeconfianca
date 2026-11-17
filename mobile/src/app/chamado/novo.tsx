import * as DocumentPicker from 'expo-document-picker';
import * as Haptics from 'expo-haptics';
import * as ImagePicker from 'expo-image-picker';
import { router } from 'expo-router';
import { useState } from 'react';
import { Alert, KeyboardAvoidingView, Platform, ScrollView, View } from 'react-native';
import Animated, { FadeIn, FadeInDown, FadeOut, LinearTransition } from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { Seletor } from '@/components/ui/seletor';
import { Botao, BotaoIcone, Campo, Icone, Pressionavel, Segmentado, Tela, Texto } from '@/components/ui';
import { estado, marca, raios } from '@/constants/theme';
import { useAbrirChamado, useCategorias, useSetores } from '@/hooks/consultas';
import { ErroApi, mensagemDoErro } from '@/services/api';
import type { Prioridade } from '@/services/tipos';
import { useCores } from '@/providers/tema';
import { tamanhoArquivo } from '@/utils/formatar';

type ArquivoLocal = { uri: string; nome: string; tipo: string; tamanho?: number };

const MAX_ANEXOS = 5;

/** Abrir chamado: as mesmas regras do portal (setor, categoria, Manutenção pede a loja). */
export default function NovoChamado() {
  const cores = useCores();
  const insets = useSafeAreaInsets();
  const setores = useSetores();
  const [setor, setSetor] = useState<number | null>(null);
  const categorias = useCategorias(setor);
  const [categoria, setCategoria] = useState<number | null>(null);
  const [titulo, setTitulo] = useState('');
  const [descricao, setDescricao] = useState('');
  const [prioridade, setPrioridade] = useState<Prioridade>('MEDIA');
  const [local, setLocal] = useState('');
  const [anexos, setAnexos] = useState<ArquivoLocal[]>([]);
  const [erros, setErros] = useState<Record<string, string>>({});
  const abrir = useAbrirChamado();

  const nomeSetor = setores.data?.find((s) => s.id === setor)?.nome ?? '';
  const ehManutencao = /manuten[cç][aã]o/i.test(nomeSetor);

  function limparErro(campo: string) {
    if (erros[campo]) setErros(({ [campo]: _, ...resto }) => resto);
  }

  async function adicionarFoto(camera: boolean) {
    const permissao = camera ? await ImagePicker.requestCameraPermissionsAsync() : await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!permissao.granted) {
      Alert.alert('Sem permissão', camera ? 'Libere a câmera nas configurações para fotografar.' : 'Libere as fotos nas configurações para anexar.');
      return;
    }
    const opcoes: ImagePicker.ImagePickerOptions = { mediaTypes: ['images'], quality: 0.7, allowsMultipleSelection: !camera, selectionLimit: MAX_ANEXOS - anexos.length };
    const r = camera ? await ImagePicker.launchCameraAsync(opcoes) : await ImagePicker.launchImageLibraryAsync(opcoes);
    if (r.canceled) return;
    juntar(r.assets.map((a, i) => ({ uri: a.uri, nome: a.fileName ?? `foto-${Date.now()}-${i}.jpg`, tipo: a.mimeType ?? 'image/jpeg', tamanho: a.fileSize })));
  }

  async function adicionarArquivo() {
    const r = await DocumentPicker.getDocumentAsync({ multiple: true, copyToCacheDirectory: true });
    if (r.canceled) return;
    juntar(r.assets.map((a) => ({ uri: a.uri, nome: a.name, tipo: a.mimeType ?? 'application/octet-stream', tamanho: a.size })));
  }

  function juntar(novos: ArquivoLocal[]) {
    const grandes = novos.filter((a) => (a.tamanho ?? 0) > 50 * 1024 * 1024);
    if (grandes.length) Alert.alert('Arquivo grande demais', 'Cada arquivo pode ter até 50 MB.');
    setAnexos((atual) => [...atual, ...novos.filter((a) => !grandes.includes(a))].slice(0, MAX_ANEXOS));
    limparErro('anexos');
  }

  function validar() {
    const e: Record<string, string> = {};
    if (!setor) e.setor = 'Escolha o setor.';
    if (setor && !categoria) e.categoria = 'Escolha a categoria.';
    if (!titulo.trim()) e.titulo = 'Dê um título ao chamado.';
    if (!descricao.trim()) e.descricao = 'Descreva o que precisa.';
    if (ehManutencao && !local.trim()) e.local = 'Informe a loja/local para a Manutenção.';
    setErros(e);
    return Object.keys(e).length === 0;
  }

  function enviar() {
    if (!validar()) {
      if (Platform.OS !== 'web') void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning);
      return;
    }
    abrir.mutate(
      { titulo: titulo.trim(), descricao: descricao.trim(), setor: setor!, categoria: categoria!, prioridade, local: local.trim() || undefined, anexos },
      {
        onSuccess: (chamado) => {
          if (Platform.OS !== 'web') void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
          router.replace({ pathname: '/chamado/[id]', params: { id: String(chamado.id) } });
        },
        onError: (erro) => {
          if (erro instanceof ErroApi && Object.keys(erro.campos).length) setErros(erro.campos);
          if (Platform.OS !== 'web') void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
        },
      },
    );
  }

  return (
    <Tela>
      <View className="flex-row items-center justify-between px-5 pb-3" style={{ paddingTop: Platform.OS === 'ios' ? 14 : insets.top + 10 }}>
        <View>
          <Texto variante="rotulo" tom="fraco">
            Chamados
          </Texto>
          <Texto variante="titulo2">Novo chamado</Texto>
        </View>
        <BotaoIcone icone="close" rotulo="Fechar" onPress={() => router.back()} />
      </View>

      <KeyboardAvoidingView className="flex-1" behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        <ScrollView contentContainerStyle={{ padding: 20, gap: 18, paddingBottom: 40 }} keyboardShouldPersistTaps="handled">
          <Animated.View entering={FadeInDown.delay(60)} className="gap-4">
            <Seletor
              rotulo="Para qual setor?"
              placeholder="Escolha o setor"
              itens={setores.data ?? []}
              valor={setor}
              carregando={setores.isPending}
              erro={erros.setor ?? (setores.isError ? mensagemDoErro(setores.error) : null)}
              aoEscolher={(id) => {
                setSetor(id);
                setCategoria(null);
                limparErro('setor');
              }}
            />
            {setor ? (
              <Animated.View entering={FadeIn} layout={LinearTransition}>
                <Seletor
                  rotulo="Categoria"
                  placeholder="Escolha a categoria"
                  itens={(categorias.data ?? []).map((c) => ({ id: c.id, nome: c.nome, detalhe: c.horas_solucao ? `Prazo padrão: ${c.horas_solucao} h` : undefined }))}
                  valor={categoria}
                  carregando={categorias.isPending}
                  erro={erros.categoria}
                  aoEscolher={(id) => {
                    setCategoria(id);
                    limparErro('categoria');
                    // Traz a descrição-modelo da categoria, se a pessoa ainda não escreveu nada.
                    const modelo = categorias.data?.find((c) => c.id === id)?.descricao_padrao;
                    if (modelo && !descricao.trim()) {
                      setDescricao(modelo);
                      limparErro('descricao');
                    }
                  }}
                />
              </Animated.View>
            ) : null}
            {ehManutencao ? (
              <Animated.View entering={FadeIn} layout={LinearTransition}>
                <Campo rotulo="Loja / local" icone="location-outline" placeholder="Ex.: Loja Centro" value={local} onChangeText={(v) => (setLocal(v), limparErro('local'))} erro={erros.local} />
              </Animated.View>
            ) : null}
          </Animated.View>

          <Animated.View entering={FadeInDown.delay(120)} layout={LinearTransition} className="gap-4">
            <Campo rotulo="Título" placeholder="Resumo em poucas palavras" value={titulo} onChangeText={(v) => (setTitulo(v), limparErro('titulo'))} erro={erros.titulo} maxLength={200} returnKeyType="next" />
            <Campo rotulo="Descrição" placeholder="Conte o que aconteceu, onde e desde quando." value={descricao} onChangeText={(v) => (setDescricao(v), limparErro('descricao'))} erro={erros.descricao} multiline />
            <View className="gap-1.5">
              <Texto variante="legenda" tom="suave">
                Prioridade
              </Texto>
              <Segmentado
                opcoes={[
                  { valor: 'BAIXA', rotulo: 'Baixa' },
                  { valor: 'MEDIA', rotulo: 'Média' },
                  { valor: 'ALTA', rotulo: 'Alta' },
                  { valor: 'CRITICA', rotulo: 'Crítica' },
                ]}
                valor={prioridade}
                aoMudar={setPrioridade}
              />
            </View>
          </Animated.View>

          <Animated.View entering={FadeInDown.delay(180)} layout={LinearTransition} className="gap-2">
            <Texto variante="legenda" tom="suave">
              Anexos ({anexos.length}/{MAX_ANEXOS})
            </Texto>
            {anexos.map((a, i) => (
              <Animated.View key={`${a.uri}-${i}`} entering={FadeIn} exiting={FadeOut} layout={LinearTransition} className="flex-row items-center gap-3 rounded-md px-3 py-2.5" style={{ backgroundColor: cores.superficie }}>
                <Icone nome={a.tipo.startsWith('image/') ? 'image-outline' : 'document-outline'} tamanho={20} cor={marca[500]} />
                <View className="flex-1">
                  <Texto variante="legenda" numberOfLines={1}>
                    {a.nome}
                  </Texto>
                  {a.tamanho ? (
                    <Texto variante="legenda" tom="fraco" style={{ fontSize: 11 }}>
                      {tamanhoArquivo(a.tamanho)}
                    </Texto>
                  ) : null}
                </View>
                <Pressionavel onPress={() => setAnexos((atual) => atual.filter((_, j) => j !== i))} hitSlop={10} accessibilityLabel={`Remover ${a.nome}`}>
                  <Icone nome="close-circle" tamanho={20} cor={cores.texto3} />
                </Pressionavel>
              </Animated.View>
            ))}
            {anexos.length < MAX_ANEXOS ? (
              <View className="flex-row gap-2">
                {Platform.OS !== 'web' ? <BotaoAnexo icone="camera-outline" rotulo="Câmera" onPress={() => adicionarFoto(true)} /> : null}
                <BotaoAnexo icone="images-outline" rotulo="Fotos" onPress={() => adicionarFoto(false)} />
                <BotaoAnexo icone="document-attach-outline" rotulo="Arquivo" onPress={adicionarArquivo} />
              </View>
            ) : null}
            {erros.anexos ? (
              <Texto variante="legenda" tom="perigo">
                {erros.anexos}
              </Texto>
            ) : null}
          </Animated.View>

          {abrir.isError && !(abrir.error instanceof ErroApi && Object.keys(abrir.error.campos).length) ? (
            <Animated.View entering={FadeIn} className="flex-row items-center gap-2 rounded-md px-3 py-2.5" style={{ backgroundColor: `${estado.perigo}18` }}>
              <Icone nome="alert-circle" tamanho={18} cor={estado.perigo} />
              <Texto variante="legenda" tom="perigo" className="flex-1">
                {mensagemDoErro(abrir.error)}
              </Texto>
            </Animated.View>
          ) : null}
        </ScrollView>

        <View style={{ paddingHorizontal: 20, paddingTop: 12, paddingBottom: Math.max(insets.bottom, 16), borderTopWidth: 1, borderTopColor: cores.borda, backgroundColor: cores.superficie }}>
          <Botao titulo={abrir.isPending ? 'Enviando…' : 'Abrir chamado'} icone="paper-plane" tamanho="lg" onPress={enviar} carregando={abrir.isPending} />
        </View>
      </KeyboardAvoidingView>
    </Tela>
  );
}

function BotaoAnexo({ icone, rotulo, onPress }: { icone: 'camera-outline' | 'images-outline' | 'document-attach-outline'; rotulo: string; onPress: () => void }) {
  const cores = useCores();
  return (
    <Pressionavel
      onPress={onPress}
      vibrar
      escala={0.95}
      style={{ flex: 1, height: 64, borderRadius: raios.md, borderWidth: 1.5, borderStyle: 'dashed', borderColor: cores.borda, alignItems: 'center', justifyContent: 'center', gap: 4, backgroundColor: cores.superficie }}>
      <Icone nome={icone} tamanho={20} cor={marca[500]} />
      <Texto variante="legenda" tom="suave">
        {rotulo}
      </Texto>
    </Pressionavel>
  );
}
