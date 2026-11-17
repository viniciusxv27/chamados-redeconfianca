import { fontes, marca, raios } from '@/constants/theme';
import { useTema } from '@/providers/tema';
import { comAlfa } from '@/utils/formatar';

import { Pressionavel } from './pressionavel';
import { Texto } from './texto';

/** Filtro tocável (pílula). Ativo: laranja translúcido. */
export function Chip({ rotulo, ativo, onPress }: { rotulo: string; ativo: boolean; onPress: () => void }) {
  const { cores } = useTema();
  return (
    <Pressionavel
      onPress={onPress}
      vibrar
      escala={0.94}
      accessibilityRole="button"
      accessibilityState={{ selected: ativo }}
      style={{
        height: 34,
        paddingHorizontal: 14,
        borderRadius: raios.pilula,
        justifyContent: 'center',
        borderWidth: 1,
        borderColor: ativo ? marca[500] : cores.borda,
        backgroundColor: ativo ? comAlfa(marca[500], 0.12) : cores.superficie,
      }}>
      <Texto variante="legenda" style={{ color: ativo ? marca[600] : cores.texto2, fontFamily: ativo ? fontes.negrito : fontes.seminegrito }}>
        {rotulo}
      </Texto>
    </Pressionavel>
  );
}
