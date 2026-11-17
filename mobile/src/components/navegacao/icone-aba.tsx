import { useEffect } from 'react';
import { View } from 'react-native';
import Animated, { useAnimatedStyle, useSharedValue, withSpring } from 'react-native-reanimated';

import { Icone, type NomeIcone, Texto } from '@/components/ui';
import { estado, fontes, marca, movimento } from '@/constants/theme';
import { useCores } from '@/providers/tema';
import { comAlfa } from '@/utils/formatar';

/**
 * Ícone da barra de abas: ao ficar ativo, a pílula laranja cresce por trás e o
 * ícone dá um pulinho (mola). Badge vermelho para não lidos.
 */
export function IconeAba({ nome, nomeAtivo, ativo, cor, badge }: { nome: NomeIcone; nomeAtivo: NomeIcone; ativo: boolean; cor: string; badge?: number }) {
  const cores = useCores();
  const progresso = useSharedValue(ativo ? 1 : 0);
  useEffect(() => {
    progresso.set(withSpring(ativo ? 1 : 0, movimento.mola));
  }, [ativo, progresso]);

  const pilula = useAnimatedStyle(() => ({
    opacity: progresso.get(),
    transform: [{ scaleX: 0.5 + progresso.get() * 0.5 }],
  }));
  const icone = useAnimatedStyle(() => ({ transform: [{ translateY: -progresso.get() * 1.5 }, { scale: 1 + progresso.get() * 0.06 }] }));

  return (
    <View style={{ width: 56, height: 30, alignItems: 'center', justifyContent: 'center' }}>
      <Animated.View style={[{ position: 'absolute', width: 56, height: 30, borderRadius: 15, backgroundColor: comAlfa(marca[500], 0.14) }, pilula]} />
      <Animated.View style={icone}>
        <Icone nome={ativo ? nomeAtivo : nome} tamanho={22} cor={cor} />
      </Animated.View>
      {badge ? (
        <View style={{ position: 'absolute', top: -4, right: 6, minWidth: 18, height: 18, borderRadius: 9, paddingHorizontal: 4, backgroundColor: estado.perigo, alignItems: 'center', justifyContent: 'center', borderWidth: 2, borderColor: cores.superficie }}>
          <Texto style={{ color: '#fff', fontFamily: fontes.negrito, fontSize: 10, lineHeight: 12 }}>{badge > 99 ? '99+' : badge}</Texto>
        </View>
      ) : null}
    </View>
  );
}
