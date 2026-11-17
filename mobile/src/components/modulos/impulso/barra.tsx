import { useEffect } from 'react';
import { View } from 'react-native';
import Animated, { useAnimatedStyle, useSharedValue, withTiming, Easing } from 'react-native-reanimated';

import { useCores } from '@/providers/tema';

/** Barra de progresso que enche ao aparecer (0–100). */
export function BarraProgresso({ pct, cor, altura = 8 }: { pct: number; cor: string; altura?: number }) {
  const cores = useCores();
  const largura = useSharedValue(0);
  const alvo = Math.max(0, Math.min(100, pct || 0));
  useEffect(() => {
    largura.set(withTiming(alvo, { duration: 700, easing: Easing.out(Easing.cubic) }));
  }, [alvo, largura]);
  const enchimento = useAnimatedStyle(() => ({ width: `${largura.get()}%` }));
  return (
    <View style={{ height: altura, borderRadius: altura / 2, backgroundColor: cores.superficie3, overflow: 'hidden' }}>
      <Animated.View style={[{ height: altura, borderRadius: altura / 2, backgroundColor: cor }, enchimento]} />
    </View>
  );
}
