/**
 * Uma competência da Gestão de metas — a tela /gestao-metas/<mês>/ no celular, um pilar por vez:
 * total da rede, as lojas (meta do mês, referências, variações e o realizado até agora) e, ao tocar,
 * o detalhe da loja. Quem edita muda a meta (na Fixa também CNs e decisão), salva e divide entre os
 * consultores do Quadro de HC, faz o preenchimento rápido e fecha a competência — tudo pelas views
 * do portal. A planilha .xlsx abre pelo link do app.
 */
import * as Haptics from 'expo-haptics';
import { type Href, router, useLocalSearchParams } from 'expo-router';
import { useMemo, useState } from 'react';
import { Alert, Platform, ScrollView, View } from 'react-native';

import { Folha, Interruptor } from '@/components/modulos/agenda/pecas';
import { EsqueletoBloco, Indicador, LinhaValor, TelaModulo, abrirArquivo } from '@/components/modulos/comum';
import { CartaoLoja, Referencia, Variacoes, valorDoPilar } from '@/components/modulos/gestao_metas/pecas';
import { Botao, BotaoIcone, Campo, Cartao, Chip, Entrada, EstadoVazio, Icone, Segmentado, Selo, Texto } from '@/components/ui';
import { estado, marca } from '@/constants/theme';
import { useCompetencia, useDividirMeta, useFecharCompetencia, usePreencherMetas, useRealizadoAgora, useSalvarMeta } from '@/hooks/modulos/gestao_metas';
import { useCores } from '@/providers/tema';
import { mensagemDoErro } from '@/services/api';
import type { CampoMeta, Competencia, Decisao, LinhaMeta } from '@/services/modulos/gestao_metas';
import { numero, reais } from '@/utils/dinheiro';
import { comAlfa } from '@/utils/formatar';
import { abrirNoPortal } from '@/utils/links';

function vibrar() {
  if (Platform.OS !== 'web') void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
}

/** Número da API → texto para o campo (vírgula decimal, sem milhar — o portal lê os dois jeitos). */
function paraCampo(v: number | null | undefined) {
  if (v == null) return '';
  return String(v).replace('.', ',');
}

