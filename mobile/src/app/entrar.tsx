/**
 * Entrar — a mesma cara do login do portal (templates/users/login.html):
 * fundo claro com degradê laranja, duas manchas de luz pulsando, o quadrado
 * escuro com o logo flutuando, "Bem-vindo de volta!", cartão branco com campos
 * suaves, "Lembrar" + "Esqueceu a senha?", botão em degradê laranja→grafite e
 * o rodapé "Acesso seguro · Disponível 24/7".
 *
 * Entrada coreografada (logo → título → cartão → campos → botão → rodapé),
 * cartão que "balança a cabeça" no erro e saída animada no sucesso, antes de o
 * app trocar para o Início.
 */
import * as Haptics from 'expo-haptics';
import { Image } from 'expo-image';
import { LinearGradient } from 'expo-linear-gradient';
import { type ComponentRef, useEffect, useRef, useState } from 'react';
import { Keyboard, KeyboardAvoidingView, Platform, Pressable, ScrollView, StyleSheet, TextInput, View } from 'react-native';
import Animated, {
  Easing,
  FadeIn,
  FadeInDown,
  FadeInUp,
  interpolate,
  useAnimatedStyle,
  useReducedMotion,
  useSharedValue,
  withDelay,
  withRepeat,
  withSequence,
  withSpring,
  withTiming,
  ZoomIn,
} from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { BotaoGradiente } from '@/components/ui/botao-gradiente';
import { Campo, Icone, Pressionavel, Texto } from '@/components/ui';
import { estado, fontes, marca, raios } from '@/constants/theme';
import { mensagemDoErro } from '@/services/api';
import { apagar, CHAVES, gravar, ler } from '@/services/armazenamento';
import { useSessao } from '@/providers/sessao';
import { useTema } from '@/providers/tema';
import { comAlfa } from '@/utils/formatar';
import { abrirNoPortal } from '@/utils/links';

const mola = (atraso: number) => FadeInDown.delay(atraso).springify().damping(18).stiffness(160);

