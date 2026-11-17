import * as Haptics from 'expo-haptics';
import { Image } from 'expo-image';
import { LinearGradient } from 'expo-linear-gradient';
import { type ComponentRef, useEffect, useRef, useState } from 'react';
import { KeyboardAvoidingView, Platform, ScrollView, TextInput, View } from 'react-native';
import Animated, {
  Easing,
  FadeIn,
  FadeInDown,
  FadeInUp,
  useAnimatedStyle,
  useSharedValue,
  withRepeat,
  withSequence,
  withTiming,
} from 'react-native-reanimated';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { Botao, Campo, Icone, Texto } from '@/components/ui';
import { estado, marca, raios } from '@/constants/theme';
import { mensagemDoErro } from '@/services/api';
import { useSessao } from '@/providers/sessao';
import { useCores } from '@/providers/tema';
import { abrirNoPortal } from '@/utils/links';

/**
 * Entrar: o topo é o preto do menu do portal com o logo flutuando; o
 * formulário sobe por cima. Erro de senha faz o cartão "balançar a cabeça".
 */
export default function Entrar() {
  const { entrar } = useSessao();
  const cores = useCores();
  const insets = useSafeAreaInsets();
  const senhaRef = useRef<ComponentRef<typeof TextInput>>(null);
  const [email, setEmail] = useState('');
  const [senha, setSenha] = useState('');
  const [erro, setErro] = useState<string | null>(null);
  const [enviando, setEnviando] = useState(false);

  const flutuar = useSharedValue(0);
  const tremer = useSharedValue(0);
  useEffect(() => {
    flutuar.set(withRepeat(withTiming(1, { duration: 2600, easing: Easing.inOut(Easing.sin) }), -1, true));
  }, [flutuar]);
  const logo = useAnimatedStyle(() => ({ transform: [{ translateY: -6 + flutuar.get() * 12 }] }));
  const cartao = useAnimatedStyle(() => ({ transform: [{ translateX: tremer.get() }] }));

  async function enviar() {
    if (!email.trim() || !senha) {
      setErro('Informe seu e-mail e sua senha.');
      return;
    }
    setErro(null);
    setEnviando(true);
    try {
      await entrar(email, senha);
      if (Platform.OS !== 'web') void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    } catch (e) {
      setErro(mensagemDoErro(e));
      tremer.set(withSequence(withTiming(-10, { duration: 50 }), withTiming(10, { duration: 50 }), withTiming(-6, { duration: 50 }), withTiming(6, { duration: 50 }), withTiming(0, { duration: 50 })));
      if (Platform.OS !== 'web') void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
    } finally {
      setEnviando(false);
    }
  }

  return (
    <View className="flex-1" style={{ backgroundColor: estado.tinta }}>
      {/* Calor laranja vindo do topo (gradiente: funciona igual no iOS, Android e web). */}
      <LinearGradient colors={[`${marca[700]}66`, `${marca[900]}22`, estado.tinta]} locations={[0, 0.45, 0.8]} style={{ position: 'absolute', inset: 0 }} />

      <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined} className="flex-1">
        <ScrollView contentContainerStyle={{ flexGrow: 1 }} keyboardShouldPersistTaps="handled" bounces={false}>
          <View className="flex-1 items-center justify-center px-8" style={{ paddingTop: insets.top + 24, minHeight: 300 }}>
            <Animated.View entering={FadeIn.duration(600)}>
              <Animated.View style={logo}>
                <Image source={require('@/assets/images/logo-branca.png')} style={{ width: 96, height: 96 }} contentFit="contain" accessibilityLabel="Logo Rede Confiança" />
              </Animated.View>
            </Animated.View>
            <Animated.View entering={FadeInDown.delay(150).duration(500)} className="mt-6 items-center gap-2">
              <Texto variante="display" tom="branco" className="text-center">
                Rede Confiança
              </Texto>
              <Texto tom="branco" className="text-center" style={{ opacity: 0.6 }}>
                Chamados, mural e avisos na palma da mão
              </Texto>
            </Animated.View>
          </View>

          <Animated.View entering={FadeInUp.delay(250).springify().damping(20)}>
           <Animated.View style={cartao}>
            <View
              className="gap-4 px-6 pt-8"
              style={{ backgroundColor: cores.fundo, borderTopLeftRadius: raios['2xl'], borderTopRightRadius: raios['2xl'], paddingBottom: insets.bottom + 24 }}>
              <View className="gap-1">
                <Texto variante="titulo2">Entrar</Texto>
                <Texto tom="suave">Use o mesmo e-mail e senha do portal.</Texto>
              </View>

              <Campo
                rotulo="E-mail"
                icone="mail-outline"
                placeholder="voce@redeconfianca.com.br"
                value={email}
                onChangeText={(v) => {
                  setEmail(v);
                  if (erro) setErro(null);
                }}
                autoCapitalize="none"
                autoComplete="email"
                keyboardType="email-address"
                textContentType="username"
                returnKeyType="next"
                onSubmitEditing={() => senhaRef.current?.focus()}
                editable={!enviando}
              />
              <Campo
                ref={senhaRef}
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

              {erro ? (
                <Animated.View entering={FadeIn} className="flex-row items-center gap-2 rounded-md px-3 py-2.5" style={{ backgroundColor: `${estado.perigo}18` }}>
                  <Icone nome="alert-circle" tamanho={18} cor={estado.perigo} />
                  <Texto variante="legenda" tom="perigo" className="flex-1">
                    {erro}
                  </Texto>
                </Animated.View>
              ) : null}

              <Botao titulo="Entrar" tamanho="lg" onPress={enviar} carregando={enviando} />
              <Botao titulo="Esqueci minha senha" variante="fantasma" tamanho="sm" onPress={() => abrirNoPortal('/users/forgot-password/')} />
            </View>
           </Animated.View>
          </Animated.View>
        </ScrollView>
      </KeyboardAvoidingView>
    </View>
  );
}
