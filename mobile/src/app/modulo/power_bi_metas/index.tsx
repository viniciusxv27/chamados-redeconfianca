/**
 * Metas — o /power-bi/metas/ no celular. O recorte é o da web: o consultor vê
 * as metas dele e a da loja; o gerente vê a loja, CN a CN; as demais
 * hierarquias veem a rede com filtro de loja, pilar e vendedor.
 * Subir, excluir e sincronizar a planilha ficam no portal.
 */
import { type Href, router } from 'expo-router';
import { useState } from 'react';
import { ScrollView, View } from 'react-native';

import { EsqueletoBloco, Indicador, TelaModulo } from '@/components/modulos/comum';
import { CartaoRanking, CartaoVendedor, PainelPilares } from '@/components/modulos/power_bi_metas/pecas';
import { BotaoIcone, Cartao, Chip, Entrada, EstadoVazio, Icone, Segmentado, Texto } from '@/components/ui';
import { Seletor } from '@/components/ui/seletor';
import { estado, marca } from '@/constants/theme';
import { useMetas } from '@/hooks/modulos/power_bi_metas';
import type { FiltroMetas, ResumoMetas } from '@/services/modulos/power_bi_metas';
import { reais } from '@/utils/dinheiro';
import { comAlfa } from '@/utils/formatar';
import { abrirNoPortal } from '@/utils/links';

const COR_CN = estado.info;
const COR_LOJA = estado.sucesso;

type Painel = 'cn' | 'loja';

/** Lista de textos → itens do Seletor (id 0 = todos). */
function itensDe(lista: string[], todos: string) {
  return [{ id: 0, nome: todos }, ...lista.map((nome, i) => ({ id: i + 1, nome }))];
}

function idDe(lista: string[], valor: string) {
  return valor ? lista.indexOf(valor) + 1 : 0;
}

export default function Metas() {
  const [filtro, setFiltro] = useState<FiltroMetas>({});
  const [painel, setPainel] = useState<Painel>('cn');
  const { data, isPending, error, refetch, isRefetching, isFetching } = useMetas(filtro);

  const subtitulo = data?.competencia ? `Competência ${data.competencia.rotulo}` : 'Metas do período';

  return (
    <TelaModulo
      esqueleto="painel"
      titulo="Metas"
      subtitulo={subtitulo}
      carregando={isPending}
      erro={error}
      aoAtualizar={refetch}
      atualizando={isRefetching}
      direita={<BotaoIcone icone="open-outline" rotulo="Abrir no portal" onPress={() => abrirNoPortal('/power-bi/metas/')} />}>
      {data && !data.competencia ? (
        <EstadoVazio icone="flag-outline" titulo="Nenhuma meta importada ainda" texto="Peça ao SUPERADMIN para subir a planilha de metas no portal." />
      ) : data ? (
        <>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8 }} style={{ marginHorizontal: -20 }}>
            <View style={{ width: 12 }} />
            {data.competencias.map((c) => (
              <Chip
                key={c.rotulo}
                rotulo={c.rotulo}
                ativo={data.competencia?.ano === c.ano && data.competencia?.mes === c.mes}
                onPress={() => setFiltro((f) => ({ ...f, ano: c.ano, mes: c.mes }))}
              />
            ))}
            <View style={{ width: 12 }} />
          </ScrollView>
          {data.competencia_trocada ? (
            <Texto variante="legenda" tom="suave">
              Não há carga para o mês pedido. Mostrando a mais recente.
            </Texto>
          ) : null}

          <Filtros resumo={data} filtro={filtro} setFiltro={setFiltro} />

          {isFetching && !isRefetching ? <EsqueletoBloco linhas={5} /> : <Conteudo resumo={data} painel={painel} setPainel={setPainel} />}

          {data.pode_gerenciar ? (
            <Cartao onPress={() => router.push('/modulo/power_bi_metas/cargas' as Href)} style={{ padding: 18 }}>
              <View className="flex-row items-center" style={{ gap: 14 }}>
                <View style={{ width: 40, height: 40, borderRadius: 12, backgroundColor: comAlfa(marca[500], 0.13), alignItems: 'center', justifyContent: 'center' }}>
                  <Icone nome="cloud-upload-outline" tamanho={20} cor={marca[500]} />
                </View>
                <View className="flex-1" style={{ gap: 2 }}>
                  <Texto variante="corpoForte">Cargas de metas</Texto>
                  <Texto variante="legenda" tom="suave">
                    Planilhas enviadas por competência
                  </Texto>
                </View>
                <Icone nome="chevron-forward" tamanho={18} cor={marca[500]} />
              </View>
            </Cartao>
          ) : null}
        </>
      ) : null}
    </TelaModulo>
  );
}