export default function CompetenciaMetas() {
  const { codigo, pilar: pilarInicial } = useLocalSearchParams<{ codigo: string; pilar?: string }>();
  const [pilar, setPilar] = useState(pilarInicial || 'movel');
  const [regiao, setRegiao] = useState('');
  const [busca, setBusca] = useState('');
  const [aberta, setAberta] = useState<number | null>(null);
  const [preenchendo, setPreenchendo] = useState(false);
  const { data, isPending, error, refetch, isRefetching, isFetching } = useCompetencia(codigo, pilar);
  const agora = useRealizadoAgora(codigo, !!data);
  const fechar = useFecharCompetencia(codigo);

  const doPilar = data && data.pilar === pilar ? data : undefined;
  const feitoPorLoja = useMemo(() => {
    const mapa = new Map<number, number>();
    const lojas = agora.data?.lojas?.[pilar];
    if (!lojas) return mapa;
    for (const [id, v] of Object.entries(lojas)) mapa.set(Number(id), data?.fixa ? (v.qtd ?? v.tv + v.fixa) : v.valor);
    return mapa;
  }, [agora.data, pilar, data?.fixa]);
  const temAgora = agora.data?.situacao === 'banco' || agora.data?.situacao === 'gravado';
  const regioes = useMemo(() => [...new Set((doPilar?.linhas ?? []).map((l) => l.regiao).filter(Boolean) as string[])], [doPilar]);
  const termo = busca.trim().toLowerCase();
  const linhas = (doPilar?.linhas ?? []).filter((l) => (!regiao || l.regiao === regiao) && (!termo || (l.loja ?? '').toLowerCase().includes(termo)));
  const lojaAberta = doPilar?.linhas.find((l) => l.loja_id === aberta) ?? null;
  const totalAgora = temAgora ? [...feitoPorLoja.values()].reduce((s, v) => s + v, 0) : null;

  function irPara(outro: string | null) {
    if (outro) router.replace({ pathname: '/modulo/gestao_metas/[codigo]', params: { codigo: outro, pilar } } as unknown as Href);
  }

  function confirmarFechar(acao: 'fechar' | 'reabrir') {
    const texto =
      acao === 'fechar'
        ? 'As metas desta competência ficam só para leitura e viram a "meta mês anterior" da próxima.'
        : 'A competência volta a aceitar mudanças nas metas.';
    Alert.alert(acao === 'fechar' ? 'Fechar a competência?' : 'Reabrir a competência?', texto, [
      { text: 'Cancelar', style: 'cancel' },
      {
        text: acao === 'fechar' ? 'Fechar' : 'Reabrir',
        style: acao === 'fechar' ? 'destructive' : 'default',
        onPress: () =>
          fechar.mutate(acao, {
            onSuccess: (r) => (vibrar(), Alert.alert('Pronto', r.mensagem)),
            onError: (e) => Alert.alert('Não deu certo', mensagemDoErro(e)),
          }),
      },
    ]);
  }

  return (
    <TelaModulo
      esqueleto="painel"
      titulo={data ? data.rotulo.charAt(0).toUpperCase() + data.rotulo.slice(1) : 'Competência'}
      subtitulo={data ? `${data.status_nome} · ${data.pilar_nome}` : 'Gestão de metas'}
      carregando={isPending}
      erro={data ? undefined : error}
      aoAtualizar={() => (void refetch(), void agora.refetch())}
      atualizando={isRefetching}
      direita={data ? <BotaoIcone icone="open-outline" rotulo="Abrir no portal" onPress={() => abrirNoPortal(data.url_web)} /> : undefined}>
      {data ? (
        <>
          {/* Mês anterior / seguinte */}
          <View className="flex-row items-center gap-2">
            <BotaoIcone icone="chevron-back" rotulo="Competência anterior" onPress={() => irPara(data.anterior)} />
            <View className="flex-1 flex-row flex-wrap justify-center gap-2">
              {data.fechada ? <Selo texto="Fechada · só leitura" cor="#6B7280" /> : <Selo texto="Aberta" cor={estado.sucesso} />}
            </View>
            <BotaoIcone icone="chevron-forward" rotulo="Próxima competência" onPress={() => irPara(data.seguinte)} />
          </View>

          {/* Pilares */}
          <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ marginHorizontal: -20 }} contentContainerStyle={{ paddingHorizontal: 20, gap: 8 }}>
            {data.pilares.map((p) => (
              <Chip key={p.valor} rotulo={p.rotulo} ativo={pilar === p.valor} onPress={() => (setPilar(p.valor), setAberta(null))} />
            ))}
          </ScrollView>

          {/* Ações */}
          <View className="flex-row flex-wrap gap-2">
            <Chip rotulo="Planilha .xlsx" ativo={false} onPress={() => void abrirArquivo(data.excel)} />
            <Chip rotulo="Histórico" ativo={false} onPress={() => router.push({ pathname: '/modulo/gestao_metas/historico', params: { codigo: data.codigo, pilar } } as unknown as Href)} />
            {data.pode_editar ? <Chip rotulo="Preenchimento rápido" ativo={false} onPress={() => setPreenchendo(true)} /> : null}
            {data.pode_fechar ? <Chip rotulo="Fechar competência" ativo={false} onPress={() => confirmarFechar('fechar')} /> : null}
            {data.pode_reabrir ? <Chip rotulo="Reabrir" ativo={false} onPress={() => confirmarFechar('reabrir')} /> : null}
          </View>

          {/* Avisos do topo da tela do portal */}
          {data.meses_faltando.length ? <Aviso texto={`Sem realizado importado de ${data.meses_faltando.join(', ')}: as referências desses meses ficam vazias.`} /> : null}
          {data.anterior_ausente ? <Aviso texto={`Não há a competência de ${data.rotulos.mes_anterior} no módulo: a "meta mês anterior" vem da planilha antiga (quando houver).`} /> : null}
          {data.anterior_aberta ? <Aviso texto={`A competência de ${data.rotulos.mes_anterior} ainda está aberta: a "meta mês anterior" pode mudar até ela ser fechada.`} /> : null}

          {!doPilar || (isFetching && !isRefetching && data.pilar !== pilar) ? (
            <>
              <EsqueletoBloco linhas={3} />
              <EsqueletoBloco linhas={4} />
            </>
          ) : (
            <>
              {/* Rede */}
              <Entrada indice={0}>
                <View className="flex-row flex-wrap gap-3">
                  <Indicador rotulo={doPilar.fixa ? 'Nova meta da rede (qtd)' : 'Meta da rede'} valor={valorDoPilar(doPilar.rede.alvo, doPilar.fixa)} icone="flag-outline" cor={marca[500]} />
                  <Indicador
                    rotulo={`Realizado até agora (${data.rotulos.competencia})`}
                    valor={totalAgora == null ? (agora.isFetching ? '…' : '—') : valorDoPilar(totalAgora, doPilar.fixa)}
                    detalhe={totalAgora != null && doPilar.rede.alvo ? `${Math.round((totalAgora / doPilar.rede.alvo) * 100)}% da meta` : agora.data?.situacao === 'futuro' ? 'Mês ainda não começou' : undefined}
                    icone="trending-up-outline"
                    cor={estado.sucesso}
                  />
                </View>
              </Entrada>
              <Entrada indice={1}>
                <Cartao style={{ padding: 16, gap: 10 }}>
                  <Texto variante="rotulo" tom="fraco">
                    Rede · {doPilar.rede.qtd_lojas} lojas
                  </Texto>
                  <View className="flex-row gap-2">
                    <Referencia rotulo="Média quad" valor={valorDoPilar(doPilar.rede.media_quad, doPilar.fixa)} />
                    <Referencia rotulo="Meta Vivo" valor={valorDoPilar(doPilar.rede.meta_vivo, doPilar.fixa)} />
                    <Referencia rotulo={`Meta ${data.rotulos.mes_anterior}`} valor={valorDoPilar(doPilar.rede.meta_anterior, doPilar.fixa)} />
                  </View>
                  {doPilar.rede.alvo != null ? <Variacoes linha={doPilar.rede} /> : null}
                  {doPilar.grupos.length ? (
                    <View className="gap-1" style={{ marginTop: 4 }}>
                      {doPilar.grupos.map((g) => (
                        <LinhaValor key={g.titulo} rotulo={`${g.titulo} · ${g.qtd_lojas} lojas`} valor={valorDoPilar(g.alvo, doPilar.fixa)} />
                      ))}
                    </View>
                  ) : null}
                </Cartao>
              </Entrada>

              {/* Filtros */}
              {regioes.length > 1 ? (
                <View className="flex-row flex-wrap gap-2">
                  <Chip rotulo="Todas" ativo={!regiao} onPress={() => setRegiao('')} />
                  {regioes.map((r) => (
                    <Chip key={r} rotulo={r} ativo={regiao === r} onPress={() => setRegiao(r)} />
                  ))}
                </View>
              ) : null}
              <Campo icone="search" placeholder="Buscar loja…" value={busca} onChangeText={setBusca} autoCorrect={false} />

              {linhas.length ? (
                linhas.map((l, i) => (
                  <Entrada key={l.loja_id} indice={i < 6 ? i + 2 : 0}>
                    <CartaoLoja linha={l} fixa={doPilar.fixa} rotuloAnt={data.rotulos.mes_anterior} agora={temAgora ? (feitoPorLoja.get(l.loja_id ?? 0) ?? 0) : null} aoTocar={() => setAberta(l.loja_id ?? null)} />
                  </Entrada>
                ))
              ) : (
                <EstadoVazio icone="storefront-outline" titulo="Nenhuma loja" texto="Nada com esse filtro." />
              )}
              {agora.data?.erro ? (
                <Texto variante="legenda" tom="fraco">
                  Banco de vendas fora do ar: o “até agora” mostra o realizado já importado do mês.
                </Texto>
              ) : null}
            </>
          )}

          {lojaAberta && doPilar ? (
            <FolhaLoja key={`${lojaAberta.loja_id}-${pilar}`} comp={doPilar} linha={lojaAberta} feito={temAgora ? (feitoPorLoja.get(lojaAberta.loja_id ?? 0) ?? 0) : null} aoFechar={() => setAberta(null)} />
          ) : null}
          {preenchendo && doPilar ? <FolhaPreencher comp={doPilar} aoFechar={() => setPreenchendo(false)} /> : null}
        </>
      ) : null}
    </TelaModulo>
  );
}

