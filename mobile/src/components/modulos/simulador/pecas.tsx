/** Peças do Simulador: cartão do ganho, pilar com meta × realizado e totais. */
import { useState } from 'react';
import { View } from 'react-native';

import { LinhaValor } from '@/components/modulos/comum';
import { Cartao, Icone, Texto } from '@/components/ui';
import { estado, fontes, raios } from '@/constants/theme';
import { useCores } from '@/providers/tema';
import type { Comparativo, PilarSimulador, Simulacao, Visao } from '@/services/modulos/simulador';
import { numero, porcentagem, reais } from '@/utils/dinheiro';
import { comAlfa } from '@/utils/formatar';

const VERDE_LIMA = '#65A30D';

/** A régua de cor do simulador web (_results.html): ≥ 100% verde, ≥ 69,5% amarelo, abaixo vermelho. */
export function corDoAtingimento(fracao: number | null) {
  const f = fracao ?? 0;
  if (f >= 1) return estado.sucesso;
  if (f >= 0.695) return estado.alerta;
  return estado.perigo;
}

function Barra({ fracao }: { fracao: number | null }) {
  const cores = useCores();
  const largura = Math.max(0, Math.min(100, (fracao ?? 0) * 100));
  return (
    <View style={{ height: 8, borderRadius: 4, backgroundColor: cores.superficie3, overflow: 'hidden' }}>
      <View style={{ width: `${largura}%`, height: '100%', borderRadius: 4, backgroundColor: corDoAtingimento(fracao) }} />
    </View>
  );
}

function valorDoPilar(p: PilarSimulador, v: number | null) {
  return p.quantidade ? numero(v) : reais(v);
}

export function CartaoGanho({ sim, visao }: { sim: Simulacao; visao: Visao }) {
  const linha = [sim.papel_rotulo, sim.pdv, sim.coordenador ? `Coord. ${sim.coordenador}` : null].filter(Boolean).join(' · ');
  return (
    <View style={{ borderRadius: raios.xl, padding: 20, gap: 4, backgroundColor: VERDE_LIMA, boxShadow: `0px 8px 24px ${comAlfa(VERDE_LIMA, 0.3)}` }}>
      <Texto variante="legenda" tom="branco" style={{ opacity: 0.85 }}>
        {visao === 'realizado' ? 'Ganho com o realizado até ontem' : visao === 'simulador' ? 'Ganho simulado com os valores informados' : 'Ganho projetado no mês'}
      </Texto>
      <Texto variante="display" tom="branco" numberOfLines={1} adjustsFontSizeToFit>
        {reais(sim.ganho_total)}
      </Texto>
      <Texto variante="legenda" tom="branco" style={{ opacity: 0.85 }} numberOfLines={2}>
        {sim.nome}
        {linha ? ` · ${linha}` : ''}
      </Texto>
    </View>
  );
}

function LinhaComparativo({ rotulo, c, quantidade }: { rotulo: string; c: Comparativo; quantidade: boolean }) {
  const f = (v: number | null) => (quantidade ? numero(v) : reais(v));
  return <LinhaValor rotulo={rotulo} valor={`${f(c.realizado)} de ${f(c.meta)} · ${porcentagem(c.atingimento)}`} cor={corDoAtingimento(c.atingimento)} />;
}

