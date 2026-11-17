/**
 * Sessão do app: quem está logado, se o portal o bloqueou, entrar e sair.
 *
 * Estados: 'carregando' (lendo o token guardado) → 'anonimo' | 'logado'.
 * O layout raiz troca as telas pelo estado (Stack.Protected), então nenhuma
 * tela precisa redirecionar sozinha.
 */
import { useQueryClient } from '@tanstack/react-query';
import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useState } from 'react';

import { aoBloquear, aoEncerrarSessao, definirToken } from '@/services/api';
import { apagar, CHAVES, gravar, ler } from '@/services/armazenamento';
import { sessao as apiSessao } from '@/services/portal';
import type { Bloqueio, Usuario } from '@/services/tipos';

type Estado = 'carregando' | 'anonimo' | 'logado';

type Sessao = {
  estado: Estado;
  usuario: Usuario | null;
  bloqueio: Bloqueio | null;
  entrar: (email: string, senha: string) => Promise<void>;
  sair: () => Promise<void>;
  /** Pergunta de novo ao portal (perfil e bloqueio, sem cache). */
  conferir: () => Promise<void>;
};

const Contexto = createContext<Sessao | null>(null);

export function ProvedorSessao({ children }: { children: ReactNode }) {
  const consultas = useQueryClient();
  const [estado, setEstado] = useState<Estado>('carregando');
  const [usuario, setUsuario] = useState<Usuario | null>(null);
  const [bloqueio, setBloqueio] = useState<Bloqueio | null>(null);

  const limpar = useCallback(async () => {
    definirToken(null);
    await apagar(CHAVES.token);
    consultas.clear();
    setUsuario(null);
    setBloqueio(null);
    setEstado('anonimo');
  }, [consultas]);

  // Ao abrir o app: há token guardado? Ele ainda vale?
  useEffect(() => {
    let vivo = true;
    (async () => {
      const token = await ler(CHAVES.token);
      if (!token) {
        if (vivo) setEstado('anonimo');
        return;
      }
      definirToken(token);
      try {
        const dados = await apiSessao.eu();
        if (!vivo) return;
        setUsuario(dados.usuario);
        setBloqueio(dados.bloqueio);
        setEstado('logado');
      } catch (erro) {
        // Sem internet: entra com o que tem e as telas mostram o erro de conexão.
        const semConexao = (erro as { semConexao?: boolean })?.semConexao;
        if (!vivo) return;
        if (semConexao) setEstado('logado');
        else await limpar();
      }
    })();
    return () => {
      vivo = false;
    };
  }, [limpar]);

  useEffect(() => aoEncerrarSessao(() => void limpar()), [limpar]);
  useEffect(() => aoBloquear((b) => setBloqueio(b)), []);

  const entrar = useCallback(async (email: string, senha: string) => {
    const dados = await apiSessao.entrar(email.trim(), senha);
    definirToken(dados.token);
    await gravar(CHAVES.token, dados.token);
    setUsuario(dados.usuario);
    setBloqueio(dados.bloqueio);
    setEstado('logado');
  }, []);

  const sair = useCallback(async () => {
    try {
      await apiSessao.sair();
    } catch {
      // mesmo sem internet o aparelho sai; o token expira sozinho no portal
    }
    await limpar();
  }, [limpar]);

  const conferir = useCallback(async () => {
    const dados = await apiSessao.eu(true);
    setUsuario(dados.usuario);
    setBloqueio(dados.bloqueio);
  }, []);

  const valor = useMemo(
    () => ({ estado, usuario, bloqueio, entrar, sair, conferir }),
    [estado, usuario, bloqueio, entrar, sair, conferir],
  );
  return <Contexto.Provider value={valor}>{children}</Contexto.Provider>;
}

export function useSessao() {
  const sessao = useContext(Contexto);
  if (!sessao) throw new Error('useSessao fora do ProvedorSessao');
  return sessao;
}
