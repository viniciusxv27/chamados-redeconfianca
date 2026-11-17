/**
 * Tokens de design do app — fonte única.
 *
 * Espelham o portal web (templates/base.html, static/css/custom.css e
 * static/css/tema-escuro.css): laranja #FF6B35 como cor da marca, menu preto,
 * fonte Montserrat, cinzas do Tailwind no claro e a paleta azul-grafite do tema
 * escuro. O tailwind.config.js e o theme.ts leem daqui — mudar uma cor é mudar
 * só este arquivo.
 *
 * CommonJS de propósito: o tailwind.config.js roda no Node sem transpilar.
 */

const marca = {
  50: '#FFF3EE',
  100: '#FFE3D6',
  200: '#FFC3A8',
  300: '#FF9E73',
  400: '#FF8250',
  500: '#FF6B35', // primary do portal
  600: '#F05220',
  700: '#C73E14',
  800: '#9E3214',
  900: '#7F2C14',
};

/** Cores com significado — iguais nos dois temas. */
const estado = {
  sucesso: '#27AE60',
  alerta: '#F39C12',
  perigo: '#E74C3C',
  info: '#3B82F6',
  secundaria: '#2C3E50',
  tinta: '#0A0A0A', // o preto do menu lateral do portal
};

/** Superfícies e textos por tema. */
const claro = {
  fundo: '#F3F4F6', // body bg-gray-100
  superficie: '#FFFFFF', // cartões bg-white
  superficie2: '#F9FAFB',
  superficie3: '#E8EAED',
  borda: '#E5E7EB',
  bordaSuave: '#F0F1F3',
  texto: '#111827',
  texto2: '#4B5563',
  texto3: '#9CA3AF',
};

const escuro = {
  fundo: '#12161F',
  superficie: '#1B2130',
  superficie2: '#232A3B',
  superficie3: '#2B3346',
  borda: '#313A4F',
  bordaSuave: '#262E40',
  texto: '#E7EAF0',
  texto2: '#B6BDCC',
  texto3: '#8B93A5',
};

/** Montserrat, como no portal. O nome é o que o expo-font registra. */
const fontes = {
  regular: 'Montserrat_400Regular',
  media: 'Montserrat_500Medium',
  seminegrito: 'Montserrat_600SemiBold',
  negrito: 'Montserrat_700Bold',
  extranegrito: 'Montserrat_800ExtraBold',
};

/** Raios: rounded-lg é o mais usado no portal; no toque, um pouco mais generoso. */
const raios = {
  sm: 8,
  md: 12,
  lg: 16,
  xl: 20,
  '2xl': 28,
  pilula: 999,
};

/** Espaçamento base 4 (o mesmo do Tailwind). */
const espaco = { 0: 0, 1: 4, 2: 8, 3: 12, 4: 16, 5: 20, 6: 24, 8: 32, 10: 40, 12: 48, 16: 64 };

module.exports = { marca, estado, claro, escuro, fontes, raios, espaco };
