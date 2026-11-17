import { type Href, router } from 'expo-router';
import { useState } from 'react';
import { View } from 'react-native';

import { TelaModulo } from '@/components/modulos/comum';
import { BotaoIcone, Cartao, Chip, Entrada, EstadoVazio, Icone, Segmentado, Selo, Texto } from '@/components/ui';
import { estado } from '@/constants/theme';
import { useContracheques } from '@/hooks/modulos/contracheque';
import type { HoleriteResumo, InformeResumo } from '@/services/modulos/contracheque';
import { useCores } from '@/providers/tema';
import { reais } from '@/utils/dinheiro';
import { comAlfa } from '@/utils/formatar';
import { abrirNoPortal } from '@/utils/links';

type Aba = 'holerites' | 'informes';

export default function Contracheques() {
  const { data, isPending, error, refetch, isRefetching } = useContracheques();
  const [aba, setAba] = useState<Aba>('holerites');
  const [ano, setAno] = useState<number | null>(null);

  const holerites = (data?.contracheques ?? []).filter((h) => ano == null || h.ano === ano);
  const porAno = new Map<number, HoleriteResumo[]>();
  for (const h of holerites) porAno.set(h.ano, [...(porAno.get(h.ano) ?? []), h]);

  return (
    <TelaModulo
      titulo="Contracheque"
      subtitulo="Meus holerites"
      carregando={isPending}
      erro={error}
      aoAtualizar={refetch}
      atualizando={isRefetching}
      direita={data?.administra ? <BotaoIcone icone="settings-outline" rotulo="Administrar no portal" onPress={() => abrirNoPortal('/contracheque/admin/')} /> : undefined}>
      {data ? (
        <>
          <Segmentado
            opcoes={[
              { valor: 'holerites', rotulo: `Contracheques (${data.contracheques.length})` },
              { valor: 'informes', rotulo: `Informes (${data.informes.length})` },
            ]}
            valor={aba}
            aoMudar={setAba}
          />

          {aba === 'holerites' ? (
            <>
              {data.pendentes > 0 ? <AvisoPendentes quantos={data.pendentes} /> : null}
              {data.anos.length > 1 ? (
                <View className="flex-row flex-wrap gap-2">
                  <Chip rotulo="Todos" ativo={ano == null} onPress={() => setAno(null)} />
                  {data.anos.map((a) => (
                    <Chip key={a} rotulo={String(a)} ativo={ano === a} onPress={() => setAno(a)} />
                  ))}
                </View>
              ) : null}
              {holerites.length === 0 ? (
                <EstadoVazio icone="receipt-outline" titulo="Nenhum contracheque" texto="Quando o RH importar seus holerites, eles aparecem aqui." />
              ) : (
                [...porAno.entries()].map(([anoGrupo, lista]) => (
                  <View key={anoGrupo} className="gap-3">
                    <Texto variante="rotulo" tom="fraco" className="mt-1">
                      {anoGrupo}
                    </Texto>
                    {lista.map((h, i) => (
                      <Entrada key={h.id} indice={i}>
                        <CartaoHolerite holerite={h} />
                      </Entrada>
                    ))}
                  </View>
                ))
              )}
            </>
          ) : data.informes.length === 0 ? (
            <EstadoVazio icone="document-text-outline" titulo="Nenhum informe de rendimentos" texto="O informe do ano aparece aqui quando o RH publicar." />
          ) : (
            data.informes.map((inf, i) => (
              <Entrada key={inf.id} indice={i}>
                <CartaoInforme informe={inf} />
              </Entrada>
            ))
          )}
        </>
      ) : null}
    </TelaModulo>
  );
}

function AvisoPendentes({ quantos }: { quantos: number }) {
  const cores = useCores();
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, padding: 14, borderRadius: 14, backgroundColor: comAlfa(estado.alerta, 0.12) }}>
      <Icone nome="create-outline" tamanho={20} cor={estado.alerta} />
      <Texto variante="legenda" style={{ color: cores.texto }} className="flex-1">
        {quantos === 1 ? '1 contracheque espera a sua assinatura.' : `${quantos} contracheques esperam a sua assinatura.`}
      </Texto>
    </View>
  );
}

function CartaoHolerite({ holerite: h }: { holerite: HoleriteResumo }) {
  const cores = useCores();
  return (
    <Cartao
      acessibilidade={`Contracheque de ${h.competencia}`}
      onPress={() => router.push({ pathname: '/modulo/contracheque/[id]', params: { id: String(h.id) } } as unknown as Href)}
      style={{ padding: 16, gap: 10 }}>
      <View className="flex-row items-center justify-between gap-3">
        <Texto variante="corpoForte" className="flex-1">
          {h.competencia}
        </Texto>
        <Selo texto={h.assinado ? 'Assinado' : 'Não assinado'} cor={h.assinado ? estado.sucesso : estado.perigo} />
      </View>
      {h.liquido && Number(h.liquido) ? (
        <View>
          <Texto variante="legenda" tom="fraco">
            Líquido
          </Texto>
          <Texto variante="titulo2" style={{ color: estado.sucesso }}>
            {reais(h.liquido)}
          </Texto>
        </View>
      ) : null}
      <View className="flex-row items-center justify-between gap-3">
        <View className="flex-row flex-wrap gap-x-4 gap-y-1">
          {h.proventos && Number(h.proventos) ? (
            <Texto variante="legenda" tom="suave">
              + {reais(h.proventos)}
            </Texto>
          ) : null}
          {h.descontos && Number(h.descontos) ? (
            <Texto variante="legenda" tom="suave">
              − {reais(h.descontos)}
            </Texto>
          ) : null}
        </View>
        <Icone nome="chevron-forward" tamanho={18} cor={cores.texto3} />
      </View>
    </Cartao>
  );
}

function CartaoInforme({ informe: inf }: { informe: InformeResumo }) {
  const cores = useCores();
  return (
    <Cartao
      acessibilidade={`Informe de rendimentos ${inf.ano_base}`}
      onPress={() => router.push({ pathname: '/modulo/contracheque/informe/[id]', params: { id: String(inf.id) } } as unknown as Href)}
      style={{ padding: 16, gap: 8 }}>
      <View className="flex-row items-center justify-between gap-3">
        <View className="flex-1">
          <Texto variante="corpoForte">Ano-calendário {inf.ano_base}</Texto>
          <Texto variante="legenda" tom="fraco">
            Exercício {inf.exercicio}
          </Texto>
        </View>
        <Icone nome="chevron-forward" tamanho={18} cor={cores.texto3} />
      </View>
      <View className="flex-row flex-wrap gap-x-4 gap-y-1">
        <Texto variante="legenda" tom="suave">
          Rendimentos {reais(inf.total_rendimentos)}
        </Texto>
        <Texto variante="legenda" tom="suave">
          IRRF {reais(inf.irrf)}
        </Texto>
      </View>
    </Cartao>
  );
}
