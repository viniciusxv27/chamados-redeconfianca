/**
 * NativeWind v4 (Tailwind 3). As cores vêm de src/constants/tokens.js.
 *
 * Tema escuro por classe: o app decide (sistema, claro ou escuro) e aplica com
 * o colorScheme do NativeWind — ver src/providers/tema.tsx.
 * Superfícies têm par claro/escuro: use `bg-superficie dark:bg-superficie-escuro`
 * (os componentes de src/components/ui já fazem isso por você).
 */
const { marca, estado, claro, escuro, fontes, raios } = require('./src/constants/tokens');

const comSufixo = (paleta, sufixo) =>
  Object.fromEntries(Object.entries(paleta).map(([nome, cor]) => [`${nome}${sufixo}`, cor]));

/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ['./src/**/*.{js,jsx,ts,tsx}'],
  darkMode: 'class',
  presets: [require('nativewind/preset')],
  theme: {
    extend: {
      colors: {
        marca,
        ...estado,
        ...claro,
        ...comSufixo(escuro, '-escuro'),
      },
      fontFamily: {
        sans: [fontes.regular],
        regular: [fontes.regular],
        media: [fontes.media],
        seminegrito: [fontes.seminegrito],
        negrito: [fontes.negrito],
        extranegrito: [fontes.extranegrito],
      },
      borderRadius: {
        sm: `${raios.sm}px`,
        md: `${raios.md}px`,
        lg: `${raios.lg}px`,
        xl: `${raios.xl}px`,
        '2xl': `${raios['2xl']}px`,
      },
    },
  },
  plugins: [],
};
