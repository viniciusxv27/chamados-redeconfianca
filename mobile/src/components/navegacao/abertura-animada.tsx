/**
 * Abertura do app com a marca.
 *
 * Começa EXATAMENTE como a splash nativa termina (símbolo branco de 96 pt no
 * centro, fundo #0A0A0A — ver app.json), então a troca não pisca. Daí:
 *   1. o símbolo encolhe para dentro do quadrado escuro do portal (o mesmo do
 *      login web) e um anel laranja pulsa em volta;
 *   2. o nome "Rede Confiança" sobe por baixo;
 *   3. quando a sessão já foi conferida (e passou o tempo mínimo), tudo some
 *      num zoom suave revelando a tela de baixo, que faz a própria entrada.
 * Com "reduzir movimento" ligado no aparelho, vira só um esmaecer rápido.
 */
import { Image } from 'expo-image';
import { LinearGradient } from 'expo-linear-gradient';
import * as SplashScreen from 'expo-splash-screen';
import { useEffect, useState } from 'react';
import { StyleSheet, useWindowDimensions, View } from 'react-native';
import Animated, {
  Easing,
  Extrapolation,
  interpolate,
  useAnimatedStyle,
  useReducedMotion,
  useSharedValue,
  withDelay,
  withRepeat,
  withSpring,
  withTiming,
} from 'react-native-reanimated';

import { Texto } from '@/components/ui';
import { estado, marca } from '@/constants/theme';

const SIMBOLO = 96; // = imageWidth da splash nativa (app.json)
const PROPORCAO = 464 / 440; // altura / largura do logo-simbolo.png
const QUADRO = 116;
const NO_QUADRO = 0.58; // o símbolo dentro do quadrado escuro

export function AberturaAnimada({ pronto, aoSair, aoTerminar }: { pronto: boolean; aoSair: () => void; aoTerminar: () => void }) {
  const reduzido = useReducedMotion();
  const { height } = useWindowDimensions();
  const entrada = useSharedValue(0);
  const anel = useSharedValue(0);
  const nome = useSharedValue(0);
  const saida = useSharedValue(0);
  const [tempoMinimo, setTempoMinimo] = useState(false);

  useEffect(() => {
    if (reduzido) {
      entrada.set(1);
      nome.set(withTiming(1, { duration: 200 }));
    } else {
      entrada.set(withDelay(90, withSpring(1, { damping: 15, stiffness: 120, mass: 0.9 })));
      anel.set(withDelay(320, withRepeat(withTiming(1, { duration: 1150, easing: Easing.out(Easing.cubic) }), 2, false)));
      nome.set(withDelay(420, withTiming(1, { duration: 560, easing: Easing.out(Easing.cubic) })));
    }
    const t = setTimeout(() => setTempoMinimo(true), reduzido ? 350 : 1350);
    return () => clearTimeout(t);
  }, [reduzido, entrada, anel, nome]);

  useEffect(() => {
    if (!pronto || !tempoMinimo) return;
    aoSair();
    saida.set(withTiming(1, { duration: reduzido ? 220 : 560, easing: Easing.inOut(Easing.cubic) }));
    const t = setTimeout(aoTerminar, reduzido ? 240 : 580);
    return () => clearTimeout(t);
  }, [pronto, tempoMinimo, reduzido, saida, aoSair, aoTerminar]);

  const fundo = useAnimatedStyle(() => ({ opacity: interpolate(saida.get(), [0.3, 1], [1, 0], Extrapolation.CLAMP) }));
  const brilho = useAnimatedStyle(() => ({ opacity: interpolate(entrada.get(), [0, 1], [0, 1], Extrapolation.CLAMP) }));
  const grupo = useAnimatedStyle(() => ({
    opacity: interpolate(saida.get(), [0, 0.55], [1, 0], Extrapolation.CLAMP),
    transform: [{ scale: interpolate(saida.get(), [0, 1], [1, 1.12]) }],
  }));
  const quadro = useAnimatedStyle(() => ({
    opacity: interpolate(entrada.get(), [0, 0.6], [0, 1], Extrapolation.CLAMP),
    transform: [{ scale: interpolate(entrada.get(), [0, 1], [0.55, 1]) }],
  }));
  const simbolo = useAnimatedStyle(() => ({ transform: [{ scale: interpolate(entrada.get(), [0, 1], [1, NO_QUADRO]) }] }));
  const anelEstilo = useAnimatedStyle(() => ({
    opacity: interpolate(anel.get(), [0, 0.12, 1], [0, 0.55, 0], Extrapolation.CLAMP),
    transform: [{ scale: interpolate(anel.get(), [0, 1], [1, 2.4]) }],
  }));
  const nomeEstilo = useAnimatedStyle(() => ({
    opacity: nome.get(),
    transform: [{ translateY: interpolate(nome.get(), [0, 1], [14, 0]) }],
  }));

  return (
    <Animated.View
      pointerEvents={pronto && tempoMinimo ? 'none' : 'auto'}
      style={[StyleSheet.absoluteFill, { backgroundColor: estado.tinta, zIndex: 1000 }, fundo]}
      onLayout={() => void SplashScreen.hideAsync()}
      accessibilityLabel="Abrindo o Rede Confiança">
      <Animated.View style={[StyleSheet.absoluteFill, brilho]}>
        <LinearGradient colors={[`${marca[700]}55`, `${marca[900]}1A`, 'transparent']} locations={[0, 0.45, 0.85]} style={StyleSheet.absoluteFill} />
      </Animated.View>

      <Animated.View style={[StyleSheet.absoluteFill, { alignItems: 'center', justifyContent: 'center' }, grupo]}>
        {/* anel laranja pulsando */}
        <Animated.View style={[{ position: 'absolute', width: QUADRO, height: QUADRO, borderRadius: 32, borderWidth: 2, borderColor: marca[500] }, anelEstilo]} />
        {/* quadrado escuro do portal */}
        <Animated.View
          style={[{ position: 'absolute', width: QUADRO, height: QUADRO, borderRadius: 30, backgroundColor: '#1F2937', boxShadow: '0px 18px 40px rgba(0,0,0,0.45)' }, quadro]}
        />
        <Animated.View style={simbolo}>
          <Image source={require('@/assets/images/logo-simbolo.png')} style={{ width: SIMBOLO, height: SIMBOLO * PROPORCAO }} contentFit="contain" />
        </Animated.View>

        <Animated.View style={[{ position: 'absolute', top: height / 2 + QUADRO / 2 + 26, left: 0, right: 0, alignItems: 'center', gap: 6 }, nomeEstilo]}>
          <Texto variante="titulo1" tom="branco">
            Rede Confiança
          </Texto>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            <View style={{ width: 18, height: 2, borderRadius: 1, backgroundColor: marca[500] }} />
            <Texto variante="rotulo" tom="branco" style={{ opacity: 0.6, letterSpacing: 2 }}>
              Portal do colaborador
            </Texto>
            <View style={{ width: 18, height: 2, borderRadius: 1, backgroundColor: marca[500] }} />
          </View>
        </Animated.View>
      </Animated.View>
    </Animated.View>
  );
}