function Aviso({ texto }: { texto: string }) {
  return (
    <View style={{ flexDirection: 'row', gap: 10, padding: 12, borderRadius: 12, backgroundColor: comAlfa(estado.info, 0.1) }}>
      <Icone nome="information-circle-outline" tamanho={18} cor={estado.info} />
      <Texto variante="legenda" tom="suave" className="flex-1">
        {texto}
      </Texto>
    </View>
  );
}

/** Detalhe da loja no pilar + edição (quem edita, competência aberta). */
function FolhaLoja({ comp, linha, feito, aoFechar }: { comp: Competencia; linha: LinhaMeta; feito: number | null; aoFechar: () => void }) {
  const cores = useCores();
  const fixa = comp.fixa;
  const salvar = useSalvarMeta(comp.codigo, comp.pilar);
  const dividir = useDividirMeta(comp.codigo, comp.pilar);
  const [meta, setMeta] = useState(paraCampo(linha.meta));
  const [cns, setCns] = useState(linha.qtd_cns == null ? '' : String(linha.qtd_cns));
  const [decisao, setDecisao] = useState<Decisao>(linha.decisao ?? 'CIMA');
  const [divisao, setDivisao] = useState(linha.divisao ?? []);
  const ocupado = salvar.isPending || dividir.isPending;

  function campos(): Partial<Record<CampoMeta, string>> {
    const c: Partial<Record<CampoMeta, string>> = {};
    if (meta !== paraCampo(linha.meta)) c.meta = meta;
    if (fixa && cns !== (linha.qtd_cns == null ? '' : String(linha.qtd_cns))) c.qtd_cns = cns;
    if (fixa && decisao !== (linha.decisao ?? 'CIMA')) c.decisao = decisao;
    return c;
  }

  async function gravar() {
    const mudou = Object.entries(campos()) as [CampoMeta, string][];
    if (!mudou.length) return aoFechar();
    try {
      for (const [campo, valor] of mudou) await salvar.mutateAsync({ loja: linha.loja_id ?? 0, campo, valor });
      vibrar();
      aoFechar();
    } catch (e) {
      Alert.alert('Não deu certo', mensagemDoErro(e));
    }
  }

  function gravarEDividir() {
    dividir.mutate(
      { loja: linha.loja_id ?? 0, campos: { meta, ...(fixa ? { qtd_cns: cns, decisao } : {}) } },
      {
        onSuccess: (r) => {
          vibrar();
          setDivisao(r.divisao.itens.map((i) => ({ nome: i.nome, percentual: i.percentual, meta: i.meta })));
          Alert.alert('Meta salva', r.mensagem);
        },
        onError: (e) => Alert.alert('Não deu certo', mensagemDoErro(e)),
      },
    );
  }

  const rot = comp.rotulos;
  return (
    <Folha aberta titulo={linha.loja ?? 'Loja'} aoFechar={aoFechar}>
      <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={{ paddingHorizontal: 20, paddingBottom: 12, gap: 14 }}>
        <Texto variante="legenda" tom="fraco">
          {comp.pilar_nome} · {linha.regiao ?? 'sem região'}
        </Texto>
        <Cartao style={{ paddingHorizontal: 14, paddingVertical: 4 }}>
          {rot.meses.map((m, i) => (
            <LinhaValor key={m} rotulo={`Realizado ${m}${fixa ? ' (TV + Fixa)' : ''}`} valor={valorDoPilar(linha.meses[i], fixa)} />
          ))}
          <LinhaValor rotulo="Média do quadrimestre" valor={valorDoPilar(linha.media_quad, fixa)} destaque />
          <LinhaValor rotulo={`Real ${rot.ano_anterior}`} valor={valorDoPilar(linha.real_ano_anterior, fixa)} />
          {fixa ? (
            <>
              <LinhaValor rotulo="Média R$ da Fixa" valor={reais(linha.media_quad_valor)} />
              <LinhaValor rotulo={linha.ticket_da_rede ? 'Ticket médio (da rede)' : 'Ticket médio'} valor={reais(linha.ticket)} />
            </>
          ) : null}
          <LinhaValor rotulo="Meta Vivo" valor={linha.meta_vivo == null ? '—' : fixa ? numero(linha.meta_vivo, 1) : valorDoPilar(linha.meta_vivo, false)} />
          <LinhaValor rotulo={`Meta ${rot.mes_anterior}`} valor={valorDoPilar(linha.meta_anterior, fixa)} />
          {fixa ? (
            <>
              <LinhaValor rotulo="QTD (meta R$ ÷ ticket)" valor={numero(linha.qtd)} />
              <LinhaValor rotulo="Por CN ↑ / ↓" valor={`${numero(linha.cima)} / ${numero(linha.baixo)}`} />
              <LinhaValor rotulo="Nova meta (qtd)" valor={numero(linha.nova_meta)} destaque />
            </>
          ) : null}
          {feito != null ? <LinhaValor rotulo={`Realizado até agora (${rot.competencia})`} valor={valorDoPilar(feito, fixa)} cor={estado.sucesso} /> : null}
        </Cartao>
        {linha.alvo != null ? <Variacoes linha={linha} /> : null}

        {comp.pode_editar ? (
          <View className="gap-3">
            <Campo rotulo={fixa ? `Meta ${rot.competencia} (R$ da Fixa)` : `Meta ${rot.competencia}`} icone="flag-outline" keyboardType="decimal-pad" placeholder="—" value={meta} onChangeText={setMeta} />
            {fixa ? (
              <>
                <Campo rotulo="CNs (consultores)" icone="people-outline" keyboardType="number-pad" placeholder="—" value={cns} onChangeText={setCns} />
                <View className="gap-1.5">
                  <Texto variante="legenda" tom="suave">
                    Decisão (arredondar a meta por CN)
                  </Texto>
                  <Segmentado
                    opcoes={[
                      { valor: 'CIMA' as Decisao, rotulo: 'Para cima' },
                      { valor: 'BAIXO' as Decisao, rotulo: 'Para baixo' },
                    ]}
                    valor={decisao}
                    aoMudar={setDecisao}
                  />
                </View>
              </>
            ) : null}
            <Botao titulo="Salvar" icone="checkmark" onPress={() => void gravar()} carregando={salvar.isPending} desativado={ocupado} />
            <Botao titulo="Salvar e dividir entre os CNs" variante="secundario" icone="git-branch-outline" onPress={gravarEDividir} carregando={dividir.isPending} desativado={ocupado} />
            <Texto variante="legenda" tom="fraco">
              Dividir usa os consultores do Quadro de HC da loja (metas de 100%, 80% e 60%).
            </Texto>
          </View>
        ) : (
          <Texto variante="legenda" tom="fraco">
            {comp.fechada ? 'Competência fechada: só leitura.' : 'Seu acesso é só de visualização.'}
          </Texto>
        )}

        {divisao.length ? (
          <Cartao style={{ paddingHorizontal: 14, paddingVertical: 6 }}>
            <Texto variante="rotulo" tom="fraco" style={{ paddingTop: 8 }}>
              Meta por consultor
            </Texto>
            {divisao.map((d) => (
              <LinhaValor key={d.nome} rotulo={`${d.nome} · ${d.percentual}%`} valor={valorDoPilar(d.meta, fixa)} />
            ))}
          </Cartao>
        ) : null}
        <View style={{ height: 1, backgroundColor: cores.bordaSuave }} />
      </ScrollView>
    </Folha>
  );
}