export default function Entrar() {
  const { entrar } = useSessao();
  const { cores, escuro } = useTema();
  const insets = useSafeAreaInsets();
  const reduzido = useReducedMotion();
  const senhaRef = useRef<ComponentRef<typeof TextInput>>(null);
  const [email, setEmail] = useState('');
  const [senha, setSenha] = useState('');
  const [lembrar, setLembrar] = useState(true);
  const [erro, setErro] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);

  // E-mail lembrado no aparelho (nunca a senha).
  useEffect(() => {
    ler(CHAVES.email).then((salvo) => {
      if (salvo) setEmail((atual) => atual || salvo);
    });
  }, []);

  const flutuar = useSharedValue(0);
  const tremer = useSharedValue(0);
  const saida = useSharedValue(0);
  useEffect(() => {
    if (reduzido) return;
    flutuar.set(withRepeat(withTiming(1, { duration: 3000, easing: Easing.inOut(Easing.sin) }), -1, true));
  }, [flutuar, reduzido]);

  const logoFlutuando = useAnimatedStyle(() => ({ transform: [{ translateY: interpolate(flutuar.get(), [0, 1], [-5, 5]) }] }));
  const cartaoTremendo = useAnimatedStyle(() => ({ transform: [{ translateX: tremer.get() }] }));
  const conteudoSaindo = useAnimatedStyle(() => ({
    opacity: interpolate(saida.get(), [0, 1], [1, 0]),
    transform: [{ translateY: interpolate(saida.get(), [0, 1], [0, 24]) }, { scale: interpolate(saida.get(), [0, 1], [1, 0.96]) }],
  }));

  async function enviar() {
    if (enviando) return;
    if (!email.trim() || !senha) {
      setErro('Informe seu e-mail e sua senha.');
      balancar();
      return;
    }
    Keyboard.dismiss();
    setErro(null);
    setEnviando(true);
    try {
      await entrar(email, senha, async () => {
        if (lembrar) await gravar(CHAVES.email, email.trim());
        else await apagar(CHAVES.email);
        if (Platform.OS !== 'web') void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
        if (reduzido) return;
        saida.set(withTiming(1, { duration: 320, easing: Easing.in(Easing.cubic) }));
        await new Promise((ok) => setTimeout(ok, 300));
      });
    } catch (e) {
      setErro(mensagemDoErro(e));
      balancar();
      if (Platform.OS !== 'web') void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
      setEnviando(false);
    }
  }

  function balancar() {
    tremer.set(withSequence(withTiming(-10, { duration: 50 }), withTiming(10, { duration: 50 }), withTiming(-6, { duration: 50 }), withTiming(6, { duration: 50 }), withTiming(0, { duration: 50 })));
  }

  const fundo = escuro ? ['#2A1912', cores.fundo, cores.fundo] : ['#FFEDE5', '#F9FAFB', '#F3F4F6'];

  return (
    <View style={{ flex: 1, backgroundColor: cores.fundo }}>
      {/* bg-gradient-to-br from-primary/10 via-gray-50 to-gray-100 */}
      <LinearGradient colors={fundo as [string, string, string]} start={{ x: 0, y: 0 }} end={{ x: 1, y: 1 }} style={StyleSheet.absoluteFill} />
      <ManchaDeLuz cor={marca[500]} tamanho={300} style={{ top: -70, left: -90 }} atraso={0} intensidade={escuro ? 0.22 : 0.28} />
      <ManchaDeLuz cor={escuro ? '#4B5563' : '#9CA3AF'} tamanho={380} style={{ bottom: -110, right: -120 }} atraso={900} intensidade={escuro ? 0.18 : 0.22} />

      <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} style={{ flex: 1 }}>
        <ScrollView
          contentContainerStyle={{ flexGrow: 1, justifyContent: 'center', paddingHorizontal: 20, paddingTop: insets.top + 24, paddingBottom: insets.bottom + 20 }}
          keyboardShouldPersistTaps="handled"
          keyboardDismissMode="interactive"
          showsVerticalScrollIndicator={false}>
          <Animated.View style={[{ width: '100%', maxWidth: 440, alignSelf: 'center' }, conteudoSaindo]}>
            {/* Cabeçalho: quadrado escuro com o logo + boas-vindas */}
            <View style={{ alignItems: 'center', marginBottom: 28 }}>
              <Animated.View entering={ZoomIn.springify().damping(13).stiffness(140)}>
                <Animated.View style={logoFlutuando}>
                  <View
                    style={{
                      width: 84,
                      height: 84,
                      borderRadius: 24,
                      backgroundColor: '#1F2937',
                      alignItems: 'center',
                      justifyContent: 'center',
                      boxShadow: '0px 14px 30px rgba(17,24,39,0.35)',
                    }}>
                    <Image source={require('@/assets/images/logo-simbolo.png')} style={{ width: 46, height: 46 * (464 / 440) }} contentFit="contain" accessibilityLabel="Logo Rede Confiança" />
                  </View>
                </Animated.View>
              </Animated.View>
              <Animated.View entering={mola(140)} style={{ alignItems: 'center', marginTop: 22, gap: 6 }}>
                <Texto variante="titulo1" className="text-center" accessibilityRole="header">
                  Bem-vindo de volta!
                </Texto>
                <Texto tom="suave" className="text-center">
                  Entre na sua conta para continuar
                </Texto>
              </Animated.View>
            </View>

            {/* Cartão do formulário */}
            <Animated.View entering={FadeInUp.delay(220).springify().damping(18).stiffness(150)}>
              <Animated.View
                style={[
                  {
                    backgroundColor: escuro ? comAlfa(cores.superficie, 0.92) : 'rgba(255,255,255,0.92)',
                    borderRadius: raios['2xl'],
                    padding: 24,
                    gap: 20,
                    borderWidth: 1,
                    borderColor: escuro ? cores.borda : 'rgba(255,255,255,0.6)',
                    boxShadow: escuro ? '0px 24px 50px rgba(0,0,0,0.45)' : '0px 25px 50px rgba(17,24,39,0.12)',
                  },
                  cartaoTremendo,
                ]}>
                {erro ? (
                  <Animated.View
                    entering={FadeIn.duration(200)}
                    style={{ flexDirection: 'row', alignItems: 'center', gap: 10, padding: 14, borderRadius: raios.lg, backgroundColor: comAlfa(estado.perigo, 0.08), borderWidth: 1, borderColor: comAlfa(estado.perigo, 0.2) }}
                    accessibilityLiveRegion="polite">
                    <Icone nome="alert-circle" tamanho={20} cor={estado.perigo} />
                    <Texto variante="legenda" tom="perigo" className="flex-1" style={{ fontSize: 14 }}>
                      {erro}
                    </Texto>
                  </Animated.View>
                ) : null}

                <Animated.View entering={mola(300)}>
                  <Campo
                    variante="suave"
                    rotulo="E-mail"
                    icone="mail-outline"
                    placeholder="seu.email@exemplo.com"
                    value={email}
                    onChangeText={(v) => {
                      setEmail(v);
                      if (erro) setErro(null);
                    }}
                    autoCapitalize="none"
                    autoCorrect={false}
                    autoComplete="email"
                    keyboardType="email-address"
                    textContentType="username"
                    returnKeyType="next"
                    onSubmitEditing={() => senhaRef.current?.focus()}
                    editable={!enviando}
                  />
                </Animated.View>
                <Animated.View entering={mola(360)}>
                  <Campo
                    ref={senhaRef}
                    variante="suave"
                    rotulo="Senha"
                    icone="lock-closed-outline"
                    placeholder="Sua senha"
                    senha
                    value={senha}
                    onChangeText={(v) => {
                      setSenha(v);
                      if (erro) setErro(null);
                    }}
                    autoComplete="current-password"
                    textContentType="password"
                    returnKeyType="go"
                    onSubmitEditing={enviar}
                    editable={!enviando}
                  />
                </Animated.View>

                <Animated.View entering={mola(420)} style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8, marginTop: -4 }}>
                  <CaixaDeMarcar marcado={lembrar} aoMudar={setLembrar} rotulo="Lembrar e-mail" />
                  <Pressionavel onPress={() => abrirNoPortal('/users/forgot-password/')} hitSlop={10} escala={0.95} accessibilityRole="link" accessibilityLabel="Esqueceu a senha?">
                    <Texto variante="legenda" style={{ color: marca[600], fontFamily: fontes.seminegrito, fontSize: 13 }}>
                      Esqueceu a senha?
                    </Texto>
                  </Pressionavel>
                </Animated.View>

                <Animated.View entering={mola(480)}>
                  <BotaoGradiente titulo="Entrar" icone="log-in-outline" onPress={enviar} carregando={enviando} />
                </Animated.View>
              </Animated.View>
            </Animated.View>

            {/* Rodapé, como o do portal */}
            <Animated.View entering={FadeIn.delay(620).duration(500)} style={{ alignItems: 'center', marginTop: 28, gap: 10 }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 22 }}>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                  <Icone nome="shield-checkmark" tamanho={15} cor={estado.sucesso} />
                  <Texto variante="legenda" tom="suave">
                    Acesso seguro
                  </Texto>
                </View>
                <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
                  <Icone nome="time" tamanho={15} cor={estado.info} />
                  <Texto variante="legenda" tom="suave">
                    Disponível 24/7
                  </Texto>
                </View>
              </View>
              <Texto variante="legenda" tom="fraco" style={{ fontSize: 11 }}>
                © {new Date().getFullYear()} Rede Confiança. Todos os direitos reservados.
              </Texto>
            </Animated.View>
          </Animated.View>
        </ScrollView>
      </KeyboardAvoidingView>
    </View>
  );
}

