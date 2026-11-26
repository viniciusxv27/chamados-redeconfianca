/**
 * Cartão do Impulso na home, como o do portal (templates/impulso/_home_card.html): a pontuação do mês,
 * a faixa, a posição no ranking, quanto falta para a próxima faixa, os três blocos e as próximas
 * dicas (cada uma leva para onde se resolve). Só para quem é do Impulso; sem dados, o cartão some.
 */
import { LinearGradient } from 'expo-linear-gradient';
import { type Href, router } from 'expo-router';
import { useEffect } from 'react';
import { View } from 'react-native';
import Animated, { useAnimatedStyle, useSharedValue, withDelay, withTiming } from 'react-native-reanimated';

import { Cartao, Esqueleto, GrupoEsqueleto, Icone, type NomeIcone, Pressionavel, Texto } from '@/components/ui';
import { estado, fontes, raios } from '@/constants/theme';
import { useImpulsoHome } from '@/hooks/consultas';
import { useTema } from '@/providers/tema';
import type { CartaoImpulsoHome, DicaImpulso } from '@/services/tipos';
import { comAlfa } from '@/utils/formatar';
import { abrirLink } from '@/utils/links';

const TONS: Record<DicaImpulso['tom'], { base: string; claro: string; texto: string }> = {
  vermelho: { base: '#EF4444', claro: '#FEF2F2', texto: '#991B1B' },
  ambar: { base: '#F59E0B', claro: '#FFFBEB', texto: '#92400E' },
  azul: { base: '#3B82F6', claro: '#EFF6FF', texto: '#1E40AF' },
  laranja: { base: '#FF6B35', claro: '#FFF7F2', texto: '#7C2D12' },
};

function numero(v: number) {
  return Number.isInteger(v) ? String(v) : v.toFixed(1).replace('.', ',');
}

function Barra({ pct, cor, altura = 5, atraso = 150 }: { pct: number; cor: string; altura?: number; atraso?: number }) {
  const { cores } = useTema();
  const largura = useSharedValue(0);
  useEffect(() => {
    largura.set(withDelay(atraso, withTiming(Math.max(0, Math.min(100, pct)), { duration: 900 })));
  }, [pct, atraso, largura]);
  const estilo = useAnimatedStyle(() => ({ width: `${largura.get()}%` }));
  return (
    <View style={{ height: altura, borderRadius: 99, backgroundColor: cores.superficie3, overflow: 'hidden' }}>
      <Animated.View style={[{ height: '100%', borderRadius: 99, backgroundColor: cor }, estilo]} />
    </View>
  );
}

export function CartaoImpulso() {
  const { data, isPending } = useImpulsoHome();
  if (isPending) return <EsqueletoImpulso />;
  if (!data) return null;
  return <Conteudo cartao={data} />;
}

