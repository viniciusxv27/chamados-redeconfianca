/**
 * Nova publicação no Feed da rede: foto ou vídeo (da galeria ou na hora, pela câmera), descrição
 * opcional e Publicar. Os limites são os do portal (social/servicos.py): foto até 20 MB, vídeo até 100 MB.
 */
import * as Haptics from 'expo-haptics';
import { Image } from 'expo-image';
import * as ImagePicker from 'expo-image-picker';
import { router } from 'expo-router';
import { useVideoPlayer, VideoView } from 'expo-video';
import { useState } from 'react';
import { KeyboardAvoidingView, Platform, ScrollView, TextInput, View } from 'react-native';
import Animated, { FadeIn } from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { COR_FEED } from '@/components/social/post-social';
import { BarraDetalhe, BotaoGradiente, Icone, type NomeIcone, Pressionavel, Tela, Texto } from '@/components/ui';
import { estado, fontes, marca, raios } from '@/constants/theme';
import { usePublicarPost } from '@/hooks/consultas';
import { mensagemDoErro } from '@/services/api';
import { useCores } from '@/providers/tema';

const LIMITE_FOTO = 20 * 1024 * 1024;
const LIMITE_VIDEO = 100 * 1024 * 1024;

type Escolhido = { uri: string; nome: string; tipo: string; video: boolean; largura?: number; altura?: number; tamanho?: number };

function PreviaVideo({ uri, proporcao }: { uri: string; proporcao: number }) {
  const player = useVideoPlayer(uri, (p) => {
    p.loop = true;
    p.muted = true;
    p.play();
  });
  return <VideoView player={player} nativeControls contentFit="contain" style={{ width: '100%', aspectRatio: proporcao, backgroundColor: '#000' }} />;
}

