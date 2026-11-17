import type { ReactNode } from 'react';
import { View, type StyleProp, type ViewStyle } from 'react-native';

import { raios, sombras } from '@/constants/theme';
import { useTema } from '@/providers/tema';

import { Pressionavel } from './pressionavel';

/** Superfície padrão. Com onPress vira tocável (encolhe no toque). */
export function Cartao({ children, onPress, style, acessibilidade }: { children: ReactNode; onPress?: () => void; style?: StyleProp<ViewStyle>; acessibilidade?: string }) {
  const { cores, escuro } = useTema();
  const base: ViewStyle = {
    backgroundColor: cores.superficie,
    borderRadius: raios.lg,
    borderWidth: 1,
    borderColor: escuro ? cores.borda : cores.bordaSuave,
    boxShadow: escuro ? undefined : sombras.cartao,
  };
  if (onPress) {
    return (
      <Pressionavel onPress={onPress} escala={0.985} style={[base, style]} accessibilityLabel={acessibilidade}>
        {children}
      </Pressionavel>
    );
  }
  return <View style={[base, style]}>{children}</View>;
}
