/**
 * Abre o PDF com link novo: o link assinado da API vale 5 minutos, então antes
 * de abrir pedimos o detalhe de novo (a pessoa pode ter ficado na tela).
 */
import { useState } from 'react';

import { mensagemDoErro } from '@/services/api';

import { abrirArquivo } from '../comum';

export function useAbrirPdf<T>(recarregar: () => Promise<{ data?: T }>) {
  const [abrindo, setAbrindo] = useState<string | null>(null);
  const [erro, setErro] = useState<string | null>(null);

  async function abrir(qual: string, pegar: (dados: T) => string | null | undefined) {
    setErro(null);
    setAbrindo(qual);
    try {
      const { data } = await recarregar();
      const url = data ? pegar(data) : null;
      if (!url) {
        setErro('O PDF não está disponível. Tente pelo portal.');
        return;
      }
      await abrirArquivo(url);
    } catch (e) {
      setErro(mensagemDoErro(e));
    } finally {
      setAbrindo(null);
    }
  }

  return { abrir, abrindo, erro };
}
