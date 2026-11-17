import { useEffect } from 'react';
import { View, type DimensionValue, type StyleProp, type ViewStyle } from 'react-native';
import Animated, { useAnimatedStyle, useSharedValue, withRepeat, withTiming, Easing } from 'react-native-reanimated';

import { raios } from '@/constants/theme';
import { useCores } from '@/providers/tema';

import { Cartao } from './cartao';

/** Bloco pulsante no lugar do conteúdo que ainda vai chegar. */
export function Esqueleto({ largura = '100%', altura = 14, raio = 8, style }: { largura?: DimensionValue; altura?: number; raio?: number; style?: StyleProp<ViewStyle> }) {
  const cores = useCores();
  const brilho = useSharedValue(0.55);
  useEffect(() => {
    brilho.set(withRepeat(withTiming(1, { duration: 750, easing: Easing.inOut(Easing.quad) }), -1, true));
  }, [brilho]);
  const animado = useAnimatedStyle(() => ({ opacity: brilho.get() }));
  return <Animated.View style={[{ width: largura, height: altura, borderRadius: raio, backgroundColor: cores.superficie3 }, animado, style]} />;
}

/** Esqueleto de um cartão de lista (avatar + duas linhas + selo). */
export function EsqueletoCartao() {
  return (
    <Cartao style={{ padding: 16, gap: 12 }}>
      <View className="flex-row items-center gap-3">
        <Esqueleto largura={36} altura={36} raio={18} />
        <View className="flex-1 gap-2">
          <Esqueleto largura="70%" />
          <Esqueleto largura="40%" altura={11} />
        </View>
      </View>
      <View className="flex-row gap-2">
        <Esqueleto largura={90} altura={22} raio={raios.pilula} />
        <Esqueleto largura={64} altura={22} raio={raios.pilula} />
      </View>
    </Cartao>
  );
}

export function EsqueletoLista({ quantos = 5 }: { quantos?: number }) {
  return (
    <View className="gap-3">
      {Array.from({ length: quantos }, (_, i) => (
        <EsqueletoCartao key={i} />
      ))}
    </View>
  );
}
