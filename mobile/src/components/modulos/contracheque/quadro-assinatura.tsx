/**
 * Quadro de assinatura (desenhar com o dedo) + confirmação, como o da web.
 *
 * Usado no Contracheque e nos Documentos. O desenho vira PNG aqui mesmo
 * (png-assinatura.ts) e vai para a mesma função de assinar do portal.
 * Enquanto a pessoa desenha, `aoDesenhar(true)` avisa a tela para travar a
 * rolagem (senão o dedo rola a página em vez de assinar).
 */
import * as Haptics from 'expo-haptics';
import { useState } from 'react';
import { type GestureResponderEvent, type LayoutChangeEvent, Platform, View } from 'react-native';
import Animated, { FadeIn } from 'react-native-reanimated';

import { Botao, Icone, Texto } from '@/components/ui';
import { estado, raios } from '@/constants/theme';
import { useCores } from '@/providers/tema';
import { comAlfa } from '@/utils/formatar';

import { assinaturaEmPng, type Ponto, type Traco } from './png-assinatura';

const ALTURA = 180;
const ESPESSURA = 2.5;
const TINTA = '#1A1A2E';

export function QuadroAssinatura({
  aviso,
  detalhe,
  rotulo,
  enviando,
  erro,
  aoAssinar,
  aoDesenhar,
}: {
  aviso: string;
  detalhe?: string;
  rotulo: string;
  enviando: boolean;
  erro?: string | null;
  aoAssinar: (png: string) => void;
  aoDesenhar?: (desenhando: boolean) => void;
}) {
  const cores = useCores();
  const [tracos, setTracos] = useState<Traco[]>([]);
  const [largura, setLargura] = useState(0);
  const [confirmando, setConfirmando] = useState(false);
  const vazio = tracos.length === 0;

  const ponto = (e: GestureResponderEvent): Ponto => ({
    x: Math.max(0, Math.min(largura, e.nativeEvent.locationX)),
    y: Math.max(0, Math.min(ALTURA, e.nativeEvent.locationY)),
  });

  const comecar = (e: GestureResponderEvent) => {
    aoDesenhar?.(true);
    setConfirmando(false);
    const p = ponto(e);
    setTracos((t) => [...t, [p]]);
  };

  const mover = (e: GestureResponderEvent) => {
    const p = ponto(e);
    setTracos((t) => {
      const atual = t[t.length - 1];
      if (!atual) return t;
      const ultimo = atual[atual.length - 1];
      if (ultimo && Math.hypot(p.x - ultimo.x, p.y - ultimo.y) < 2) return t;
      return [...t.slice(0, -1), [...atual, p]];
    });
  };

  const soltar = () => aoDesenhar?.(false);

  const limpar = () => {
    setTracos([]);
    setConfirmando(false);
  };

  const assinar = () => {
    if (!confirmando) {
      setConfirmando(true);
      return;
    }
    if (Platform.OS !== 'web') void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    aoAssinar(assinaturaEmPng(tracos, largura, ALTURA, ESPESSURA));
  };

  return (
    <View className="gap-3">
      <View style={{ flexDirection: 'row', gap: 10, padding: 12, borderRadius: raios.md, backgroundColor: comAlfa(estado.info, 0.1) }}>
        <Icone nome="information-circle-outline" tamanho={20} cor={estado.info} />
        <View className="flex-1 gap-1">
          <Texto variante="legenda" style={{ color: cores.texto }}>
            {aviso}
          </Texto>
          {detalhe ? (
            <Texto variante="legenda" tom="fraco">
              {detalhe}
            </Texto>
          ) : null}
        </View>
      </View>

      <View
        onLayout={(e: LayoutChangeEvent) => setLargura(e.nativeEvent.layout.width)}
        onStartShouldSetResponder={() => !enviando}
        onMoveShouldSetResponder={() => !enviando}
        onResponderTerminationRequest={() => false}
        onResponderGrant={comecar}
        onResponderMove={mover}
        onResponderRelease={soltar}
        onResponderTerminate={soltar}
        accessibilityLabel="Quadro para desenhar a assinatura"
        style={{
          height: ALTURA,
          borderRadius: raios.md,
          borderWidth: 2,
          borderStyle: 'dashed',
          borderColor: vazio ? cores.borda : comAlfa(TINTA, 0.35),
          // Papel branco nos dois temas: a assinatura é tinta escura, como no PDF.
          backgroundColor: '#FFFFFF',
          overflow: 'hidden',
        }}>
        <View pointerEvents="none" style={{ position: 'absolute', left: 0, top: 0, right: 0, bottom: 0 }}>
          {vazio ? (
            <View className="flex-1 flex-row items-center justify-center gap-2">
              <Icone nome="create-outline" tamanho={18} cor="#9CA3AF" />
              <Texto variante="legenda" style={{ color: '#9CA3AF' }}>
                Desenhe sua assinatura aqui
              </Texto>
            </View>
          ) : (
            <Desenho tracos={tracos} />
          )}
          <View style={{ position: 'absolute', left: 24, right: 24, bottom: 36, height: 1, backgroundColor: '#E5E7EB' }} />
        </View>
      </View>

      {erro ? (
        <Animated.View entering={FadeIn.duration(200)} className="flex-row items-center gap-2">
          <Icone nome="alert-circle" tamanho={18} cor={estado.perigo} />
          <Texto variante="legenda" tom="perigo" className="flex-1">
            {erro}
          </Texto>
        </Animated.View>
      ) : null}

      {confirmando && !vazio ? (
        <Animated.View entering={FadeIn.duration(200)}>
          <Texto variante="legenda" tom="suave">
            Confirma a assinatura? Esta ação não pode ser desfeita.
          </Texto>
        </Animated.View>
      ) : null}

      <View className="flex-row gap-3">
        <Botao titulo="Limpar" icone="refresh" variante="secundario" larguraTotal={false} desativado={vazio || enviando} onPress={limpar} style={{ flex: 1 }} />
        <Botao
          titulo={confirmando ? 'Confirmar' : rotulo}
          icone={confirmando ? 'checkmark-circle' : 'create'}
          desativado={vazio}
          carregando={enviando}
          larguraTotal={false}
          onPress={assinar}
          style={{ flex: 2 }}
        />
      </View>
    </View>
  );
}

