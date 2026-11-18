import { type Href, router } from 'expo-router';
import { useState } from 'react';
import { View } from 'react-native';

import { EsqueletoBloco, Indicador, LinhaValor, TelaModulo } from '@/components/modulos/comum';
import { Atalho, CartaoPilar, CartaoTotal, PilaresAparte, SeletorReferencia } from '@/components/modulos/comissao/pecas';
import { BotaoIcone, Cartao, Entrada, EstadoVazio, Texto } from '@/components/ui';
import { Seletor } from '@/components/ui/seletor';
import { estado } from '@/constants/theme';
import { useComissao, usePessoasComissao } from '@/hooks/modulos/comissao';
import type { DadosPlanilha, FiltroComissao, ResumoComissao } from '@/services/modulos/comissao';
import { reais } from '@/utils/dinheiro';
import { abrirNoPortal } from '@/utils/links';

/** Item "eu" do seletor de pessoa (o gestor volta para a própria comissão). */
const EU = 0;

export default function Comissao() {
  const [filtro, setFiltro] = useState<FiltroComissao>({});
  const { data, isPending, error, refetch, isRefetching, isFetching } = useComissao(filtro);
  const referencia = { ano: filtro.ano, mes: filtro.mes, fase: filtro.fase };
  const pessoas = usePessoasComissao(referencia, !!data?.pode_ver_outros);

  const subtitulo = data?.referencia ? `${data.referencia.rotulo} · ${data.referencia.fase_rotulo}` : 'Minha comissão';
  const itensPessoa = [
    ...(data?.papel === 'superadmin' ? [] : [{ id: EU, nome: 'Minha comissão' }]),
    ...(pessoas.data ?? []).map((p) => ({ id: p.id, nome: p.nome, detalhe: [p.papel, p.setor].filter(Boolean).join(' · ') })),
  ];

  return (
    <TelaModulo
      esqueleto="painel"
      titulo="Comissão"
      subtitulo={subtitulo}
      carregando={isPending}
      erro={error}
      aoAtualizar={refetch}
      atualizando={isRefetching}
      direita={<BotaoIcone icone="open-outline" rotulo="Abrir no portal" onPress={() => abrirNoPortal('/users/commission/')} />}>
      {data && !data.liberado ? (
        <EstadoVazio icone="hourglass-outline" titulo="Ainda não liberado" texto={data.aviso ?? undefined} />
      ) : data ? (
        <>
          <SeletorReferencia
            opcoes={data.opcoes}
            atual={data.referencia}
            aoEscolher={(r) => setFiltro((f) => ({ ...f, ano: r.ano, mes: r.mes, fase: r.fase }))}
          />
          {data.referencia?.selecao_invalida ? (
            <Texto variante="legenda" tom="suave">
              A referência pedida não está liberada para você. Mostrando a mais recente.
            </Texto>
          ) : null}
          {data.pode_ver_outros ? (
            <Seletor
              rotulo="Ver a comissão de"
              placeholder={data.papel === 'superadmin' ? 'Escolha uma pessoa' : 'Minha comissão'}
              itens={itensPessoa}
              valor={filtro.usuario ?? (data.papel === 'superadmin' ? null : EU)}
              carregando={pessoas.isPending}
              aoEscolher={(id) => setFiltro((f) => ({ ...f, usuario: id === EU ? undefined : id }))}
            />
          ) : null}

          {isFetching && !isRefetching ? <EsqueletoBloco linhas={4} /> : <Conteudo resumo={data} />}

          <View className="mt-2 gap-3">
            <Atalho icone="time-outline" titulo="Histórico" texto="O que cada mês pagou" onPress={() => router.push('/modulo/comissao/historico' as Href)} />
            {data.pode_projecao ? (
              <Atalho icone="stats-chart-outline" titulo="Médias de comissão" texto="Projeção do mês pelo simulador" onPress={() => router.push('/modulo/comissao/projecao' as Href)} />
            ) : null}
          </View>
        </>
      ) : null}
    </TelaModulo>
  );
}

