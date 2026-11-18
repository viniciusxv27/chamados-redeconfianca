/**
 * Elogio da home, igual ao cartão do portal: borda em degradê roxo → rosa →
 * laranja, o logo num quadrado roxo/rosa com estrelinhas piscando em volta,
 * "Fulano para Ciclano", as estrelas da nota, a data e o comentário.
 */
import { Image } from 'expo-image';
import { LinearGradient } from 'expo-linear-gradient';
import { useEffect } from 'react';
import { StyleSheet, View } from 'react-native';
import Animated, { Easing, useAnimatedStyle, useReducedMotion, useSharedValue, withDelay, withRepeat, withTiming } from 'react-native-reanimated';

import { Icone, Pressionavel, Texto } from '@/components/ui';
import { fontes, raios } from '@/constants/theme';
import { useCores } from '@/providers/tema';
import type { Elogio } from '@/services/tipos';
import { dataHora } from '@/utils/formatar';
import { abrirNoPortal } from '@/utils/links';

const ROXO = '#9333EA';
const ROSA = '#EC4899';
const LARANJA = '#FB923C';

function poucasPalavras(texto: string, n = 15) {
  const palavras = texto.trim().split(/\s+/);
  return palavras.length > n ? `${palavras.slice(0, n).join(' ')}…` : texto.trim();
}

export function CartaoElogio({ elogio }: { elogio: Elogio }) {
  const cores = useCores();
  const para = elogio.para_pessoa?.nome ?? (elogio.para_setor ? `o setor ${elogio.para_setor}` : '—');
  return (
    <LinearGradient colors={[ROXO, ROSA, LARANJA]} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={{ borderRadius: raios['2xl'], padding: 2 }}>
      <View style={{ backgroundColor: cores.superficie, borderRadius: raios['2xl'] - 2, padding: 18, flexDirection: 'row', gap: 14 }}>
        <Selo />
        <View className="flex-1 gap-1.5">
          <Texto variante="legenda" tom="suave" style={{ fontSize: 14, lineHeight: 20 }}>
            <Texto variante="legenda" style={{ fontFamily: fontes.negrito, fontSize: 14 }}>
              {elogio.de?.nome ?? 'Alguém'}
            </Texto>
            {' para '}
            <Texto variante="legenda" style={{ fontFamily: fontes.negrito, fontSize: 14 }}>
              {para}
            </Texto>
          </Texto>
          <View className="flex-row items-center gap-0.5">
            {[1, 2, 3, 4, 5].map((i) => (
              <Icone key={i} nome="star" tamanho={15} cor={i <= elogio.estrelas ? '#A855F7' : cores.borda} />
            ))}
          </View>
          <Texto variante="legenda" tom="fraco" style={{ fontSize: 11 }}>
            {dataHora(elogio.criado_em)}
          </Texto>
          <Texto tom="suave" style={{ fontSize: 14, lineHeight: 20 }}>
            {poucasPalavras(elogio.comentario)}
          </Texto>
          {elogio.para_mim && elogio.url_web ? (
            <Pressionavel onPress={() => abrirNoPortal(elogio.url_web!)} hitSlop={8} escala={0.95} accessibilityRole="link" style={{ marginTop: 4 }}>
              <Texto variante="rotulo" style={{ color: ROXO, letterSpacing: 1 }}>
                Ver detalhes →
              </Texto>
            </Pressionavel>
          ) : null}
        </View>
      </View>
    </LinearGradient>
  );
}

/** Quadrado roxo/rosa com o logo e 4 estrelinhas piscando em volta (o animate-pulse do portal). */
function Selo() {
  return (
    <View style={{ width: 52, height: 52 }}>
      <LinearGradient colors={['#A855F7', ROSA]} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={[StyleSheet.absoluteFill, { borderRadius: 16, alignItems: 'center', justifyContent: 'center', boxShadow: '0px 6px 14px rgba(168,85,247,0.35)' }]}>
        <Image source={require('@/assets/images/logo-simbolo.png')} style={{ width: 30, height: 30 * (464 / 440) }} contentFit="contain" />
      </LinearGradient>
      <Estrelinha atraso={0} style={{ top: -6, left: -6 }} cor="#C084FC" />
      <Estrelinha atraso={200} style={{ top: -6, right: -6 }} cor="#F472B6" />
      <Estrelinha atraso={400} style={{ bottom: -6, left: -6 }} cor="#D8B4FE" />
      <Estrelinha atraso={600} style={{ bottom: -6, right: -6 }} cor="#F9A8D4" />
    </View>
  );
}

function Estrelinha({ atraso, style, cor }: { atraso: number; style: object; cor: string }) {
  const reduzido = useReducedMotion();
  const brilho = useSharedValue(1);
  useEffect(() => {
    if (reduzido) return;
    brilho.set(withDelay(atraso, withRepeat(withTiming(0.35, { duration: 1000, easing: Easing.inOut(Easing.sin) }), -1, true)));
  }, [brilho, atraso, reduzido]);
  const animado = useAnimatedStyle(() => ({ opacity: brilho.get(), transform: [{ scale: 0.85 + brilho.get() * 0.25 }] }));
  return (
    <Animated.View pointerEvents="none" style={[{ position: 'absolute', zIndex: 2 }, style, animado]}>
      <Icone nome="star" tamanho={12} cor={cor} />
    </Animated.View>
  );
}
