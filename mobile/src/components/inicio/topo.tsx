/**
 * Topo da home no estilo novo do portal (templates/home.html + _rc_animacoes.html): faixa em
 * degradê laranja com formas suaves, foto, "Bom dia/Boa tarde/Boa noite 👋", o nome, cargo e setor,
 * saldo C$ (para quem vê no portal), a data por extenso e os atalhos entrando em cascata.
 *
 * Reanimated: a mão que acena e as formas que flutuam têm o transform num Animated.View próprio,
 * separado do que tem `entering` (misturar os dois no mesmo View quebra no iOS).
 */
import { router } from 'expo-router';
import { LinearGradient } from 'expo-linear-gradient';
import { useEffect, useState } from 'react';
import { View } from 'react-native';
import Animated, { Easing, FadeInDown, useAnimatedStyle, useReducedMotion, useSharedValue, withDelay, withRepeat, withSequence, withTiming } from 'react-native-reanimated';

import { Avatar, Icone, type NomeIcone, Pressionavel, Texto } from '@/components/ui';
import { fontes, raios } from '@/constants/theme';
import { useTema } from '@/providers/tema';
import { saudacao as saudacaoLocal } from '@/utils/formatar';
import { abrirNoPortal } from '@/utils/links';

const DEGRADE_CLARO = ['#FF6B35', '#F7931E', '#F59E0B'] as const;
const DEGRADE_ESCURO = ['#B8441C', '#A85A12', '#8A5A0B'] as const;

type Atalho = { icone: NomeIcone; rotulo: string; aoTocar: () => void };

export function TopoInicio({
  primeiroNome,
  nome,
  foto,
  linha,
  saldo,
  saudacao,
  podeCriarComunicado,
  avisosOcultos,
}: {
  primeiroNome: string;
  nome: string;
  foto?: string | null;
  linha: string;
  saldo: string | null;
  saudacao?: string;
  podeCriarComunicado: boolean;
  avisosOcultos: number;
}) {
  const { escuro } = useTema();
  const porExtenso = new Date().toLocaleDateString('pt-BR', { weekday: 'long', day: 'numeric', month: 'long' });
  const hoje = porExtenso.charAt(0).toUpperCase() + porExtenso.slice(1);
  const atalhos: Atalho[] = [
    { icone: 'add-circle-outline', rotulo: 'Novo chamado', aoTocar: () => router.push('/chamado/novo') },
    { icone: 'list-outline', rotulo: 'Meus chamados', aoTocar: () => router.navigate({ pathname: '/chamados', params: { escopo: 'meus' } }) },
    { icone: 'heart-outline', rotulo: 'Reconhecer', aoTocar: () => abrirNoPortal('/compliments/create/') },
    { icone: 'megaphone-outline', rotulo: 'Comunicados', aoTocar: () => router.push('/comunicados') },
    { icone: 'storefront-outline', rotulo: 'Mercadinho', aoTocar: () => abrirNoPortal('/prizes/') },
    ...(podeCriarComunicado ? [{ icone: 'create-outline' as NomeIcone, rotulo: 'Criar comunicado', aoTocar: () => abrirNoPortal('/communications/create/') }] : []),
  ];

  return (
    <Animated.View entering={FadeInDown.duration(420)}>
      <LinearGradient
        colors={[...(escuro ? DEGRADE_ESCURO : DEGRADE_CLARO)]}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 1 }}
        style={{ borderRadius: raios['2xl'], overflow: 'hidden', boxShadow: escuro ? undefined : '0px 12px 28px rgba(255,107,53,0.30)' }}>
        <Forma tamanho={160} cor="rgba(255,255,255,0.16)" style={{ top: -60, right: -40 }} atraso={0} />
        <Forma tamanho={110} cor="rgba(236,72,153,0.22)" style={{ bottom: -45, left: -30 }} atraso={900} />
        <Forma tamanho={46} cor="rgba(255,255,255,0.14)" style={{ top: 70, right: 90 }} atraso={1600} />

        <View style={{ padding: 20, gap: 16 }}>
          <View className="flex-row items-center gap-4">
            <Pressionavel onPress={() => router.navigate('/perfil')} escala={0.95} accessibilityLabel="Abrir perfil">
              <Avatar nome={nome || '?'} foto={foto} tamanho={60} borda="rgba(255,255,255,0.85)" />
            </Pressionavel>
            <View className="flex-1">
              <View className="flex-row items-center gap-1.5">
                <Texto variante="legenda" style={{ color: 'rgba(255,255,255,0.92)', fontSize: 14 }}>
                  {saudacao || saudacaoLocal()},
                </Texto>
                <MaoQueAcena />
              </View>
              <Texto variante="titulo1" tom="branco" numberOfLines={1}>
                {primeiroNome ? `${primeiroNome}!` : 'Olá!'}
              </Texto>
              {linha ? (
                <Texto variante="legenda" numberOfLines={1} style={{ color: 'rgba(255,255,255,0.88)' }}>
                  {linha}
                </Texto>
              ) : null}
            </View>
          </View>

          <Texto variante="legenda" numberOfLines={1} style={{ color: 'rgba(255,255,255,0.9)', marginTop: -6 }}>
            {hoje}
          </Texto>

          {avisosOcultos > 0 || saldo != null ? (
            <View className="flex-row flex-wrap items-center gap-2" style={{ marginTop: -4 }}>
              {saldo != null ? (
                <View style={{ height: 30, paddingHorizontal: 12, borderRadius: raios.pilula, backgroundColor: 'rgba(255,255,255,0.22)', justifyContent: 'center' }}>
                  <Texto tom="branco" style={{ fontFamily: fontes.negrito, fontSize: 14, lineHeight: 18 }}>
                    C$ {saldo}
                  </Texto>
                </View>
              ) : null}
              {avisosOcultos > 0 ? (
                <Pressionavel
                  onPress={() => abrirNoPortal('/?ver_avisos=1')}
                  escala={0.95}
                  accessibilityLabel={`${avisosOcultos} avisos não mostrados ao SUPERADMIN`}
                  style={{ flexDirection: 'row', alignItems: 'center', gap: 6, height: 30, paddingLeft: 4, paddingRight: 10, borderRadius: raios.pilula, backgroundColor: 'rgba(255,255,255,0.2)' }}>
                  <View style={{ width: 22, height: 22, borderRadius: 11, backgroundColor: '#FFFFFF', alignItems: 'center', justifyContent: 'center' }}>
                    <Texto style={{ color: '#EA580C', fontFamily: fontes.negrito, fontSize: 11, lineHeight: 14 }}>{String(avisosOcultos)}</Texto>
                  </View>
                  <Texto variante="legenda" tom="branco" style={{ fontFamily: fontes.seminegrito, fontSize: 12 }}>
                    {avisosOcultos === 1 ? 'aviso não mostrado' : 'avisos não mostrados'}
                  </Texto>
                </Pressionavel>
              ) : null}
            </View>
          ) : null}

          <View className="flex-row flex-wrap" style={{ gap: 8 }}>
            {atalhos.map((a, i) => (
              <Animated.View key={a.rotulo} entering={FadeInDown.delay(180 + i * 60).springify().damping(18)} style={{ flexBasis: '31%', flexGrow: 1 }}>
                <Pressionavel
                  onPress={a.aoTocar}
                  vibrar
                  escala={0.94}
                  accessibilityLabel={a.rotulo}
                  style={{ alignItems: 'center', justifyContent: 'center', gap: 6, minHeight: 76, paddingVertical: 10, paddingHorizontal: 2, borderRadius: raios.lg, backgroundColor: 'rgba(255,255,255,0.18)', borderWidth: 1, borderColor: 'rgba(255,255,255,0.25)' }}>
                  <Icone nome={a.icone} tamanho={21} cor="#FFFFFF" />
                  <Texto variante="legenda" tom="branco" numberOfLines={2} style={{ fontFamily: fontes.seminegrito, fontSize: 11, lineHeight: 14, letterSpacing: -0.2, textAlign: 'center' }}>
                    {a.rotulo}
                  </Texto>
                </Pressionavel>
              </Animated.View>
            ))}
          </View>
        </View>
      </LinearGradient>
    </Animated.View>
  );
}

