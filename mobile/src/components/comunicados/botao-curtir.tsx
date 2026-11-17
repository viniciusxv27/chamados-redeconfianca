import * as Haptics from 'expo-haptics';
import { Platform } from 'react-native';
import Animated, { useAnimatedStyle, useSharedValue, withSequence, withSpring } from 'react-native-reanimated';

import { Icone, Pressionavel, Texto } from '@/components/ui';
import { estado } from '@/constants/theme';
import { useCurtir } from '@/hooks/consultas';
import { useCores } from '@/providers/tema';

/** Coração que "pula" ao curtir — a curtida aparece na hora e confirma com o portal. */
export function BotaoCurtir({ id, curti, curtidas }: { id: number; curti: boolean; curtidas: number }) {
  const cores = useCores();
  const curtir = useCurtir();
  const pulo = useSharedValue(1);
  const estilo = useAnimatedStyle(() => ({ transform: [{ scale: pulo.get() }] }));

  return (
    <Pressionavel
      hitSlop={10}
      accessibilityLabel={curti ? 'Descurtir' : 'Curtir'}
      accessibilityState={{ selected: curti }}
      onPress={() => {
        if (!curti) {
          pulo.set(withSequence(withSpring(1.35, { damping: 8, stiffness: 400 }), withSpring(1, { damping: 12 })));
          if (Platform.OS !== 'web') void Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
        }
        curtir.mutate(id);
      }}
      style={{ flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 6, paddingVertical: 4 }}>
      <Animated.View style={estilo}>
        <Icone nome={curti ? 'heart' : 'heart-outline'} tamanho={20} cor={curti ? estado.perigo : cores.texto3} />
      </Animated.View>
      {curtidas > 0 ? (
        <Texto variante="legenda" tom={curti ? 'perigo' : 'fraco'}>
          {curtidas}
        </Texto>
      ) : null}
    </Pressionavel>
  );
}