function Conteudo({ cartao }: { cartao: CartaoImpulsoHome }) {
  const { cores, escuro } = useTema();
  const pct = Math.round(cartao.percentual);
  return (
    <Cartao style={{ padding: 0, overflow: 'hidden' }}>
      <LinearGradient colors={['#2563EB', '#7C3AED', '#FF6B35']} start={{ x: 0, y: 0 }} end={{ x: 1, y: 0 }} style={{ height: 4 }} />
      <View className="flex-row items-center gap-3.5" style={{ padding: 16, paddingBottom: 10 }}>
        <View
          accessibilityLabel={`${pct}% da pontuação do mês`}
          style={{ width: 64, height: 64, borderRadius: 32, borderWidth: 5, borderColor: cartao.faixa.cor, alignItems: 'center', justifyContent: 'center', backgroundColor: comAlfa(cartao.faixa.cor, 0.08) }}>
          <Texto style={{ fontFamily: fontes.extranegrito, fontSize: 17, color: cores.texto }}>{pct}%</Texto>
        </View>
        <View className="flex-1" style={{ gap: 4 }}>
          <Texto style={{ fontFamily: fontes.extranegrito, fontSize: 10.5, letterSpacing: 1.2, color: '#2563EB' }} numberOfLines={1}>
            SEU IMPULSO · {cartao.mes.toUpperCase()}
            {cartao.em_conferencia ? ' (EM CONFERÊNCIA)' : ''}
          </Texto>
          <View className="flex-row flex-wrap items-center gap-2">
            <View className="flex-row items-center gap-1" style={{ backgroundColor: cartao.faixa.cor, borderRadius: 99, paddingHorizontal: 9, paddingVertical: 3 }}>
              <Icone nome={cartao.faixa.icone as NomeIcone} tamanho={12} cor="#fff" />
              <Texto style={{ color: '#fff', fontFamily: fontes.negrito, fontSize: 12 }}>{cartao.faixa.nome}</Texto>
            </View>
            {cartao.posicao ? (
              <View className="flex-row items-center gap-1">
                <Icone nome="trophy" tamanho={13} cor="#F59E0B" />
                <Texto variante="legenda" style={{ fontFamily: fontes.negrito, color: cores.texto2 }}>
                  {cartao.posicao}º de {cartao.participantes} no ranking
                </Texto>
              </View>
            ) : null}
          </View>
          <Texto variante="legenda" tom="fraco" style={{ fontSize: 12 }}>
            {numero(cartao.total)} de {numero(cartao.maximo)} pts
            {cartao.proxima ? (
              <>
                {' · faltam '}
                <Texto variante="legenda" style={{ fontFamily: fontes.negrito, color: cores.texto, fontSize: 12 }}>
                  {numero(cartao.proxima.falta)}%
                </Texto>
                {` para ${cartao.proxima.nome}`}
              </>
            ) : (
              ' · nível máximo! 🚀'
            )}
          </Texto>
        </View>
      </View>

      <View className="flex-row gap-2" style={{ paddingHorizontal: 16 }}>
        {cartao.blocos.map((b, i) => (
          <View key={b.nome} className="flex-1" style={{ borderRadius: 12, backgroundColor: cores.superficie2, padding: 8, gap: 3 }}>
            <View className="flex-row items-center gap-1">
              <Icone nome={b.icone as NomeIcone} tamanho={11} cor={b.cor} />
              <Texto variante="legenda" numberOfLines={1} style={{ fontFamily: fontes.negrito, fontSize: 11, color: cores.texto3, flexShrink: 1 }}>
                {b.nome}
              </Texto>
            </View>
            <Texto style={{ fontFamily: fontes.negrito, fontSize: 15, color: cores.texto }}>
              {numero(b.valor)}
              <Texto style={{ fontSize: 11, color: cores.texto3, fontFamily: fontes.seminegrito }}>/{numero(b.max)}</Texto>
            </Texto>
            <Barra pct={b.pct} cor={b.cor} altura={4} atraso={150 + i * 80} />
          </View>
        ))}
      </View>

      {cartao.dicas.length ? (
        <View style={{ padding: 16, paddingTop: 12, gap: 7 }} accessibilityLabel="Próximos passos">
          {cartao.dicas.map((d, i) => {
            const tom = TONS[d.tom] ?? TONS.laranja;
            const fundo = escuro ? comAlfa(tom.base, 0.14) : tom.claro;
            const texto = escuro ? tom.base : tom.texto;
            return (
              <Pressionavel
                key={i}
                escala={0.98}
                onPress={() => (d.url ? abrirLink(d.url) : router.push('/modulo/impulso' as Href))}
                accessibilityLabel={d.texto}
                style={{ flexDirection: 'row', alignItems: 'center', gap: 10, borderRadius: 12, backgroundColor: fundo, paddingHorizontal: 10, paddingVertical: 9 }}>
                <View style={{ width: 28, height: 28, borderRadius: 9, backgroundColor: escuro ? comAlfa(tom.base, 0.18) : 'rgba(255,255,255,0.8)', alignItems: 'center', justifyContent: 'center' }}>
                  <Icone nome={d.icone as NomeIcone} tamanho={14} cor={tom.base} />
                </View>
                <Texto className="flex-1" style={{ color: texto, fontFamily: fontes.seminegrito, fontSize: 13, lineHeight: 17 }}>
                  {d.texto}
                </Texto>
                {d.pontos ? <Texto style={{ color: texto, fontFamily: fontes.extranegrito, fontSize: 11 }}>+{d.pontos} pts</Texto> : null}
                <Icone nome="chevron-forward" tamanho={13} cor={texto} />
              </Pressionavel>
            );
          })}
        </View>
      ) : (
        <View className="flex-row items-center gap-2" style={{ padding: 16, paddingTop: 12 }}>
          <Icone nome="checkmark-circle" tamanho={16} cor={estado.sucesso} />
          <Texto variante="legenda" tom="suave">
            Tudo em dia por aqui. Continue assim!
          </Texto>
        </View>
      )}

      <View className="flex-row items-center justify-between" style={{ borderTopWidth: 1, borderTopColor: cores.bordaSuave, paddingHorizontal: 16, paddingVertical: 11 }}>
        <Texto variante="legenda" tom="fraco">
          {cartao.metas.abertas} {cartao.metas.abertas === 1 ? 'meta' : 'metas'} em aberto
        </Texto>
        <Pressionavel onPress={() => router.push('/modulo/impulso' as Href)} hitSlop={8} escala={0.95} accessibilityRole="link">
          <Texto variante="legenda" style={{ color: '#2563EB', fontFamily: fontes.negrito }}>
            Abrir o Impulso →
          </Texto>
        </Pressionavel>
      </View>
    </Cartao>
  );
}

function EsqueletoImpulso() {
  return (
    <Cartao style={{ padding: 16, gap: 12, borderRadius: raios.lg }}>
      <GrupoEsqueleto>
        <View className="flex-row items-center gap-3.5">
          <Esqueleto largura={64} altura={64} raio={32} />
          <View className="flex-1 gap-2">
            <Esqueleto largura="45%" altura={10} />
            <Esqueleto largura="60%" />
            <Esqueleto largura="50%" altura={10} />
          </View>
        </View>
        <Esqueleto largura="100%" altura={52} />
      </GrupoEsqueleto>
    </Cartao>
  );
}