/** 👋 balançando duas vezes ao abrir e depois de tempos em tempos (o .rc-acena do portal). */
function MaoQueAcena() {
  const reduzido = useReducedMotion();
  const giro = useSharedValue(0);
  useEffect(() => {
    if (reduzido) return;
    const aceno = withSequence(
      withTiming(14, { duration: 120 }),
      withTiming(-8, { duration: 120 }),
      withTiming(14, { duration: 120 }),
      withTiming(-4, { duration: 120 }),
      withTiming(10, { duration: 120 }),
      withTiming(0, { duration: 160 }),
      withDelay(2600, withTiming(0, { duration: 1 })),
    );
    giro.set(withDelay(500, withRepeat(aceno, -1, false)));
  }, [giro, reduzido]);
  const estilo = useAnimatedStyle(() => ({ transform: [{ rotate: `${giro.get()}deg` }] }));
  return (
    <Animated.View style={[{ transformOrigin: '70% 80%' }, estilo]}>
      <Texto style={{ fontSize: 15, lineHeight: 19 }}>👋</Texto>
    </Animated.View>
  );
}

/** Círculo translúcido flutuando devagar atrás do conteúdo (as .rc-forma do portal). */
function Forma({ tamanho, cor, style, atraso }: { tamanho: number; cor: string; style: object; atraso: number }) {
  const reduzido = useReducedMotion();
  const y = useSharedValue(0);
  useEffect(() => {
    if (reduzido) return;
    y.set(withDelay(atraso, withRepeat(withTiming(10, { duration: 3200, easing: Easing.inOut(Easing.sin) }), -1, true)));
  }, [y, atraso, reduzido]);
  const estilo = useAnimatedStyle(() => ({ transform: [{ translateY: y.get() }] }));
  return <Animated.View pointerEvents="none" style={[{ position: 'absolute', width: tamanho, height: tamanho, borderRadius: tamanho / 2, backgroundColor: cor }, style, estilo]} />;
}

/** Número que conta de 0 até o valor ao aparecer (o data-rc-conta do portal). Fica dentro de um <Texto>. */
export function NumeroQueConta({ valor }: { valor: number }) {
  const reduzido = useReducedMotion();
  const [mostrado, setMostrado] = useState(0);
  useEffect(() => {
    const inicio = Date.now();
    const duracao = reduzido ? 0 : 900;
    let quadro = 0;
    // O 1º passo já roda no próximo quadro (setState só no callback, nunca direto no efeito).
    const passo = () => {
      const t = duracao ? Math.min(1, (Date.now() - inicio) / duracao) : 1;
      setMostrado(Math.round(valor * (1 - Math.pow(1 - t, 3))));
      if (t < 1) quadro = requestAnimationFrame(passo);
    };
    quadro = requestAnimationFrame(passo);
    return () => cancelAnimationFrame(quadro);
  }, [valor, reduzido]);
  return <>{String(mostrado)}</>;
}
