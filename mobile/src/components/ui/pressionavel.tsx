import * as Haptics from 'expo-haptics';
import type { ReactNode } from 'react';
import { Platform, Pressable, type PressableProps, type StyleProp, type ViewStyle } from 'react-native';
import Animated, { useAnimatedStyle, useSharedValue, withSpring } from 'react-native-reanimated';

import { movimento } from '@/constants/theme';

const AnimatedPressable = Animated.createAnimatedComponent(Pressable);

export type PressionavelProps = Omit<PressableProps, 'style' | 'children'> & {
  children: ReactNode;
  style?: StyleProp<ViewStyle>;
  /** Quanto encolhe no toque (0.97 = 3%). */
  escala?: number;
  /** Vibração leve no toque (iOS/Android). */
  vibrar?: boolean;
};

/**
 * Base de tudo que é tocável: encolhe com mola no toque e volta ao soltar —
 * a resposta é imediata, na thread de UI, mesmo com o JS ocupado.
 */
export function Pressionavel({ children, style, escala = 0.97, vibrar = false, onPressIn, onPressOut, onPress, disabled, ...resto }: PressionavelProps) {
  const s = useSharedValue(1);
  const estilo = useAnimatedStyle(() => ({ transform: [{ scale: s.get() }] }));

  return (
    <AnimatedPressable
      accessibilityRole="button"
      disabled={disabled}
      onPressIn={(e) => {
        s.set(withSpring(escala, movimento.mola));
        onPressIn?.(e);
      }}
      onPressOut={(e) => {
        s.set(withSpring(1, movimento.mola));
        onPressOut?.(e);
      }}
      onPress={(e) => {
        if (vibrar && Platform.OS !== 'web') void Haptics.selectionAsync();
        onPress?.(e);
      }}
      style={[estilo, disabled ? { opacity: 0.5 } : null, style]}
      {...resto}>
      {children}
    </AnimatedPressable>
  );
}