export default function NovaPublicacao() {
  const cores = useCores();
  const insets = useSafeAreaInsets();
  const publicar = usePublicarPost();
  const [escolhido, setEscolhido] = useState<Escolhido | null>(null);
  const [legenda, setLegenda] = useState('');
  const [aviso, setAviso] = useState('');

  async function escolher(origem: 'galeria' | 'foto' | 'video') {
    setAviso('');
    const camera = origem !== 'galeria';
    const permissao = camera ? await ImagePicker.requestCameraPermissionsAsync() : await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!permissao.granted) {
      setAviso(camera ? 'Libere a câmera nas configurações do celular para fotografar ou filmar.' : 'Libere o acesso às fotos nas configurações do celular.');
      return;
    }
    const opcoes: ImagePicker.ImagePickerOptions = {
      mediaTypes: origem === 'galeria' ? ['images', 'videos'] : origem === 'foto' ? ['images'] : ['videos'],
      quality: 0.8,
      videoMaxDuration: 180,
    };
    const r = camera ? await ImagePicker.launchCameraAsync(opcoes) : await ImagePicker.launchImageLibraryAsync(opcoes);
    if (r.canceled || !r.assets?.length) return;
    const a = r.assets[0];
    const video = a.type === 'video' || (a.mimeType ?? '').startsWith('video/');
    const limite = video ? LIMITE_VIDEO : LIMITE_FOTO;
    if (a.fileSize && a.fileSize > limite) {
      setAviso(`Arquivo grande demais: o limite é ${video ? '100 MB para vídeo' : '20 MB para foto'}.`);
      return;
    }
    const extensao = video ? 'mp4' : 'jpg';
    const nome = a.fileName || `${video ? 'video' : 'foto'}-${Date.now()}.${extensao}`;
    setEscolhido({
      uri: a.uri,
      nome: /\.[a-z0-9]{2,5}$/i.test(nome) ? nome : `${nome}.${extensao}`,
      tipo: a.mimeType || (video ? 'video/mp4' : 'image/jpeg'),
      video,
      largura: a.width || undefined,
      altura: a.height || undefined,
      tamanho: a.fileSize,
    });
  }

  function enviar() {
    if (!escolhido || publicar.isPending) return;
    publicar.mutate(
      { uri: escolhido.uri, nome: escolhido.nome, tipo: escolhido.tipo, legenda: legenda.trim(), largura: escolhido.largura, altura: escolhido.altura },
      {
        onSuccess: () => {
          if (Platform.OS !== 'web') void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
          router.back();
        },
      },
    );
  }

  // Mesma faixa do portal: entre 4:5 em pé e 1,91:1 deitado.
  const proporcao = escolhido?.largura && escolhido.altura ? Math.min(1.91, Math.max(0.8, escolhido.largura / escolhido.altura)) : 1;

  return (
    <Tela>
      <BarraDetalhe titulo="Nova publicação" />
      <KeyboardAvoidingView className="flex-1" behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        <ScrollView contentContainerStyle={{ padding: 20, paddingTop: 8, gap: 18, paddingBottom: 24 }} keyboardShouldPersistTaps="handled">
          {escolhido ? (
            <Animated.View entering={FadeIn} style={{ borderRadius: raios.lg, overflow: 'hidden', backgroundColor: cores.superficie2 }}>
              {escolhido.video ? (
                <PreviaVideo uri={escolhido.uri} proporcao={proporcao} />
              ) : (
                <Image source={{ uri: escolhido.uri }} style={{ width: '100%', aspectRatio: proporcao }} contentFit="cover" />
              )}
              <Pressionavel
                onPress={() => setEscolhido(null)}
                disabled={publicar.isPending}
                accessibilityLabel="Trocar a foto ou o vídeo"
                style={{ position: 'absolute', top: 10, right: 10, width: 36, height: 36, borderRadius: 18, backgroundColor: 'rgba(0,0,0,0.55)', alignItems: 'center', justifyContent: 'center' }}>
                <Icone nome="close" tamanho={20} cor="#fff" />
              </Pressionavel>
            </Animated.View>
          ) : (
            <View className="gap-3">
              <Texto tom="suave">Escolha o que vai compartilhar com a rede:</Texto>
              <Opcao icone="images" titulo="Da galeria" texto="Uma foto ou um vídeo do celular" onPress={() => void escolher('galeria')} />
              <Opcao icone="camera" titulo="Tirar foto" texto="Abre a câmera agora" onPress={() => void escolher('foto')} />
              <Opcao icone="videocam" titulo="Gravar vídeo" texto="Até 3 minutos (100 MB)" onPress={() => void escolher('video')} />
            </View>
          )}

          {aviso ? (
            <View className="flex-row items-center gap-2">
              <Icone nome="alert-circle" tamanho={16} cor={estado.alerta} />
              <Texto variante="legenda" style={{ color: estado.alerta, flex: 1 }}>
                {aviso}
              </Texto>
            </View>
          ) : null}

          <View className="gap-2">
            <Texto variante="legenda" tom="suave" style={{ fontFamily: fontes.seminegrito, fontSize: 14 }}>
              Descrição (opcional)
            </Texto>
            <TextInput
              value={legenda}
              onChangeText={setLegenda}
              placeholder="Conte o que está acontecendo…"
              placeholderTextColor={cores.texto3}
              multiline
              maxLength={2200}
              selectionColor={marca[500]}
              accessibilityLabel="Descrição"
              style={{ minHeight: 96, borderRadius: raios.md, borderWidth: 1, borderColor: cores.borda, padding: 14, color: cores.texto, fontFamily: fontes.media, fontSize: 15, backgroundColor: cores.superficie, textAlignVertical: 'top', ...(Platform.OS === 'web' ? { outlineWidth: 0 } : null) }}
            />
          </View>

          {publicar.isError ? (
            <View className="flex-row items-center gap-2">
              <Icone nome="alert-circle" tamanho={16} cor={estado.perigo} />
              <Texto variante="legenda" tom="perigo" style={{ flex: 1 }}>
                {mensagemDoErro(publicar.error)}
              </Texto>
            </View>
          ) : null}
        </ScrollView>
        <View style={{ paddingHorizontal: 20, paddingTop: 10, paddingBottom: Math.max(insets.bottom, 14), borderTopWidth: 1, borderTopColor: cores.bordaSuave }}>
          <BotaoGradiente
            titulo={publicar.isPending ? (escolhido?.video ? 'Enviando o vídeo…' : 'Publicando…') : 'Publicar'}
            onPress={enviar}
            icone="paper-plane"
            desativado={!escolhido || publicar.isPending}
            carregando={publicar.isPending}
          />
        </View>
      </KeyboardAvoidingView>
    </Tela>
  );
}

function Opcao({ icone, titulo, texto, onPress }: { icone: NomeIcone; titulo: string; texto: string; onPress: () => void }) {
  const cores = useCores();
  return (
    <Pressionavel
      onPress={onPress}
      escala={0.98}
      vibrar
      accessibilityLabel={titulo}
      style={{ flexDirection: 'row', alignItems: 'center', gap: 14, borderRadius: raios.lg, borderWidth: 1, borderColor: cores.borda, backgroundColor: cores.superficie, padding: 16 }}>
      <View style={{ width: 44, height: 44, borderRadius: 14, backgroundColor: '#FFF1EB', alignItems: 'center', justifyContent: 'center' }}>
        <Icone nome={icone} tamanho={22} cor={COR_FEED[0]} />
      </View>
      <View className="flex-1">
        <Texto variante="corpoForte">{titulo}</Texto>
        <Texto variante="legenda" tom="fraco">
          {texto}
        </Texto>
      </View>
      <Icone nome="chevron-forward" tamanho={18} cor={cores.texto3} />
    </Pressionavel>
  );
}