function Filtros({ resumo, filtro, setFiltro }: { resumo: ResumoMetas; filtro: FiltroMetas; setFiltro: (fn: (f: FiltroMetas) => FiltroMetas) => void }) {
  const f = resumo.filtros;
  if (resumo.perfil === 'gerente') {
    return (
      <Seletor
        rotulo="CN da loja"
        placeholder="Todos"
        itens={itensDe(f.cns, 'Todos')}
        valor={idDe(f.cns, filtro.cn ?? '')}
        aoEscolher={(id) => setFiltro((x) => ({ ...x, cn: id ? f.cns[id - 1] : undefined }))}
      />
    );
  }
  if (resumo.perfil !== 'rede') return null;
  return (
    <View style={{ gap: 12 }}>
      <View className="flex-row" style={{ gap: 12 }}>
        <View className="flex-1">
          <Seletor
            rotulo="Loja"
            placeholder="Todas"
            itens={itensDe(f.lojas, 'Todas')}
            valor={idDe(f.lojas, filtro.loja ?? '')}
            aoEscolher={(id) => setFiltro((x) => ({ ...x, loja: id ? f.lojas[id - 1] : undefined }))}
          />
        </View>
        <View className="flex-1">
          <Seletor
            rotulo="Pilar"
            placeholder="Todos"
            itens={itensDe(f.pilares, 'Todos')}
            valor={idDe(f.pilares, filtro.pilar ?? '')}
            aoEscolher={(id) => setFiltro((x) => ({ ...x, pilar: id ? f.pilares[id - 1] : undefined }))}
          />
        </View>
      </View>
      <Seletor
        rotulo="Vendedor"
        placeholder="Todos"
        itens={itensDe(f.vendedores, 'Todos')}
        valor={idDe(f.vendedores, filtro.vendedor ?? '')}
        aoEscolher={(id) => setFiltro((x) => ({ ...x, vendedor: id ? f.vendedores[id - 1] : undefined }))}
      />
    </View>
  );
}

function Conteudo({ resumo: r, painel, setPainel }: { resumo: ResumoMetas; painel: Painel; setPainel: (p: Painel) => void }) {
  return (
    <>
      <Entrada>
        <View className="flex-row flex-wrap" style={{ gap: 12 }}>
          {r.totais.meta !== null ? <Indicador rotulo="Meta total" valor={reais(r.totais.meta)} icone="flag-outline" cor={marca[500]} /> : null}
          <Indicador rotulo="Metas CN" valor={reais(r.totais.cn)} icone="person-outline" cor={COR_CN} />
          <Indicador rotulo="Meta da loja (PDV)" valor={reais(r.totais.loja)} icone="storefront-outline" cor={COR_LOJA} />
        </View>
      </Entrada>

      <Segmentado
        opcoes={[
          { valor: 'cn', rotulo: 'Painel CN' },
          { valor: 'loja', rotulo: 'Painel Loja' },
        ]}
        valor={painel}
        aoMudar={setPainel}
      />

      {r.fixa_quantidade ? (
        <Texto variante="legenda" tom="suave">
          FIXA aparece em quantidade e não entra nos totais em R$.
        </Texto>
      ) : null}

      {painel === 'cn' ? <PainelCn resumo={r} /> : <PainelLoja resumo={r} />}
    </>
  );
}

function PainelCn({ resumo: r }: { resumo: ResumoMetas }) {
  if (r.tabela_gerente) {
    const t = r.tabela_gerente;
    return (
      <>
        <Texto variante="rotulo" tom="fraco" className="mt-1">
          CNs da loja · toque para ver os pilares
        </Texto>
        {t.linhas.length === 0 ? (
          <EstadoVazio icone="people-outline" titulo="Sem metas CN" texto="Nenhum CN da sua loja nesta competência." />
        ) : (
          t.linhas.map((l, i) => (
            <Entrada key={l.vendedor} indice={i < 10 ? i : 0}>
              <CartaoVendedor vendedor={l.vendedor} total={l.total} colunas={t.colunas} celulas={l.celulas} cor={COR_CN} />
            </Entrada>
          ))
        )}
        {r.top_consultores ? <CartaoRanking ranking={r.top_consultores} cor={COR_CN} vazio="Sem metas CN para exibir." /> : null}
      </>
    );
  }
  return (
    <>
      <Entrada indice={1}>
        <PainelPilares titulo="Painel CN por pilar" linhas={r.painel_cn} cor={COR_CN} vazio="Sem metas CN para o período selecionado." />
      </Entrada>
      {r.top_consultores ? (
        <Entrada indice={2}>
          <CartaoRanking ranking={r.top_consultores} cor={COR_CN} vazio="Sem metas CN para exibir." />
        </Entrada>
      ) : null}
    </>
  );
}

function PainelLoja({ resumo: r }: { resumo: ResumoMetas }) {
  return (
    <>
      <Entrada indice={1}>
        <PainelPilares titulo="Painel Loja por pilar" linhas={r.painel_loja} cor={COR_LOJA} vazio="Sem meta de loja para o período selecionado." />
      </Entrada>
      {/* Para o consultor a web deixa "Minha loja" sempre vazio (views.py:1124): aqui some. */}
      {r.perfil === 'cn' && r.top_lojas.itens.length === 0 ? null : (
        <Entrada indice={2}>
          <CartaoRanking ranking={r.top_lojas} cor={COR_LOJA} vazio="Sem metas de loja para exibir." />
        </Entrada>
      )}
    </>
  );
}
