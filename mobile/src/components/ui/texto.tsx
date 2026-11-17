import { Text, type TextProps } from 'react-native';

import { estado, marca, tipografia, type VarianteTexto } from '@/constants/theme';
import { useCores } from '@/providers/tema';

type Tom = 'padrao' | 'suave' | 'fraco' | 'marca' | 'branco' | 'perigo' | 'sucesso';

export type TextoProps = TextProps & { variante?: VarianteTexto; tom?: Tom; className?: string };

/**
 * Todo texto do app passa por aqui: Montserrat na escala do tema, cor pelo tom.
 *
 * A cor vai por `style` (do tema atual), não por classe: o tom muda com o
 * estado (selecionado, lido…) e, no react-native-web, trocar a classe deixava
 * a antiga grudada. `className` fica para layout (flex, margem, alinhamento).
 */
export function Texto({ variante = 'corpo', tom = 'padrao', className = '', style, ...resto }: TextoProps) {
  const cores = useCores();
  const cor = {
    padrao: cores.texto,
    suave: cores.texto2,
    fraco: cores.texto3,
    marca: marca[600],
    branco: '#FFFFFF',
    perigo: estado.perigo,
    sucesso: estado.sucesso,
  }[tom];
  return (
    <Text
      className={`${variante === 'rotulo' ? 'uppercase' : ''} ${className}`}
      style={[tipografia[variante], { color: cor }, style]}
      maxFontSizeMultiplier={1.4}
      {...resto}
    />
  );
}
