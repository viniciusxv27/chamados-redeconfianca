import { router, type Href } from 'expo-router';
import { View } from 'react-native';

import { Atalho, AZUL_PONTO, Batidas, diaPorExtenso, Faixa, isoDoDia, saldo } from '@/components/modulos/ponto/pecas';
import { EsqueletoBloco, Indicador, TelaModulo } from '@/components/modulos/comum';
import { Botao, BotaoIcone, Cartao, Entrada, Icone, Texto } from '@/components/ui';
import { estado, fontes, marca } from '@/constants/theme';
import { useMarcacoes, useResumoPonto } from '@/hooks/modulos/ponto';
import { useCores } from '@/providers/tema';
import { mensagemDoErro } from '@/services/api';
import type { PainelPonto } from '@/services/modulos/ponto';
import { comAlfa } from '@/utils/formatar';
import { abrirNoPortal } from '@/utils/links';

const ir = (pathname: string, params?: Record<string, string>) => router.push({ pathname, params } as unknown as Href);

const COR_SITUACAO: Record<NonNullable<PainelPonto['status']>['situacao'], string> = {
  SEM_ENTRADA: '#6B7280',
  TRABALHANDO: estado.sucesso,
  EM_INTERVALO: estado.alerta,
  ENCERRADO: AZUL_PONTO,
};

/** Entrada do módulo: o dia de hoje, pendências e os atalhos que a pessoa pode abrir. */
export default function Ponto() {
  const resumo = useResumoPonto();
  const comPonto = !!resumo.data?.secoes.marcacoes;
  const painel = useMarcacoes(comPonto);
  const hoje = painel.data?.hoje ?? isoDoDia(new Date());

  function atualizar() {
    void resumo.refetch();
    if (comPonto) void painel.refetch();
  }

  const r = resumo.data;
  return (
    <TelaModulo
      titulo="Ponto"
      subtitulo={diaPorExtenso(hoje)}
      carregando={resumo.isPending}
      erro={resumo.isError ? resumo.error : undefined}
      aoAtualizar={atualizar}
      atualizando={resumo.isRefetching || painel.isRefetching}
      direita={<BotaoIcone icone="open-outline" rotulo="Abrir o portal completo" onPress={() => abrirNoPortal('/ponto/')} />}>
      {r ? (
        <>
          {comPonto ? <Hoje painel={painel.data} carregando={painel.isPending} erro={painel.isError ? painel.error : null} aoTentar={() => void painel.refetch()} /> : null}

          <Entrada indice={2}>
            <Botao titulo="Nova solicitação ao DP" icone="add-circle-outline" onPress={() => ir('/modulo/ponto/nova')} />
          </Entrada>

          <View className="gap-3">
            {comPonto ? (
              <Entrada indice={3}>
                <Atalho icone="calendar-outline" titulo="Marcações do período" detalhe="Últimos 28 dias, dia a dia, com previsto e realizado" onPress={() => ir('/modulo/ponto/marcacoes')} />
              </Entrada>
            ) : null}
            <Entrada indice={4}>
              <Atalho
                icone="document-text-outline"
                titulo="Minhas solicitações"
                detalhe={
                  r.solicitacoes.devolvidas
                    ? `${r.solicitacoes.devolvidas} devolvida${r.solicitacoes.devolvidas > 1 ? 's' : ''} para corrigir`
                    : r.solicitacoes.abertas
                      ? `${r.solicitacoes.abertas} em andamento`
                      : 'Ajustes de ponto, atestados e declarações'
                }
                contador={r.solicitacoes.devolvidas || undefined}
                onPress={() => ir('/modulo/ponto/solicitacoes')}
              />
            </Entrada>
            {r.secoes.caixa_dp ? (
              <Entrada indice={5}>
                <Atalho icone="file-tray-full-outline" titulo="Caixa do DP" detalhe="Analisar, aprovar ou devolver" cor={marca[500]} contador={r.dp_pendentes || undefined} onPress={() => ir('/modulo/ponto/caixa')} />
              </Entrada>
            ) : null}
            {r.secoes.ferias ? (
              <Entrada indice={6}>
                <Atalho icone="sunny-outline" titulo="Minhas férias" detalhe="Saldo, períodos e próximas férias" cor="#14B8A6" onPress={() => ir('/modulo/ponto/ferias')} />
              </Entrada>
            ) : null}
            <Entrada indice={7}>
              <Atalho icone="grid-outline" titulo="Minha escala" detalhe="Horários da semana montados pela loja" cor="#8B5CF6" onPress={() => ir('/modulo/ponto/escala')} />
            </Entrada>
          </View>

          {comPonto ? (
            <Texto variante="legenda" tom="fraco" className="text-center">
              Para bater o ponto, use o relógio ou o app da Sólides.
            </Texto>
          ) : null}
        </>
      ) : null}
    </TelaModulo>
  );
}