export function CartaoPilarSimulador({ pilar: p, visao }: { pilar: PilarSimulador; visao: Visao }) {
  const cores = useCores();
  const [aberto, setAberto] = useState(false);
  const cor = corDoAtingimento(p.atingimento);
  return (
    <Cartao onPress={() => setAberto((a) => !a)} acessibilidade={`${p.rotulo}: ${porcentagem(p.atingimento)} da meta, ${reais(p.ganho)}`} style={{ padding: 16, gap: 10 }}>
      <View className="flex-row items-center gap-3">
        <View className="flex-1">
          <Texto variante="corpoForte">{p.rotulo}</Texto>
          <Texto variante="legenda" tom="suave">
            {valorDoPilar(p, p.realizado)} de {valorDoPilar(p, p.meta)}
          </Texto>
        </View>
        <View className="items-end">
          <Texto variante="corpoForte">{reais(p.ganho)}</Texto>
          <Texto variante="legenda" style={{ color: cor, fontFamily: fontes.seminegrito }}>
            {porcentagem(p.atingimento)}
          </Texto>
        </View>
        <Icone nome={aberto ? 'chevron-up' : 'chevron-down'} tamanho={16} cor={cores.texto3} />
      </View>
      <Barra fracao={p.atingimento} />
      {p.por_dia ? (
        <Texto variante="legenda" tom="fraco">
          Faltam {valorDoPilar(p, p.por_dia)}
          {p.quantidade ? ' por dia útil' : '/dia útil'} para bater a meta
        </Texto>
      ) : null}
      {aberto ? (
        <View>
          {p.quantidade && p.receita ? <LinhaValor rotulo="Receita" valor={reais(p.receita)} /> : null}
          {p.taxa_comissao != null ? <LinhaValor rotulo="% comissão" valor={porcentagem(p.taxa_comissao, true, 2)} /> : null}
          <LinhaValor rotulo="Comissão" valor={reais(p.comissao)} />
          {p.premio != null ? <LinhaValor rotulo={`Premiação${p.taxa_premio ? ` (${porcentagem(p.taxa_premio, true, 2)})` : ''}`} valor={reais(p.premio)} /> : null}
          {p.premio_pdv != null ? <LinhaValor rotulo="Prêmio PDV/coordenação" valor={reais(p.premio_pdv)} /> : null}
          {p.hunter2 ? <LinhaValor rotulo="Hunter II" valor={reais(p.hunter2)} /> : null}
          {p.hunter3 ? <LinhaValor rotulo="Hunter III" valor={reais(p.hunter3)} /> : null}
          {p.loja ? <LinhaComparativo rotulo={visao === 'realizado' ? 'Loja (realizado)' : 'Loja (projeção)'} c={p.loja} quantidade={p.quantidade} /> : null}
          {p.coordenacao ? <LinhaComparativo rotulo="Coordenação" c={p.coordenacao} quantidade={p.quantidade} /> : null}
          <LinhaValor rotulo="Total do pilar" valor={reais(p.ganho)} destaque />
        </View>
      ) : null}
    </Cartao>
  );
}

export function CartaoTotais({ sim }: { sim: Simulacao }) {
  const t = sim.totais;
  return (
    <Cartao style={{ paddingHorizontal: 16, paddingVertical: 8 }}>
      <LinhaValor rotulo="Pilares" valor={reais(t.pilares)} />
      {t.hunter2 ? <LinhaValor rotulo="Hunter II" valor={reais(t.hunter2)} /> : null}
      {t.hunter3 ? <LinhaValor rotulo="Hunter III" valor={reais(t.hunter3)} /> : null}
      <LinhaValor rotulo="Bônus 6/7 pilares" valor={reais(t.bonus_6_7)} cor={t.bonus_6_7 ? estado.sucesso : undefined} />
      {sim.acelerador_ligado ? <LinhaValor rotulo={`Acelerador B2B (+${sim.acelerador_pct}%)`} valor={reais(t.acelerador)} cor={estado.sucesso} /> : null}
      <LinhaValor rotulo="Ganho total" valor={reais(sim.ganho_total)} destaque cor={estado.sucesso} />
      {sim.salario_base != null ? <LinhaValor rotulo="Salário base (A parte)" valor={reais(sim.salario_base)} /> : null}
    </Cartao>
  );
}

/** Avisos do papel: sniper, gerente operacional. */
export function AvisoPapel({ sim }: { sim: Simulacao }) {
  const cores = useCores();
  const texto = sim.sniper
    ? `Sniper: recebe uma parte da comissão de ${sim.sniper_de ?? 'o coordenador atribuído'}.`
    : sim.gerente_operacional
      ? `Gerente operacional: ${sim.percentual_operacional ?? 75}% da comissão de ${sim.gerente_base ?? 'o gerente de vendas da loja'}.`
      : null;
  if (!texto) return null;
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, padding: 14, borderRadius: 14, backgroundColor: comAlfa(estado.info, 0.12) }}>
      <Icone nome="information-circle-outline" tamanho={20} cor={estado.info} />
      <Texto variante="legenda" style={{ color: cores.texto }} className="flex-1">
        {texto}
      </Texto>
    </View>
  );
}
