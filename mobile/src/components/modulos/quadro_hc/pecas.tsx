/** Peças do Quadro de HC: cores das funções, selo de falta/sobra, barra da composição e cartões. */
import { View } from 'react-native';

import { Avatar, Cartao, Icone, Selo, Texto } from '@/components/ui';
import { estado, fontes } from '@/constants/theme';
import { useCores } from '@/providers/tema';
import type { ComparacaoFuncao, LojaResumo, PessoaHC, SituacaoHC } from '@/services/modulos/quadro_hc';

/** As mesmas cores das funções da tela web (templates/quadro_hc/quadro.html: .g-*). */
export const COR_GRUPO: Record<string, string> = {
  Coordenação: '#475569',
  Gerente: '#FF6B35',
  'Gerente operacional': '#F59E0B',
  Administrativo: '#0EA5E9',
  Recepção: '#8B5CF6',
  'Consultor pleno': '#10B981',
  Consultor: '#14B8A6',
  'Sniper / itinerante': '#F43F5E',
  Outros: '#94A3B8',
  'Sem cargo': '#94A3B8',
};

export const corDoGrupo = (grupo: string) => COR_GRUPO[grupo] ?? '#94A3B8';

export const COR_SITUACAO_HC: Record<SituacaoHC, string> = {
  falta: estado.perigo,
  sobra: estado.alerta,
  ok: estado.sucesso,
  sem_previsto: '#94A3B8',
};

/** 'AAAA-MM-DD' → 'DD/MM/AAAA' sem passar por Date (não escorrega de dia pelo fuso). */
export function dataCurta(iso: string | null | undefined, ano = true) {
  if (!iso) return null;
  const [a, m, d] = iso.slice(0, 10).split('-');
  if (!a || !m || !d) return null;
  return ano ? `${d}/${m}/${a}` : `${d}/${m}`;
}

export function textoSituacaoHC(loja: Pick<LojaResumo, 'situacao_hc' | 'falta' | 'sobra'>) {
  switch (loja.situacao_hc) {
    case 'falta':
      return `Falta${loja.falta > 1 ? 'm' : ''} ${loja.falta}`;
    case 'sobra':
      return `Sobra${loja.sobra > 1 ? 'm' : ''} ${loja.sobra}`;
    case 'ok':
      return 'Quadro completo';
    default:
      return 'Sem previsto';
  }
}

export function SeloHC({ loja }: { loja: Pick<LojaResumo, 'situacao_hc' | 'falta' | 'sobra'> }) {
  return <Selo texto={textoSituacaoHC(loja)} cor={COR_SITUACAO_HC[loja.situacao_hc]} />;
}

/** Barra fina com a composição da loja por função (a .barra-hc da web). */
export function BarraComposicao({ resumo, total }: { resumo: { grupo: string; n: number }[]; total: number }) {
  const cores = useCores();
  if (!total) return null;
  return (
    <View style={{ flexDirection: 'row', height: 6, borderRadius: 3, overflow: 'hidden', backgroundColor: cores.superficie3, gap: 2 }}>
      {resumo.map((g) => (
        <View key={g.grupo} style={{ flex: g.n, backgroundColor: corDoGrupo(g.grupo) }} />
      ))}
    </View>
  );
}

/** Real × previsto × diferença em três números. */
export function NumerosHC({ real, previsto, diferenca }: { real: number; previsto: number; diferenca: number | null }) {
  const corDif = diferenca == null || diferenca === 0 ? estado.sucesso : diferenca < 0 ? estado.perigo : estado.alerta;
  return (
    <View className="flex-row gap-2">
      <Numero rotulo="Real" valor={String(real)} />
      <Numero rotulo="Previsto" valor={previsto ? String(previsto) : '—'} />
      <Numero rotulo="Diferença" valor={diferenca == null ? '—' : diferenca > 0 ? `+${diferenca}` : String(diferenca)} cor={diferenca == null ? undefined : corDif} />
    </View>
  );
}