function Hoje({ painel, carregando, erro, aoTentar }: { painel?: PainelPonto; carregando: boolean; erro: unknown; aoTentar: () => void }) {
  const cores = useCores();
  if (carregando) return <EsqueletoBloco linhas={4} />;
  if (erro || !painel) {
    return (
      <Cartao style={{ padding: 16, gap: 10 }}>
        <Faixa icone="cloud-offline-outline" texto={erro ? mensagemDoErro(erro) : 'Não deu para carregar o ponto.'} cor={estado.perigo} />
        <Botao titulo="Tentar de novo" variante="secundario" icone="refresh" tamanho="sm" onPress={aoTentar} />
      </Cartao>
    );
  }
  if (!painel.integracao_ativa) return <Faixa icone="power-outline" texto="A integração com o ponto está desligada." cor={cores.texto3} />;
  if (!painel.vinculado) return <Faixa icone="link-outline" texto="Seu usuário ainda não está ligado ao ponto da Sólides. Fale com o RH." cor={estado.alerta} />;

  const st = painel.status;
  const cor = st ? COR_SITUACAO[st.situacao] : cores.texto3;
  return (
    <View className="gap-4">
      {painel.isento ? <Faixa icone="checkmark-circle-outline" texto="Você está dispensado de bater ponto." cor={estado.sucesso} /> : null}
      {painel.erro ? <Faixa icone="alert-circle-outline" texto={`O ponto não respondeu: ${painel.erro}`} cor={estado.perigo} /> : null}

      {st ? (
        <Entrada indice={0}>
          <Cartao style={{ padding: 18, gap: 14 }}>
            <View className="flex-row items-center gap-3">
              <View style={{ width: 44, height: 44, borderRadius: 14, backgroundColor: comAlfa(cor, 0.14), alignItems: 'center', justifyContent: 'center' }}>
                <Icone nome={st.dentro ? 'play-circle' : st.situacao === 'EM_INTERVALO' ? 'cafe-outline' : 'time-outline'} tamanho={24} cor={cor} />
              </View>
              <View className="flex-1">
                <Texto variante="rotulo" tom="fraco">
                  Hoje
                </Texto>
                <Texto variante="corpoForte" numberOfLines={2}>
                  {st.rotulo}
                </Texto>
              </View>
              <View className="items-end">
                <Texto variante="titulo2" style={{ fontVariant: ['tabular-nums'] }}>
                  {st.trabalhado}
                </Texto>
                <Texto variante="legenda" tom="fraco">
                  trabalhado
                </Texto>
              </View>
            </View>
            <Batidas eventos={st.eventos} vazio="Nenhuma batida hoje ainda." />
            <View className="flex-row flex-wrap gap-x-4 gap-y-2">
              {st.marcos.map((m) => (
                <View key={m.rotulo} className="flex-row items-center gap-1.5">
                  <Icone nome={m.feito ? 'checkmark-circle' : 'ellipse-outline'} tamanho={16} cor={m.feito ? estado.sucesso : cores.texto3} />
                  <Texto variante="legenda" tom={m.feito ? 'padrao' : 'fraco'}>
                    {m.rotulo}
                  </Texto>
                </View>
              ))}
            </View>
          </Cartao>
        </Entrada>
      ) : null}

      {painel.pendencias.length ? (
        <Entrada indice={1}>
          <Cartao style={{ padding: 16, gap: 12, borderColor: comAlfa(estado.alerta, 0.45) }}>
            <View className="flex-row items-center gap-2">
              <Icone nome="warning-outline" tamanho={18} cor={estado.alerta} />
              <Texto variante="corpoForte" className="flex-1" style={{ color: estado.alerta }}>
                {painel.pendencias.length} dia{painel.pendencias.length > 1 ? 's' : ''} com ponto em aberto
              </Texto>
            </View>
            <Texto variante="legenda" tom="suave">
              Você bateu a entrada e não bateu a saída. Peça o ajuste ao DP:
            </Texto>
            {painel.pendencias.map((p) => (
              <View key={p.entrada} className="flex-row items-center gap-3">
                <View className="flex-1">
                  <Texto variante="corpoForte" style={{ textTransform: 'capitalize' }}>
                    {diaPorExtenso(p.dia)}
                  </Texto>
                  <Texto variante="legenda" tom="fraco">
                    Entrada às {p.hora}, sem saída
                  </Texto>
                </View>
                <Botao titulo="Pedir ajuste" tamanho="sm" variante="secundario" larguraTotal={false} onPress={() => ir('/modulo/ponto/nova', { tipo: 'AJUSTE', data: p.dia })} />
              </View>
            ))}
          </Cartao>
        </Entrada>
      ) : null}

      {painel.semana ? (
        <Entrada indice={1}>
          <View className="flex-row flex-wrap gap-3">
            <Indicador icone="stopwatch-outline" rotulo="Trabalhado na semana" valor={painel.semana.feito ?? '—'} cor={AZUL_PONTO} detalhe={painel.semana.previsto ? `Previsto ${painel.semana.previsto}` : undefined} />
            <Indicador
              icone="scale-outline"
              rotulo="Saldo nos últimos 28 dias"
              valor={saldo(painel.periodo?.diferenca_segundos)}
              cor={(painel.periodo?.diferenca_segundos ?? 0) < 0 ? estado.perigo : estado.sucesso}
              detalhe={painel.tem_jornada ? 'Hoje ainda não conta' : 'Sem jornada prevista'}
            />
          </View>
        </Entrada>
      ) : null}

      {painel.ajustes_solides.ligado && painel.ajustes_solides.pedidos.some((p) => p.status === 'PENDENTE' || p.status === 'ERRO') ? (
        <Texto variante="legenda" tom="suave" style={{ fontFamily: fontes.seminegrito }}>
          Você tem pedido de ajuste aguardando na Sólides — veja em Marcações do período.
        </Texto>
      ) : null}
    </View>
  );
}
