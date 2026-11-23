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
import Constants, { ExecutionEnvironment } from 'expo-constants';
import { DarkTheme, DefaultTheme, Stack, ThemeProvider } from 'expo-router';
import * as SplashScreen from 'expo-splash-screen';
import { StatusBar } from 'expo-status-bar';
import { useCallback, useState } from 'react';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { AberturaAnimada } from '@/components/navegacao/abertura-animada';
import { PushDoPortal } from '@/components/navegacao/push-do-portal';
import { ProvedorConsultas } from '@/providers/consultas';
import { ProvedorSessao, useSessao } from '@/providers/sessao';
import { ProvedorTema, useTema } from '@/providers/tema';

SplashScreen.preventAutoHideAsync();
// A splash nativa só some quando a abertura animada já está na tela (mesmo desenho: sem piscar).
// (No Expo Go essa opção não existe — só em build próprio; lá ela é pulada para não poluir o log.)
if (Constants.executionEnvironment !== ExecutionEnvironment.StoreClient) {
  SplashScreen.setOptions({ duration: 150, fade: true });
}

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
            <ProvedorSessao>{fontesProntas ? <AppComAbertura /> : null}</ProvedorSessao>
          </ProvedorTema>
        </ProvedorConsultas>
      </SafeAreaProvider>
    </GestureHandlerRootView>
  );
}

/**
 * A abertura com a marca e, por baixo, o app. A primeira tela só monta quando a
 * abertura começa a sair (sessão conferida e tema lido): assim a entrada dela
 * acontece à vista, emendada na saída do logo — e não escondida atrás dele.
 */
function AppComAbertura() {
  const { estado } = useSessao();
  const tema = useTema();
  const [revelar, setRevelar] = useState(false);
  const [abertura, setAbertura] = useState(true);
  const sair = useCallback(() => setRevelar(true), []);
  const terminar = useCallback(() => setAbertura(false), []);
  return (
    <>
      {revelar ? <Navegacao /> : null}
      {abertura ? <AberturaAnimada pronto={estado !== 'carregando' && tema.pronto} aoSair={sair} aoTerminar={terminar} /> : null}
    </>
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
          <Stack.Screen name="destinatarios/[id]" />
          <Stack.Screen name="reconhecimento" />
        </Stack.Protected>
        {/* Módulos: também abertos no bloqueio por curso vencido — como /cursos/ na web, é por lá
            que a pessoa manda o comprovante e sai da trava (o servidor só libera o módulo Cursos). */}
        <Stack.Protected guard={logado && (!bloqueio || bloqueio.tipo === 'curso')}>
          <Stack.Screen name="modulo" />
        </Stack.Protected>
      </Stack>
      <PushDoPortal />
    </ThemeProvider>
  );
}
