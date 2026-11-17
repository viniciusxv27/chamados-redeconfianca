import '../global.css';
import '@/constants/interop';

import {
  Montserrat_400Regular,
  Montserrat_500Medium,
  Montserrat_600SemiBold,
  Montserrat_700Bold,
  Montserrat_800ExtraBold,
  useFonts,
} from '@expo-google-fonts/montserrat';
import { DarkTheme, DefaultTheme, Stack, ThemeProvider } from 'expo-router';
import * as SplashScreen from 'expo-splash-screen';
import { StatusBar } from 'expo-status-bar';
import { useEffect } from 'react';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { ProvedorConsultas } from '@/providers/consultas';
import { ProvedorSessao, useSessao } from '@/providers/sessao';
import { ProvedorTema, useTema } from '@/providers/tema';

SplashScreen.preventAutoHideAsync();
SplashScreen.setOptions({ duration: 350, fade: true });

export default function RaizDoApp() {
  const [fontesProntas] = useFonts({
    Montserrat_400Regular,
    Montserrat_500Medium,
    Montserrat_600SemiBold,
    Montserrat_700Bold,
    Montserrat_800ExtraBold,
  });

  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <SafeAreaProvider>
        <ProvedorConsultas>
          <ProvedorTema>
            <ProvedorSessao>{fontesProntas ? <Navegacao /> : null}</ProvedorSessao>
          </ProvedorTema>
        </ProvedorConsultas>
      </SafeAreaProvider>
    </GestureHandlerRootView>
  );
}

/**
 * Quem vê o quê é decidido aqui, pela sessão (Stack.Protected): sem login só
 * existe "entrar"; bloqueado pelo portal só existe "bloqueado"; logado, o resto.
 * Trocar de estado troca de tela sozinho — nenhuma tela redireciona.
 */
function Navegacao() {
  const { estado, bloqueio } = useSessao();
  const { cores, escuro } = useTema();

  useEffect(() => {
    if (estado !== 'carregando') void SplashScreen.hideAsync();
  }, [estado]);

  if (estado === 'carregando') return null;
  const logado = estado === 'logado';

  const temaNavegacao = {
    ...(escuro ? DarkTheme : DefaultTheme),
    colors: { ...(escuro ? DarkTheme : DefaultTheme).colors, background: cores.fundo, card: cores.superficie, primary: cores.marca[500], text: cores.texto, border: cores.borda },
  };

  return (
    <ThemeProvider value={temaNavegacao}>
      <StatusBar style={escuro ? 'light' : 'dark'} />
      <Stack screenOptions={{ headerShown: false, animation: 'ios_from_right', contentStyle: { backgroundColor: cores.fundo } }}>
        <Stack.Protected guard={!logado}>
          <Stack.Screen name="entrar" options={{ animation: 'fade' }} />
        </Stack.Protected>
        <Stack.Protected guard={logado && !!bloqueio}>
          <Stack.Screen name="bloqueado" options={{ animation: 'fade', gestureEnabled: false }} />
        </Stack.Protected>
        <Stack.Protected guard={logado && !bloqueio}>
          <Stack.Screen name="(abas)" options={{ animation: 'fade' }} />
          <Stack.Screen name="chamado/[id]" />
          <Stack.Screen name="chamado/novo" options={{ presentation: 'modal', animation: 'slide_from_bottom' }} />
          <Stack.Screen name="comunicado/[id]" />
          <Stack.Screen name="comunicados" />
          <Stack.Screen name="modulo" />
        </Stack.Protected>
      </Stack>
    </ThemeProvider>
  );
}
