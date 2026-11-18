/**
 * Tema claro/escuro. A pessoa escolhe no Perfil: Sistema (padrão), Claro ou
 * Escuro. A escolha fica no aparelho e é aplicada pelo NativeWind (classes
 * `dark:`) — e devolvida em `cores` para o que não usa classe (ícones, barra).
 */
import { useColorScheme as useEsquemaNativeWind } from 'nativewind';
import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useState } from 'react';

import { paleta, type Cores } from '@/constants/theme';
import { CHAVES, gravar, ler } from '@/services/armazenamento';

export type PreferenciaTema = 'sistema' | 'claro' | 'escuro';

type Tema = { preferencia: PreferenciaTema; escolher: (p: PreferenciaTema) => void; cores: Cores; escuro: boolean; /** A preferência guardada já foi lida (antes disso o esquema pode trocar). */ pronto: boolean };

const Contexto = createContext<Tema | null>(null);

const PARA_NATIVEWIND = { sistema: 'system', claro: 'light', escuro: 'dark' } as const;

export function ProvedorTema({ children }: { children: ReactNode }) {
  const { colorScheme, setColorScheme } = useEsquemaNativeWind();
  const [preferencia, setPreferencia] = useState<PreferenciaTema>('sistema');
  const [pronto, setPronto] = useState(false);

  useEffect(() => {
    ler(CHAVES.tema).then((salvo) => {
      if (salvo === 'claro' || salvo === 'escuro' || salvo === 'sistema') {
        setPreferencia(salvo);
        setColorScheme(PARA_NATIVEWIND[salvo]);
      } else {
        setColorScheme('system');
      }
      setPronto(true);
    });
  }, [setColorScheme]);

  const escolher = useCallback(
    (p: PreferenciaTema) => {
      setPreferencia(p);
      setColorScheme(PARA_NATIVEWIND[p]);
      void gravar(CHAVES.tema, p);
    },
    [setColorScheme],
  );

  const escuro = colorScheme === 'dark';
  const valor = useMemo(
    () => ({ preferencia, escolher, cores: paleta(escuro ? 'escuro' : 'claro'), escuro, pronto }),
    [preferencia, escolher, escuro, pronto],
  );
  return <Contexto.Provider value={valor}>{children}</Contexto.Provider>;
}

export function useTema() {
  const tema = useContext(Contexto);
  if (!tema) throw new Error('useTema fora do ProvedorTema');
  return tema;
}

/** Atalho: só as cores do esquema atual. */
export function useCores() {
  return useTema().cores;
}
