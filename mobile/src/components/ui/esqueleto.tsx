/**
 * Esqueletos: o desenho da tela aparece antes dos dados, com um brilho que
 * corre da esquerda para a direita (como o carregamento dos apps nativos).
 * Todos compartilham UM relógio de animação — os blocos brilham juntos, em
 * onda, e não cada um no seu ritmo.
 */
import { LinearGradient } from 'expo-linear-gradient';
import { createContext, type ReactNode, useContext, useEffect } from 'react';
import { type DimensionValue, StyleSheet, type StyleProp, View, type ViewStyle } from 'react-native';
import Animated, {
  Easing,
  FadeIn,
  type SharedValue,
  interpolate,
  useAnimatedStyle,
  useReducedMotion,
  useSharedValue,
  withRepeat,
  withTiming,
} from 'react-native-reanimated';

import { raios } from '@/constants/theme';
import { useTema } from '@/providers/tema';

import { Cartao } from './cartao';

const RelogioContexto = createContext<SharedValue<number> | null>(null);

/** Um relógio para todos os blocos dentro dele (as telas inteiras usam). */
function useRelogio() {
  const proprio = useSharedValue(0);
  const herdado = useContext(RelogioContexto);
  const reduzido = useReducedMotion();
  const relogio = herdado ?? proprio;
  useEffect(() => {
    if (herdado || reduzido) return;
    proprio.set(withRepeat(withTiming(1, { duration: 1300, easing: Easing.inOut(Easing.quad) }), -1, false));
  }, [herdado, reduzido, proprio]);
  return relogio;
}

/** Envolve um grupo de esqueletos para brilharem em onda, juntos. */
export function GrupoEsqueleto({ children }: { children: ReactNode }) {
  const relogio = useRelogio();
  return <RelogioContexto.Provider value={relogio}>{children}</RelogioContexto.Provider>;
}

/** Bloco com brilho correndo. */
export function Esqueleto({ largura = '100%', altura = 14, raio = 8, style }: { largura?: DimensionValue; altura?: number; raio?: number; style?: StyleProp<ViewStyle> }) {
  const { cores, escuro } = useTema();
  const relogio = useRelogio();
  const brilho = useAnimatedStyle(() => ({
    transform: [{ translateX: interpolate(relogio.get(), [0, 1], [-220, 420]) }],
  }));
  const luz = escuro ? 'rgba(255,255,255,0.07)' : 'rgba(255,255,255,0.75)';
  return (
    <View style={[{ width: largura, height: altura, borderRadius: raio, backgroundColor: cores.superficie3, overflow: 'hidden' }, style]}>
      <Animated.View style={[StyleSheet.absoluteFill, { width: 220 }, brilho]}>
        <LinearGradient colors={['transparent', luz, 'transparent']} start={{ x: 0, y: 0.5 }} end={{ x: 1, y: 0.5 }} style={StyleSheet.absoluteFill} />
      </Animated.View>
    </View>
  );
}

/** Esqueleto de um cartão de lista (avatar + duas linhas + selos). */
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
    <GrupoEsqueleto>
      <Animated.View entering={FadeIn.duration(250)} style={{ gap: 16 }}>
        {Array.from({ length: quantos }, (_, i) => (
          <EsqueletoCartao key={i} />
        ))}
      </Animated.View>
    </GrupoEsqueleto>
  );
}

/** Painel: quatro números em grade + um cartão de destaque + lista curta. */
export function EsqueletoPainel() {
  return (
    <GrupoEsqueleto>
      <Animated.View entering={FadeIn.duration(250)} className="gap-4">
        <View className="flex-row flex-wrap gap-3">
          {[0, 1, 2, 3].map((i) => (
            <Cartao key={i} style={{ flexBasis: '47%', flexGrow: 1, padding: 16, gap: 10 }}>
              <Esqueleto largura={34} altura={34} raio={10} />
              <Esqueleto largura={56} altura={24} />
              <Esqueleto largura="80%" altura={11} />
            </Cartao>
          ))}
        </View>
        <Cartao style={{ padding: 18, gap: 12 }}>
          <Esqueleto largura="45%" altura={16} />
          <Esqueleto altura={10} raio={5} />
          <Esqueleto largura="85%" altura={10} raio={5} />
          <Esqueleto largura="65%" altura={10} raio={5} />
        </Cartao>
        <EsqueletoCartao />
        <EsqueletoCartao />
      </Animated.View>
    </GrupoEsqueleto>
  );
}

/** Detalhe: cabeçalho com selos e título + blocos de texto. */
export function EsqueletoDetalhe() {
  return (
    <GrupoEsqueleto>
      <Animated.View entering={FadeIn.duration(250)} className="gap-4">
        <Cartao style={{ padding: 18, gap: 14 }}>
          <View className="flex-row gap-2">
            <Esqueleto largura={96} altura={24} raio={12} />
            <Esqueleto largura={120} altura={24} raio={12} />
          </View>
          <Esqueleto largura="85%" altura={22} />
          {[0, 1, 2, 3].map((i) => (
            <View key={i} className="flex-row items-center gap-3">
              <Esqueleto largura={18} altura={18} raio={6} />
              <Esqueleto largura={80} altura={11} />
              <Esqueleto largura={`${50 - i * 6}%`} altura={11} />
            </View>
          ))}
        </Cartao>
        <Cartao style={{ padding: 18, gap: 10 }}>
          <Esqueleto largura={100} altura={12} />
          <Esqueleto />
          <Esqueleto largura="90%" />
          <Esqueleto largura="60%" />
        </Cartao>
      </Animated.View>
    </GrupoEsqueleto>
  );
}

/** Fim da lista carregando a próxima página: dois cartões, não um spinner. */
export function EsqueletoRodape() {
  return (
    <View style={{ paddingTop: 12 }}>
      <EsqueletoLista quantos={2} />
    </View>
  );
}
