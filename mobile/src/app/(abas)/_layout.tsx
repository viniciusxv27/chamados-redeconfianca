import { Tabs } from 'expo-router/js-tabs';
import { Platform } from 'react-native';

import { IconeAba } from '@/components/navegacao/icone-aba';
import { fontes } from '@/constants/theme';
import { useNotificacoes } from '@/hooks/consultas';
import { useTema } from '@/providers/tema';

/** As cinco abas do app. Ícones com pílula animada; Avisos com o número de não lidos. */
export default function LayoutAbas() {
  const { cores, escuro } = useTema();
  const { data: avisos } = useNotificacoes();

  return (
    <Tabs
      screenOptions={{
        headerShown: false,
        animation: 'shift',
        tabBarActiveTintColor: cores.marca[600],
        tabBarInactiveTintColor: cores.texto3,
        tabBarLabelStyle: { fontFamily: fontes.seminegrito, fontSize: 11, marginTop: 2 },
        tabBarStyle: {
          backgroundColor: cores.superficie,
          borderTopColor: escuro ? cores.borda : cores.bordaSuave,
          paddingTop: 6,
          minHeight: Platform.OS === 'web' ? 64 : undefined,
        },
        tabBarItemStyle: { gap: 2 },
      }}>
      <Tabs.Screen
        name="index"
        options={{ title: 'Início', tabBarIcon: ({ focused, color }) => <IconeAba nome="home-outline" nomeAtivo="home" ativo={focused} cor={color as string} /> }}
      />
      <Tabs.Screen
        name="chamados"
        options={{ title: 'Chamados', tabBarIcon: ({ focused, color }) => <IconeAba nome="chatbubbles-outline" nomeAtivo="chatbubbles" ativo={focused} cor={color as string} /> }}
      />
      <Tabs.Screen
        name="modulos"
        options={{ title: 'Módulos', tabBarIcon: ({ focused, color }) => <IconeAba nome="apps-outline" nomeAtivo="apps" ativo={focused} cor={color as string} /> }}
      />
      <Tabs.Screen
        name="avisos"
        options={{
          title: 'Avisos',
          tabBarIcon: ({ focused, color }) => (
            <IconeAba nome="notifications-outline" nomeAtivo="notifications" ativo={focused} cor={color as string} badge={avisos?.nao_lidas} />
          ),
        }}
      />
      <Tabs.Screen
        name="perfil"
        options={{ title: 'Perfil', tabBarIcon: ({ focused, color }) => <IconeAba nome="person-circle-outline" nomeAtivo="person-circle" ativo={focused} cor={color as string} /> }}
      />
    </Tabs>
  );
}
