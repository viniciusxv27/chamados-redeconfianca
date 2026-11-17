import * as Haptics from 'expo-haptics';
import { Image } from 'expo-image';
import { useLocalSearchParams } from 'expo-router';
import { useState } from 'react';
import { FlatList, Platform, ScrollView, useWindowDimensions, View } from 'react-native';
import Animated, { FadeIn, FadeInDown, ZoomIn } from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { BotaoCurtir } from '@/components/comunicados/botao-curtir';
import { Avatar, BarraDetalhe, Botao, Esqueleto, EstadoErro, Icone, Selo, Tela, Texto } from '@/components/ui';
import { estado, marca, raios } from '@/constants/theme';
import { useComunicado, useSituacaoComunicado } from '@/hooks/consultas';
import { mensagemDoErro } from '@/services/api';
import { useCores } from '@/providers/tema';
import { dataHora } from '@/utils/formatar';

export default function DetalheComunicado() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const numero = Number(id);
  const cores = useCores();
  const insets = useSafeAreaInsets();
  const { width } = useWindowDimensions();
  const { data, isPending, isError, error, refetch } = useComunicado(numero);
  const situacao = useSituacaoComunicado(numero);
  const [pagina, setPagina] = useState(0);

  function marcar(s: 'ESTOU_CIENTE' | 'ESTOU_COM_DUVIDA') {
    situacao.mutate(s, {
      onSuccess: () => Platform.OS !== 'web' && void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success),
    });
  }

  return (
    <Tela>
      <BarraDetalhe titulo="Comunicado" direita={data ? <BotaoCurtir id={data.id} curti={data.curti} curtidas={data.curtidas} /> : undefined} />
      {isPending ? (
        <View className="gap-4 p-5">
          <Esqueleto altura={200} raio={raios.lg} />
          <Esqueleto largura="80%" altura={24} />
          <Esqueleto />
          <Esqueleto largura="90%" />
          <Esqueleto largura="70%" />
        </View>
      ) : isError || !data ? (
        <EstadoErro mensagem={mensagemDoErro(error)} aoTentar={refetch} />
      ) : (
        <>
          <ScrollView contentContainerStyle={{ paddingBottom: 32 }}>
            {data.imagens.length ? (
              <Animated.View entering={FadeIn.duration(400)}>
                <FlatList
                  data={data.imagens}
                  horizontal
                  pagingEnabled
                  showsHorizontalScrollIndicator={false}
                  keyExtractor={(u, i) => `${i}-${u}`}
                  onMomentumScrollEnd={(e) => setPagina(Math.round(e.nativeEvent.contentOffset.x / width))}
                  renderItem={({ item }) => (
                    <Image source={{ uri: item }} style={{ width, aspectRatio: 4 / 3, backgroundColor: cores.superficie3 }} contentFit="contain" transition={250} />
                  )}
                />
                {data.imagens.length > 1 ? (
                  <View className="flex-row justify-center gap-1.5 py-3">
                    {data.imagens.map((_, i) => (
                      <View key={i} style={{ width: i === pagina ? 18 : 6, height: 6, borderRadius: 3, backgroundColor: i === pagina ? marca[500] : cores.borda }} />
                    ))}
                  </View>
                ) : null}
              </Animated.View>
            ) : null}

            <Animated.View entering={FadeInDown.delay(80).duration(400)} className="gap-4 px-5 pt-4">
              <View className="flex-row flex-wrap gap-2">
                {data.fixado ? <Selo texto="Fixado" cor={marca[500]} ponto={false} /> : null}
                {data.obrigatorio ? <Selo texto="Leitura obrigatória" cor={estado.perigo} /> : null}
              </View>
              <Texto variante="titulo1" selectable style={data.cor_titulo && data.cor_titulo !== '#000000' ? { color: data.cor_titulo } : undefined}>
                {data.titulo}
              </Texto>
              <View className="flex-row items-center gap-3">
                <Avatar nome={data.autor?.nome ?? 'Rede Confiança'} foto={data.autor?.foto} tamanho={36} />
                <View>
                  <Texto variante="corpoForte">{data.autor?.nome ?? 'Rede Confiança'}</Texto>
                  <Texto variante="legenda" tom="fraco">
                    {dataHora(data.criado_em)}
                  </Texto>
                </View>
              </View>
              <Texto selectable style={{ fontSize: 16, lineHeight: 25 }}>
                {data.texto}
              </Texto>
            </Animated.View>
          </ScrollView>

          {/* Confirmação de leitura, como no portal */}
          <View style={{ paddingHorizontal: 20, paddingTop: 12, paddingBottom: Math.max(insets.bottom, 16), borderTopWidth: 1, borderTopColor: cores.borda, backgroundColor: cores.superficie }}>
            {data.situacao === 'ESTOU_CIENTE' ? (
              <Animated.View entering={ZoomIn.springify()} className="flex-row items-center justify-center gap-2 py-2">
                <Icone nome="checkmark-circle" tamanho={22} cor={estado.sucesso} />
                <Texto variante="corpoForte" tom="sucesso">
                  Você confirmou que está ciente
                </Texto>
              </Animated.View>
            ) : (
              <View className="gap-2">
                {data.situacao === 'ESTOU_COM_DUVIDA' ? (
                  <Texto variante="legenda" tom="suave" className="text-center">
                    Você marcou que está com dúvida. Quando entender, confirme abaixo.
                  </Texto>
                ) : null}
                <View className="flex-row gap-3">
                  {data.situacao !== 'ESTOU_COM_DUVIDA' ? (
                    <Botao titulo="Tenho dúvida" variante="secundario" icone="help-circle-outline" onPress={() => marcar('ESTOU_COM_DUVIDA')} style={{ flex: 1 }} desativado={situacao.isPending} />
                  ) : null}
                  <Botao titulo="Estou ciente" icone="checkmark" onPress={() => marcar('ESTOU_CIENTE')} carregando={situacao.isPending} style={{ flex: 1 }} />
                </View>
              </View>
            )}
          </View>
        </>
      )}
    </Tela>
  );
}