function Numero({ rotulo, valor, cor }: { rotulo: string; valor: string; cor?: string }) {
  return (
    <View className="flex-1 items-center gap-0.5">
      <Texto variante="titulo2" style={cor ? { color: cor } : undefined} numberOfLines={1}>
        {valor}
      </Texto>
      <Texto variante="legenda" tom="suave">
        {rotulo}
      </Texto>
    </View>
  );
}

/** Tabela previsto × real por função. */
export function TabelaComparacao({ linhas }: { linhas: ComparacaoFuncao[] }) {
  const cores = useCores();
  return (
    <View>
      <View className="flex-row items-center py-1.5">
        <Texto variante="legenda" tom="fraco" className="flex-1">
          Função
        </Texto>
        {['Real', 'Prev.', 'Dif.'].map((t) => (
          <Texto key={t} variante="legenda" tom="fraco" style={{ width: 48, textAlign: 'right' }}>
            {t}
          </Texto>
        ))}
      </View>
      {linhas.map((c) => {
        const cor = c.diferenca < 0 ? estado.perigo : c.diferenca > 0 ? estado.alerta : estado.sucesso;
        return (
          <View key={c.grupo} className="flex-row items-center py-2" style={{ borderTopWidth: 1, borderTopColor: cores.bordaSuave }}>
            <View className="flex-1 flex-row items-center gap-2">
              <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: corDoGrupo(c.grupo) }} />
              <Texto variante="legenda" numberOfLines={1} className="flex-1">
                {c.grupo}
              </Texto>
            </View>
            <Texto variante="legenda" style={{ width: 48, textAlign: 'right' }}>
              {c.real}
            </Texto>
            <Texto variante="legenda" tom="suave" style={{ width: 48, textAlign: 'right' }}>
              {c.previsto}
            </Texto>
            <Texto variante="legenda" style={{ width: 48, textAlign: 'right', color: cor, fontFamily: fontes.negrito }}>
              {c.diferenca > 0 ? `+${c.diferenca}` : c.diferenca}
            </Texto>
          </View>
        );
      })}
    </View>
  );
}

export function CartaoLoja({ loja, onPress }: { loja: LojaResumo; onPress: () => void }) {
  const cores = useCores();
  const destaque = loja.situacao_hc === 'falta' || loja.situacao_hc === 'sobra' || loja.sem_gerente;
  const corBorda = loja.sem_gerente ? estado.perigo : COR_SITUACAO_HC[loja.situacao_hc];
  return (
    <Cartao
      onPress={onPress}
      acessibilidade={`Abrir ${loja.nome}: ${loja.total} pessoas, ${textoSituacaoHC(loja)}`}
      style={{ padding: 14, gap: 10, borderLeftWidth: destaque ? 4 : 1, borderLeftColor: destaque ? corBorda : undefined }}>
      <View className="flex-row items-center gap-3">
        <View className="flex-1 gap-0.5">
          <Texto variante="corpoForte" numberOfLines={1}>
            {loja.nome}
          </Texto>
          <Texto variante="legenda" tom="suave" numberOfLines={1}>
            {loja.total_previsto ? `${loja.total} de ${loja.total_previsto} previstos` : `${loja.total} ${loja.total === 1 ? 'pessoa' : 'pessoas'}`}
            {loja.vagas ? ` · ${loja.vagas} ${loja.vagas === 1 ? 'vaga' : 'vagas'}` : ''}
            {loja.encontrados != null ? ` · ${loja.encontrados} no filtro` : ''}
          </Texto>
        </View>
        <Icone nome="chevron-forward" tamanho={18} cor={cores.texto3} />
      </View>
      <BarraComposicao resumo={loja.resumo} total={loja.total} />
      <View className="flex-row flex-wrap gap-1.5">
        {loja.total_previsto ? <SeloHC loja={loja} /> : null}
        {loja.sem_gerente ? <Selo texto="Sem gerente" cor={estado.perigo} ponto={false} /> : null}
        {loja.marcas.demissao ? <Selo texto={`${loja.marcas.demissao} demissão no cadastro`} cor={estado.perigo} ponto={false} /> : null}
        {loja.marcas.experiencia ? <Selo texto={`${loja.marcas.experiencia} experiência em 7 dias`} cor={estado.alerta} ponto={false} /> : null}
      </View>
    </Cartao>
  );
}

