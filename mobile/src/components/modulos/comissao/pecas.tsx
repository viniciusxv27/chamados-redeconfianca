/** Peças da tela de Comissão: cartão do total, pilar (recolhido/aberto), barra de atingimento. */
import { useState } from 'react';
import { ScrollView, View } from 'react-native';

import { LinhaValor } from '@/components/modulos/comum';
import { Cartao, Chip, Icone, Pressionavel, Texto } from '@/components/ui';
import { estado, fontes, marca, raios } from '@/constants/theme';
import { useCores } from '@/providers/tema';
import type { DadosAparte, PilarComissao, ReferenciaComissao } from '@/services/modulos/comissao';
import { numero, porcentagem, reais } from '@/utils/dinheiro';
import { comAlfa } from '@/utils/formatar';

/** A mesma régua de cor da tela web: verde ≥ 100%, amarelo ≥ 80%, vermelho abaixo. */
export function corDoAtingimento(pct: number) {
  if (pct >= 100) return estado.sucesso;
  if (pct >= 80) return estado.alerta;
  return estado.perigo;
}

export function Barra({ pct, cor }: { pct: number; cor: string }) {
  const cores = useCores();
  const largura = Math.max(0, Math.min(100, pct));
  return (
    <View style={{ height: 8, borderRadius: 4, backgroundColor: cores.superficie3, overflow: 'hidden' }}>
      <View style={{ width: `${largura}%`, height: '100%', borderRadius: 4, backgroundColor: cor }} />
    </View>
  );
}

/** O número grande do topo (RV total). */
export function CartaoTotal({ rotulo, valor, linha }: { rotulo: string; valor: number; linha?: string }) {
  return (
    <View style={{ borderRadius: raios.xl, padding: 20, gap: 4, backgroundColor: marca[500], boxShadow: `0px 8px 24px ${comAlfa(marca[500], 0.3)}` }}>
      <Texto variante="legenda" tom="branco" style={{ opacity: 0.85 }}>
        {rotulo}
      </Texto>
      <Texto variante="display" tom="branco" numberOfLines={1} adjustsFontSizeToFit>
        {reais(valor)}
      </Texto>
      {linha ? (
        <Texto variante="legenda" tom="branco" style={{ opacity: 0.8 }} numberOfLines={2}>
          {linha}
        </Texto>
      ) : null}
    </View>
  );
}

/** Chips de mês/fase: "Set/2026 · Fechada". */
export function SeletorReferencia({ opcoes, atual, aoEscolher }: { opcoes: ReferenciaComissao[]; atual: ReferenciaComissao | null; aoEscolher: (r: ReferenciaComissao) => void }) {
  if (opcoes.length <= 1) return null;
  return (
    <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8, paddingRight: 20 }} style={{ marginHorizontal: -20, paddingLeft: 20, flexGrow: 0 }}>
      {opcoes.map((o) => {
        const ativo = !!atual && atual.ano === o.ano && atual.mes === o.mes && atual.fase === o.fase;
        return (
          <Chip key={`${o.ano}-${o.mes}-${o.fase}`} rotulo={`${o.rotulo.slice(0, 3)}/${o.ano} · ${o.fase === 'antes' ? 'Prévia' : 'Fechada'}`} ativo={ativo} onPress={() => aoEscolher(o)} />
        );
      })}
    </ScrollView>
  );
}

