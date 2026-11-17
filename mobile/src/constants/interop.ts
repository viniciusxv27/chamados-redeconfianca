/**
 * NativeWind só aplica `className` em componentes que ele conhece. Os
 * animados do Reanimated são criados à parte, então registramos aqui — assim
 * `<Animated.View className="…" entering={…}>` funciona como uma View comum.
 * Importado uma vez, no layout raiz.
 */
import { cssInterop } from 'nativewind';
import Animated from 'react-native-reanimated';

cssInterop(Animated.View, { className: 'style' });
