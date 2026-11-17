/**
 * Tema do app em TypeScript: tipografia, sombras e movimento.
 * As cores moram em ./tokens.js (as mesmas que o Tailwind usa).
 */
import tokens from './tokens';

export const { marca, estado, claro, escuro, fontes, raios, espaco } = tokens;

export type Paleta = typeof claro;
export type EsquemaDeCor = 'claro' | 'escuro';

/** Escala tipográfica (Montserrat). Toque: corpo nunca abaixo de 15. */
export const tipografia = {
  display: { fontFamily: fontes.extranegrito, fontSize: 34, lineHeight: 40, letterSpacing: -0.6 },
  titulo1: { fontFamily: fontes.negrito, fontSize: 28, lineHeight: 34, letterSpacing: -0.4 },
  titulo2: { fontFamily: fontes.negrito, fontSize: 22, lineHeight: 28, letterSpacing: -0.2 },
  titulo3: { fontFamily: fontes.seminegrito, fontSize: 18, lineHeight: 24 },
  corpo: { fontFamily: fontes.regular, fontSize: 15, lineHeight: 22 },
  corpoForte: { fontFamily: fontes.seminegrito, fontSize: 15, lineHeight: 22 },
  legenda: { fontFamily: fontes.media, fontSize: 13, lineHeight: 18 },
  rotulo: { fontFamily: fontes.seminegrito, fontSize: 11, lineHeight: 14, letterSpacing: 0.8 },
} as const;

export type VarianteTexto = keyof typeof tipografia;

/** Sombras (boxShadow do RN — mesma sintaxe do CSS, funciona nas duas plataformas). */
export const sombras = {
  nenhuma: undefined,
  cartao: '0px 1px 2px rgba(17, 24, 39, 0.06), 0px 4px 12px rgba(17, 24, 39, 0.05)',
  elevada: '0px 8px 24px rgba(17, 24, 39, 0.12)',
  flutuante: '0px 12px 32px rgba(255, 107, 53, 0.35)',
  escura: '0px 8px 24px rgba(0, 0, 0, 0.45)',
} as const;

/** Movimento: molas curtas e firmes — nada de animação "mole" atrasando o toque. */
export const movimento = {
  mola: { damping: 18, stiffness: 240, mass: 0.8 },
  molaSuave: { damping: 22, stiffness: 160, mass: 1 },
  rapido: 160,
  normal: 260,
  lento: 420,
  /** Atraso entre itens de uma lista entrando em cascata. */
  cascata: 55,
} as const;

/** Paleta pronta para o esquema atual (ícones, RefreshControl, tab bar). */
export function paleta(esquema: EsquemaDeCor) {
  return { ...(esquema === 'escuro' ? escuro : claro), marca, ...estado, esquema };
}

export type Cores = ReturnType<typeof paleta>;
