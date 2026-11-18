/** Peças do formulário de venda: interruptor, campo de dinheiro, escolhas em pílulas e a caixa de erros. */
import type { ReactNode } from 'react';
import { Switch, View } from 'react-native';
import Animated, { FadeIn } from 'react-native-reanimated';

import { Campo, Cartao, Chip, Icone, Texto } from '@/components/ui';
import { estado, fontes, marca } from '@/constants/theme';
import { useCores } from '@/providers/tema';
import { comAlfa } from '@/utils/formatar';

export function Interruptor({ rotulo, detalhe, valor, aoMudar }: { rotulo: string; detalhe?: string; valor: boolean; aoMudar: (v: boolean) => void }) {
  const cores = useCores();
  return (
    <View className="flex-row items-center gap-3 py-1.5">
      <View className="flex-1">
        <Texto variante="corpoForte">{rotulo}</Texto>
        {detalhe ? (
          <Texto variante="legenda" tom="fraco">
            {detalhe}
          </Texto>
        ) : null}
      </View>
      <Switch
        value={valor}
        onValueChange={aoMudar}
        trackColor={{ false: cores.superficie3, true: marca[500] }}
        thumbColor="#FFFFFF"
        ios_backgroundColor={cores.superficie3}
        accessibilityLabel={rotulo}
      />
    </View>
  );
}

/** Valor em reais digitado (vírgula ou ponto); guarda o texto, quem conta é `num()`. */
export function CampoDinheiro({ rotulo, valor, aoMudar, ajuda, erro, placeholder = '0,00' }: { rotulo: string; valor: string; aoMudar: (v: string) => void; ajuda?: string; erro?: string | null; placeholder?: string }) {
  return (
    <Campo
      rotulo={rotulo}
      icone="cash-outline"
      value={valor}
      onChangeText={(v) => aoMudar(v.replace(/[^0-9.,]/g, ''))}
      keyboardType="decimal-pad"
      placeholder={placeholder}
      ajuda={ajuda}
      erro={erro}
    />
  );
}

/** Escolha única em pílulas (segmentação, grupo do plano, condição, forma de pagamento…). */
export function Escolha<T extends string>({ rotulo, opcoes, valor, aoMudar }: { rotulo?: string; opcoes: { valor: T; rotulo: string }[]; valor: T | null; aoMudar: (v: T) => void }) {
  return (
    <View className="gap-1.5">
      {rotulo ? (
        <Texto variante="legenda" tom="suave" style={{ fontFamily: fontes.seminegrito }}>
          {rotulo}
        </Texto>
      ) : null}
      <View className="flex-row flex-wrap gap-2">
        {opcoes.map((o) => (
          <Chip key={o.valor} rotulo={o.rotulo} ativo={valor === o.valor} onPress={() => aoMudar(o.valor)} />
        ))}
      </View>
    </View>
  );
}

/** Os erros a corrigir nesta etapa (da conferência local ou do servidor). */
export function CaixaErros({ erros, titulo = 'Corrija antes de continuar' }: { erros: string[]; titulo?: string }) {
  if (!erros.length) return null;
  return (
    <Animated.View entering={FadeIn.duration(200)}>
      <Cartao style={{ padding: 14, gap: 6, borderColor: estado.perigo, backgroundColor: comAlfa(estado.perigo, 0.06) }}>
        <View className="flex-row items-center gap-2">
          <Icone nome="alert-circle" tamanho={18} cor={estado.perigo} />
          <Texto variante="corpoForte" tom="perigo">
            {titulo}
          </Texto>
        </View>
        {erros.map((e) => (
          <Texto key={e} variante="legenda" tom="perigo">
            • {e}
          </Texto>
        ))}
      </Cartao>
    </Animated.View>
  );
}

/** Bloco com título dentro de um cartão (cada parte da etapa). */
export function Bloco({ titulo, direita, children }: { titulo: string; direita?: ReactNode; children: ReactNode }) {
  return (
    <Cartao style={{ padding: 16, gap: 12 }}>
      <View className="flex-row items-center justify-between gap-2">
        <Texto variante="titulo3" className="flex-1">
          {titulo}
        </Texto>
        {direita}
      </View>
      {children}
    </Cartao>
  );
}