function fimDaSituacao(p: PessoaHC) {
  if (p.ferias_fim) return `${p.status} até ${dataCurta(p.ferias_fim, false)}`;
  if (p.retorno) return `${p.status} · volta ${dataCurta(p.retorno, false)}`;
  return p.status;
}

/** Os selos da pessoa no quadro (as marcas da tela web: _marcas.html e as tags da linha). */
export function SelosPessoa({ pessoa: p }: { pessoa: PessoaHC }) {
  return (
    <View className="flex-row flex-wrap gap-1.5">
      {p.status ? <Selo texto={fimDaSituacao(p)} cor={p.situacao === 'FERIAS' ? estado.info : '#6B7280'} /> : null}
      {p.novo ? <Selo texto="Novo" cor={estado.sucesso} ponto={false} /> : null}
      {p.sai_em ? <Selo texto={`Sai ${dataCurta(p.sai_em, false)}`} cor={estado.perigo} ponto={false} /> : null}
      {p.demissao_antiga ? <Selo texto={`Demissão ${dataCurta(p.demissao_antiga)} no cadastro`} cor={estado.perigo} ponto={false} /> : null}
      {p.origem_funcao === 'grupo' ? <Selo texto="Gerente pelo grupo" cor={estado.info} ponto={false} /> : null}
      {p.ajuste ? <Selo texto="Ajuste manual" cor="#6B7280" ponto={false} /> : null}
      {p.experiencia ? <Selo texto="Experiência em 7 dias" cor={estado.alerta} ponto={false} /> : null}
      {p.agendadas.length ? <Selo texto={`Agendada ${dataCurta(p.agendadas[0]!.data, false)}`} cor={estado.alerta} ponto={false} /> : null}
    </View>
  );
}

export function CartaoPessoa({ pessoa, onPress }: { pessoa: PessoaHC; onPress: () => void }) {
  const cores = useCores();
  const cor = corDoGrupo(pessoa.grupo);
  return (
    <Cartao onPress={onPress} acessibilidade={`Abrir ${pessoa.nome}`} style={{ padding: 14, flexDirection: 'row', alignItems: 'center', gap: 12 }}>
      <Avatar nome={pessoa.nome} tamanho={44} borda={cor} />
      <View className="flex-1 gap-1">
        <Texto variante="corpoForte" numberOfLines={1}>
          {pessoa.nome}
        </Texto>
        <View className="flex-row items-center gap-1.5">
          <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: cor }} />
          <Texto variante="legenda" tom="suave" numberOfLines={1} className="flex-1">
            {pessoa.cargo || 'Sem cargo'}
            {pessoa.contratacao ? ` · desde ${dataCurta(pessoa.contratacao)}` : ''}
          </Texto>
        </View>
        <SelosPessoa pessoa={pessoa} />
      </View>
      <Icone nome="chevron-forward" tamanho={18} cor={cores.texto3} />
    </Cartao>
  );
}

/** Linha de vaga em aberto (à mão ou pelo previsto). */
export function CartaoVaga({ titulo, detalhe }: { titulo: string; detalhe: string }) {
  return (
    <Cartao style={{ padding: 14, flexDirection: 'row', alignItems: 'center', gap: 12, borderStyle: 'dashed' }}>
      <View style={{ width: 44, height: 44, borderRadius: 22, alignItems: 'center', justifyContent: 'center', backgroundColor: `${estado.alerta}22` }}>
        <Icone nome="person-add-outline" tamanho={20} cor={estado.alerta} />
      </View>
      <View className="flex-1 gap-0.5">
        <Texto variante="corpoForte" numberOfLines={1}>
          {titulo}
        </Texto>
        <Texto variante="legenda" tom="suave" numberOfLines={2}>
          {detalhe}
        </Texto>
      </View>
    </Cartao>
  );
}