function Conteudo({ resumo }: { resumo: ResumoComissao }) {
  if (resumo.precisa_escolher) {
    return <EstadoVazio icone="people-outline" titulo="Escolha uma pessoa" texto="Selecione acima de quem você quer ver o comissionamento." />;
  }
  if (resumo.erro || !resumo.dados) {
    return <EstadoVazio icone="document-text-outline" titulo="Sem dados neste mês" texto={resumo.erro ?? 'Nada encontrado na planilha.'} />;
  }
  const quem = resumo.vendo_outro && resumo.alvo ? resumo.alvo.nome : undefined;
  if (resumo.dados.tipo === 'aparte') {
    const d = resumo.dados;
    return (
      <>
        <Entrada>
          <CartaoTotal rotulo="Comissão do mês" valor={d.total} linha={[quem, `Salário base ${reais(d.salario_base)}`].filter(Boolean).join(' · ')} />
        </Entrada>
        {!d.tem_configuracao ? (
          <Texto variante="legenda" tom="suave">
            Sem fatores cadastrados: valem os padrões.
          </Texto>
        ) : null}
        <Texto variante="rotulo" tom="fraco" className="mt-2">
          Atingimento da rede por pilar
        </Texto>
        <PilaresAparte dados={d} />
      </>
    );
  }
  return <ConteudoPlanilha dados={resumo.dados} quem={quem} />;
}

function ConteudoPlanilha({ dados: d, quem }: { dados: DadosPlanilha; quem?: string }) {
  return (
    <>
      <Entrada>
        <CartaoTotal rotulo="RV total" valor={d.total} linha={[quem ?? d.nome, d.cargo || 'Consultor'].filter(Boolean).join(' · ')} />
      </Entrada>
      <Entrada indice={1}>
        <View className="flex-row flex-wrap gap-3">
          <Indicador rotulo="Total comissão" valor={reais(d.comissao_total)} icone="cash-outline" cor={estado.sucesso} />
          <Indicador rotulo="Total premiação" valor={reais(d.premiacao_total)} icone="trophy-outline" cor={estado.info} />
          <Indicador rotulo="PDV" valor={d.pdv || '—'} icone="storefront-outline" />
          <Indicador rotulo={d.coordenador_rotulo} valor={d.coordenador || '—'} icone="person-outline" />
        </View>
      </Entrada>

      <Texto variante="rotulo" tom="fraco" className="mt-2">
        Pilares · toque para ver o detalhe
      </Texto>
      {d.pilares.map((p, i) => (
        <Entrada key={p.chave} indice={i + 2}>
          <CartaoPilar pilar={p} />
        </Entrada>
      ))}

      <Cartao style={{ paddingHorizontal: 16, paddingVertical: 8 }}>
        <LinhaValor rotulo="Total dos pilares (com IQ)" valor={reais(d.total_pilares)} />
        <LinhaValor rotulo="Acelerador 6/7 pilares" valor={reais(d.aceleradores.seis_pilares)} />
        <LinhaValor rotulo="Campanha" valor={reais(d.aceleradores.campanha)} />
        {d.descontos.total ? <LinhaValor rotulo="Descontos" valor={reais(d.descontos.total)} cor={estado.perigo} /> : null}
        <LinhaValor rotulo="RV total" valor={reais(d.total)} destaque cor={estado.sucesso} />
      </Cartao>

      {d.descontos.total ? (
        <Cartao style={{ paddingHorizontal: 16, paddingVertical: 8 }}>
          <Texto variante="corpoForte" style={{ color: estado.perigo, paddingTop: 6 }}>
            Descontos
          </Texto>
          <LinhaValor rotulo="Advertência" valor={reais(d.descontos.advertencia)} />
          <LinhaValor rotulo="Excedente do mês anterior" valor={reais(d.descontos.excedente)} />
          <LinhaValor rotulo="Price" valor={reais(d.descontos.price)} />
        </Cartao>
      ) : null}
    </>
  );
}