/**
 * Mancha de luz difusa (o `blur-3xl animate-pulse` do portal). O desfoque de
 * verdade não existe no iOS para View, então são círculos concêntricos bem
 * transparentes — o mesmo efeito, em qualquer aparelho.
 */
function ManchaDeLuz({ cor, tamanho, style, atraso, intensidade }: { cor: string; tamanho: number; style: object; atraso: number; intensidade: number }) {
  const reduzido = useReducedMotion();
  const pulso = useSharedValue(0);
  useEffect(() => {
    if (reduzido) return;
    pulso.set(withDelay(atraso, withRepeat(withTiming(1, { duration: 2600, easing: Easing.inOut(Easing.sin) }), -1, true)));
  }, [pulso, atraso, reduzido]);
  const animado = useAnimatedStyle(() => ({
    opacity: interpolate(pulso.get(), [0, 1], [0.75, 1]),
    transform: [{ scale: interpolate(pulso.get(), [0, 1], [0.94, 1.06]) }],
  }));
  // Muitas camadas finas, mais fortes no centro: o degrau entre elas some e vira um brilho contínuo.
  const camadas = 18;
  return (
    <Animated.View pointerEvents="none" style={[{ position: 'absolute', width: tamanho, height: tamanho, alignItems: 'center', justifyContent: 'center' }, style, animado]}>
      {Array.from({ length: camadas }, (_, i) => {
        const t = i / camadas;
        const lado = tamanho * (1 - t * 0.92);
        return (
          <View
            key={i}
            style={{ position: 'absolute', width: lado, height: lado, borderRadius: lado / 2, backgroundColor: cor, opacity: (intensidade / camadas) * (0.4 + t * 1.2) }}
          />
        );
      })}
    </Animated.View>
  );
}

/** "Lembrar de mim" do portal: caixa de 24 pt, toque na linha inteira, o ✓ entra com mola. */
function CaixaDeMarcar({ marcado, aoMudar, rotulo }: { marcado: boolean; aoMudar: (v: boolean) => void; rotulo: string }) {
  const { cores } = useTema();
  const marcaCor = marca[500];
  const escala = useSharedValue(marcado ? 1 : 0);
  useEffect(() => {
    escala.set(withSpring(marcado ? 1 : 0, { damping: 14, stiffness: 260 }));
  }, [marcado, escala]);
  const check = useAnimatedStyle(() => ({ opacity: escala.get(), transform: [{ scale: 0.4 + escala.get() * 0.6 }] }));
  return (
    <Pressable
      onPress={() => {
        if (Platform.OS !== 'web') void Haptics.selectionAsync();
        aoMudar(!marcado);
      }}
      hitSlop={6}
      accessibilityRole="checkbox"
      accessibilityState={{ checked: marcado }}
      accessibilityLabel={rotulo}
      style={{ flexDirection: 'row', alignItems: 'center', gap: 10, minHeight: 44, flexShrink: 1 }}>
      <View
        style={{
          width: 24,
          height: 24,
          borderRadius: 7,
          borderWidth: 1.5,
          borderColor: marcado ? marcaCor : cores.borda,
          backgroundColor: marcado ? marcaCor : cores.superficie,
          alignItems: 'center',
          justifyContent: 'center',
        }}>
        <Animated.View style={check}>
          <Icone nome="checkmark" tamanho={16} cor="#FFFFFF" />
        </Animated.View>
      </View>
      <Texto variante="legenda" tom="suave" style={{ fontSize: 14 }} numberOfLines={1}>
        {rotulo}
      </Texto>
    </Pressable>
  );
}