const ARREDONDAR = [
  { valor: '0', rotulo: 'Sem arredondar' },
  { valor: '10', rotulo: '10' },
  { valor: '100', rotulo: '100' },
  { valor: '1000', rotulo: '1.000' },
];

/** Preenchimento rápido: a meta de todas as lojas = referência × (1 + x%), arredondada. */
function FolhaPreencher({ comp, aoFechar }: { comp: Competencia; aoFechar: () => void }) {
  const preencher = usePreencherMetas(comp.codigo);
  const [origem, setOrigem] = useState(comp.origens[0]?.valor ?? 'media_quad');
  const [percentual, setPercentual] = useState('0');
  const [arredondar, setArredondar] = useState('0');
  const [vazias, setVazias] = useState(true);

  function aplicar() {
    preencher.mutate(
      { pilar: comp.pilar, origem, percentual: percentual.replace(',', '.') || '0', arredondar, somente_vazias: vazias },
      {
        onSuccess: (r) => (vibrar(), aoFechar(), Alert.alert('Pronto', r.mensagem)),
        onError: (e) => Alert.alert('Não deu certo', mensagemDoErro(e)),
      },
    );
  }

  return (
    <Folha aberta titulo={`Preencher ${comp.pilar_nome}`} aoFechar={aoFechar}>
      <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={{ paddingHorizontal: 20, paddingBottom: 12, gap: 14 }}>
        <Texto variante="legenda" tom="suave">
          A meta de cada loja vira a referência escolhida × (1 + o percentual), arredondada.
        </Texto>
        <View className="flex-row flex-wrap gap-2">
          {comp.origens.map((o) => (
            <Chip key={o.valor} rotulo={o.rotulo} ativo={origem === o.valor} onPress={() => setOrigem(o.valor)} />
          ))}
        </View>
        <Campo rotulo="Percentual (+ ou −)" icone="trending-up-outline" keyboardType="numbers-and-punctuation" value={percentual} onChangeText={setPercentual} ajuda="Ex.: 5 = 5% acima; -3 = 3% abaixo." />
        <View className="gap-1.5">
          <Texto variante="legenda" tom="suave">
            Arredondar para
          </Texto>
          <View className="flex-row flex-wrap gap-2">
            {ARREDONDAR.map((a) => (
              <Chip key={a.valor} rotulo={a.rotulo} ativo={arredondar === a.valor} onPress={() => setArredondar(a.valor)} />
            ))}
          </View>
        </View>
        <Interruptor rotulo="Só as lojas sem meta" ajuda="Desligado, troca a meta de todas as lojas do pilar." valor={vazias} aoMudar={setVazias} />
        <Botao titulo="Preencher" icone="color-wand-outline" onPress={aplicar} carregando={preencher.isPending} />
      </ScrollView>
    </Folha>
  );
}