/** Cada trecho do traço vira uma barrinha girada (sem SVG no projeto). */
function Desenho({ tracos }: { tracos: Traco[] }) {
  const pedacos: { chave: string; x: number; y: number; comprimento: number; angulo: number }[] = [];
  tracos.forEach((traco, i) => {
    if (traco.length === 1) {
      pedacos.push({ chave: `${i}-0`, x: traco[0].x, y: traco[0].y, comprimento: 0, angulo: 0 });
      return;
    }
    for (let j = 1; j < traco.length; j++) {
      const a = traco[j - 1];
      const b = traco[j];
      pedacos.push({
        chave: `${i}-${j}`,
        x: (a.x + b.x) / 2,
        y: (a.y + b.y) / 2,
        comprimento: Math.hypot(b.x - a.x, b.y - a.y),
        angulo: Math.atan2(b.y - a.y, b.x - a.x),
      });
    }
  });
  return (
    <>
      {pedacos.map((p) => {
        const w = p.comprimento + ESPESSURA;
        return (
          <View
            key={p.chave}
            style={{
              position: 'absolute',
              left: p.x - w / 2,
              top: p.y - ESPESSURA / 2,
              width: w,
              height: ESPESSURA,
              borderRadius: ESPESSURA / 2,
              backgroundColor: TINTA,
              transform: [{ rotate: `${p.angulo}rad` }],
            }}
          />
        );
      })}
    </>
  );
}
