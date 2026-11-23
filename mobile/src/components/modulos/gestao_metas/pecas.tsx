/** Peças da Gestão de metas: cartão da loja, total da rede e as variações coloridas. */
import { View } from 'react-native';

import { Cartao, Icone, Texto } from '@/components/ui';
import { estado, fontes, marca, raios } from '@/constants/theme';
import { useCores } from '@/providers/tema';
import type { LinhaMeta } from '@/services/modulos/gestao_metas';
import { numero, porcentagem } from '@/utils/dinheiro';
import { comAlfa } from '@/utils/formatar';

/** Valor de um pilar sem centavos, como a tabela do portal: Fixa conta quantidade; os outros, reais. */
export function valorDoPilar(v: number | null | undefined, quantidade: boolean) {
  if (v == null) return '—';
  return quantidade ? numero(v) : `R$ ${numero(v)}`;
}

/** Verde subindo, vermelho caindo (o .gm-chip do portal). */
export function corDaVariacao(v: number | null | undefined) {
  if (v == null) return '#6B7280';
  if (v > 0.0005) return estado.sucesso;
  if (v < -0.0005) return estado.perigo;
  return '#6B7280';
}

function sinal(v: number | null | undefined) {
  if (v == null) return '—';
  return `${v > 0 ? '+' : ''}${porcentagem(v)}`;
}

export function ChipVariacao({ rotulo, valor }: { rotulo: string; valor: number | null | undefined }) {
  const cor = corDaVariacao(valor);
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 4, paddingHorizontal: 8, height: 24, borderRadius: raios.pilula, backgroundColor: comAlfa(cor, 0.12) }}>
      <Texto variante="legenda" style={{ fontSize: 11, color: cor, fontFamily: fontes.seminegrito }}>
        {rotulo} {sinal(valor)}
      </Texto>
    </View>
  );
}

export function Variacoes({ linha }: { linha: LinhaMeta }) {
  return (
    <View className="flex-row flex-wrap gap-1.5">
      <ChipVariacao rotulo="M×M" valor={linha.mxm} />
      <ChipVariacao rotulo="Y×Y" valor={linha.yxy} />
      <ChipVariacao rotulo="×Quad" valor={linha.mxquad} />
      <ChipVariacao rotulo="×Ant." valor={linha.mxmeta} />
    </View>
  );
}

/** Barra de quanto o realizado do mês já é da meta. */
export function BarraAgora({ feito, meta, quantidade }: { feito: number; meta: number | null; quantidade: boolean }) {
  const cores = useCores();
  const fracao = meta ? feito / meta : null;
  const cor = fracao == null ? marca[500] : fracao >= 1 ? estado.sucesso : fracao >= 0.7 ? estado.alerta : marca[500];
  return (
    <View className="gap-1">
      <View className="flex-row items-center justify-between">
        <Texto variante="legenda" tom="suave">
          Até agora: <Texto variante="legenda" style={{ fontFamily: fontes.seminegrito }}>{valorDoPilar(feito, quantidade)}</Texto>
        </Texto>
        <Texto variante="legenda" style={{ color: cor, fontFamily: fontes.seminegrito }}>
          {fracao == null ? 'sem meta' : porcentagem(fracao, true, 0)}
        </Texto>
      </View>
      <View style={{ height: 6, borderRadius: 3, backgroundColor: cores.superficie3, overflow: 'hidden' }}>
        <View style={{ width: `${Math.min(100, Math.max(0, (fracao ?? 0) * 100))}%`, height: 6, borderRadius: 3, backgroundColor: cor }} />
      </View>
    </View>
  );
}

/** Uma loja do pilar: meta do mês grande, referências e variações; toque abre o detalhe. */
export function CartaoLoja({
  linha,
  fixa,
  rotuloAnt,
  agora,
  aoTocar,
}: {
  linha: LinhaMeta;
  fixa: boolean;
  rotuloAnt: string;
  agora: number | null;
  aoTocar: () => void;
}) {
  const cores = useCores();
  const semMeta = linha.alvo == null;
  return (
    <Cartao onPress={aoTocar} acessibilidade={`${linha.loja}: meta ${valorDoPilar(linha.alvo, fixa)}`} style={{ padding: 16, gap: 12 }}>
      <View className="flex-row items-start gap-3">
        <View className="flex-1">
          <Texto variante="corpoForte" numberOfLines={1}>
            {linha.loja}
          </Texto>
          <Texto variante="legenda" tom="fraco">
            {[linha.regiao, linha.divisao?.length ? `dividida entre ${linha.divisao.length} CN${linha.divisao.length > 1 ? 's' : ''}` : null].filter(Boolean).join(' · ') || 'Sem região'}
          </Texto>
        </View>
        <View className="items-end">
          <Texto variante="rotulo" tom="fraco">
            {fixa ? 'Nova meta' : 'Meta'}
          </Texto>
          <Texto style={{ fontFamily: fontes.negrito, fontSize: 18, lineHeight: 24, color: semMeta ? cores.texto3 : cores.texto }}>{valorDoPilar(linha.alvo, fixa)}</Texto>
        </View>
        <Icone nome="chevron-forward" tamanho={16} cor={cores.texto3} />
      </View>
      <View className="flex-row gap-2">
        <Referencia rotulo="Média quad" valor={valorDoPilar(linha.media_quad, fixa)} />
        <Referencia rotulo="Meta Vivo" valor={linha.meta_vivo == null ? '—' : fixa ? numero(linha.meta_vivo, 1) : valorDoPilar(linha.meta_vivo, false)} />
        <Referencia rotulo={`Meta ${rotuloAnt}`} valor={valorDoPilar(linha.meta_anterior, fixa)} />
      </View>
      {agora != null ? <BarraAgora feito={agora} meta={linha.alvo} quantidade={fixa} /> : null}
      {!semMeta ? <Variacoes linha={linha} /> : null}
    </Cartao>
  );
}

export function Referencia({ rotulo, valor }: { rotulo: string; valor: string }) {
  const cores = useCores();
  return (
    <View style={{ flex: 1, paddingVertical: 8, paddingHorizontal: 10, borderRadius: raios.md, backgroundColor: cores.superficie2 }}>
      <Texto variante="legenda" tom="fraco" numberOfLines={1} style={{ fontSize: 11 }}>
        {rotulo}
      </Texto>
      <Texto variante="legenda" numberOfLines={1} adjustsFontSizeToFit style={{ fontFamily: fontes.seminegrito }}>
        {valor}
      </Texto>
    </View>
  );
}