/** Um pilar: recolhido mostra o atingimento do mês e o total; tocando abre o detalhamento. */
export function CartaoPilar({ pilar: p }: { pilar: PilarComissao }) {
  const cores = useCores();
  const [aberto, setAberto] = useState(false);
  const doMes = p.atingimentos[p.atingimentos.length - 1]?.pct ?? 0;
  const cor = corDoAtingimento(doMes);
  return (
    <Cartao onPress={() => setAberto((a) => !a)} acessibilidade={`${p.nome}: ${porcentagem(doMes, false)} de atingimento, ${reais(p.total)}`} style={{ padding: 16, gap: 12 }}>
      <View className="flex-row items-center gap-3">
        <View style={{ width: 40, height: 40, borderRadius: 12, alignItems: 'center', justifyContent: 'center', backgroundColor: cores.superficie2 }}>
          <Texto style={{ fontSize: 20, lineHeight: 26 }}>{p.icone}</Texto>
        </View>
        <View className="flex-1">
          <Texto variante="corpoForte">{p.nome}</Texto>
          <Texto variante="legenda" style={{ color: cor, fontFamily: fontes.seminegrito }}>
            {porcentagem(doMes, false)} no mês
          </Texto>
        </View>
        <View className="items-end">
          <Texto variante="corpoForte">{reais(p.total)}</Texto>
          <Icone nome={aberto ? 'chevron-up' : 'chevron-down'} tamanho={16} cor={cores.texto3} />
        </View>
      </View>
      <Barra pct={doMes} cor={cor} />

      {aberto ? (
        <View className="gap-1">
          <View className="flex-row gap-2">
            {p.atingimentos.map((a) => (
              <View key={a.mes} style={{ flex: 1, padding: 8, borderRadius: raios.sm, backgroundColor: cores.superficie2, alignItems: 'center' }}>
                <Texto variante="legenda" tom="fraco" numberOfLines={1}>
                  {a.mes.slice(0, 3)}
                </Texto>
                <Texto variante="legenda" style={{ color: corDoAtingimento(a.pct), fontFamily: fontes.negrito }}>
                  {porcentagem(a.pct, false)}
                </Texto>
              </View>
            ))}
          </View>
          {p.carteira != null ? (
            <LinhaValor rotulo="Atingimento da loja" valor={p.carteira_sem_dado ? 'sem dado na planilha' : porcentagem(p.carteira, false)} />
          ) : null}
          <LinhaValor rotulo="Comissão" valor={reais(p.comissao)} />
          <LinhaValor rotulo="Premiação individual" valor={reais(p.premiacao_individual)} />
          <LinhaValor rotulo="Premiação coletiva" valor={reais(p.premiacao_coletiva)} />
          <LinhaValor rotulo={p.hunter_id ? `Hunter (${p.hunter_id})` : 'Hunter'} valor={reais(p.hunter)} />
          {p.iq_pct != null ? <LinhaValor rotulo="IQ" valor={porcentagem(p.iq_pct, false)} cor={p.iq_pct < 0 ? estado.perigo : undefined} /> : null}
          {p.meta_total || p.pago || p.exclusao ? (
            <LinhaValor rotulo="Vendas · pago · exclusão" valor={`${reais(p.meta_total)} · ${reais(p.pago)} · ${reais(p.exclusao)}`} />
          ) : null}
          <LinhaValor rotulo="Total do pilar" valor={reais(p.total)} destaque />
        </View>
      ) : null}
    </Cartao>
  );
}

/** Comissão "A parte": fator × salário base, pilar a pilar. */
export function PilaresAparte({ dados }: { dados: DadosAparte }) {
  return (
    <>
      {dados.pilares.map((p) => {
        const cor = corDoAtingimento(p.atingimento);
        return (
          <Cartao key={p.chave} style={{ padding: 16, gap: 8 }}>
            <View className="flex-row items-center justify-between gap-3">
              <Texto variante="corpoForte" className="flex-1">
                {p.nome}
              </Texto>
              <Texto variante="corpoForte">{reais(p.comissao)}</Texto>
            </View>
            <Barra pct={p.atingimento} cor={cor} />
            <View className="flex-row flex-wrap justify-between gap-2">
              <Texto variante="legenda" tom="suave">
                {p.quantidade ? numero(p.realizado) : reais(p.realizado)} de {p.quantidade ? numero(p.meta) : reais(p.meta)}
              </Texto>
              <Texto variante="legenda" style={{ color: cor, fontFamily: fontes.seminegrito }}>
                {porcentagem(p.atingimento, false)} · fator {porcentagem(p.fator, false)}
              </Texto>
            </View>
          </Cartao>
        );
      })}
    </>
  );
}

/** Cartão de atalho (Histórico, Médias). */
export function Atalho({ icone, titulo, texto, onPress }: { icone: 'time-outline' | 'stats-chart-outline' | 'open-outline'; titulo: string; texto: string; onPress: () => void }) {
  const cores = useCores();
  return (
    <Pressionavel onPress={onPress} vibrar escala={0.98} accessibilityLabel={titulo} style={{ flexDirection: 'row', alignItems: 'center', gap: 12, padding: 14, borderRadius: raios.lg, backgroundColor: cores.superficie, borderWidth: 1, borderColor: cores.bordaSuave }}>
      <View style={{ width: 38, height: 38, borderRadius: 12, alignItems: 'center', justifyContent: 'center', backgroundColor: comAlfa('#16A34A', 0.13) }}>
        <Icone nome={icone} tamanho={20} cor="#16A34A" />
      </View>
      <View className="flex-1">
        <Texto variante="corpoForte">{titulo}</Texto>
        <Texto variante="legenda" tom="fraco" numberOfLines={2}>
          {texto}
        </Texto>
      </View>
      <Icone nome="chevron-forward" tamanho={18} cor={cores.texto3} />
    </Pressionavel>
  );
}
