/**
 * Cache das chamadas (TanStack Query): a tela abre na hora com o que já tinha
 * e atualiza por baixo; volta do segundo plano → recarrega o que estiver velho.
 */
import { focusManager, QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { type ReactNode, useEffect, useState } from 'react';
import { AppState, Platform } from 'react-native';

import { ErroApi } from '@/services/api';

function criarCliente() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        gcTime: 10 * 60_000,
        // Erro do lado de cá (4xx) não melhora tentando de novo; rede e 5xx, sim (2x).
        retry: (falhas, erro) => {
          if (erro instanceof ErroApi && erro.status >= 400 && erro.status < 500) return false;
          return falhas < 2;
        },
      },
      mutations: { retry: false },
    },
  });
}

export function ProvedorConsultas({ children }: { children: ReactNode }) {
  const [cliente] = useState(criarCliente);

  useEffect(() => {
    if (Platform.OS === 'web') return;
    const assinatura = AppState.addEventListener('change', (estado) => focusManager.setFocused(estado === 'active'));
    return () => assinatura.remove();
  }, []);

  return <QueryClientProvider client={cliente}>{children}</QueryClientProvider>;
}
