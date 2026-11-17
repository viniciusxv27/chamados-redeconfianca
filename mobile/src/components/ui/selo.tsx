import { View } from 'react-native';

import { fontes } from '@/constants/theme';
import type { Prioridade, StatusChamado } from '@/services/tipos';
import { COR_PRIORIDADE, COR_STATUS, comAlfa } from '@/utils/formatar';

import { Texto } from './texto';

/** Pílula colorida: fundo translúcido + texto na cor cheia (legível nos dois temas). */
export function Selo({ texto, cor, ponto = true }: { texto: string; cor: string; ponto?: boolean }) {
  return (
    <View
      style={{ backgroundColor: comAlfa(cor, 0.14), paddingHorizontal: 10, paddingVertical: 4, borderRadius: 999, flexDirection: 'row', alignItems: 'center', gap: 6, alignSelf: 'flex-start' }}>
      {ponto ? <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: cor }} /> : null}
      <Texto variante="legenda" style={{ color: cor, fontFamily: fontes.seminegrito, fontSize: 12 }} numberOfLines={1}>
        {texto}
      </Texto>
    </View>
  );
}

export function SeloStatus({ status, nome }: { status: StatusChamado; nome: string }) {
  return <Selo texto={nome} cor={COR_STATUS[status] ?? '#6B7280'} />;
}

export function SeloPrioridade({ prioridade, nome }: { prioridade: Prioridade; nome: string }) {
  return <Selo texto={nome} cor={COR_PRIORIDADE[prioridade] ?? '#6B7280'} ponto={false} />;
}
