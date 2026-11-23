/**
 * Gestão de metas — o /gestao-metas/ no celular: as competências (mês da meta), da mais nova
 * para a mais antiga. Abrir uma competência mostra a meta de cada loja, pilar a pilar.
 * Abrir competência nova, importar planilhas, lojas e acessos ficam no portal.
 */
import { type Href, router } from 'expo-router';
import { View } from 'react-native';

import { Indicador, TelaModulo } from '@/components/modulos/comum';
import { BotaoIcone, Cartao, Entrada, EstadoVazio, Icone, Selo, Texto } from '@/components/ui';
import { estado, marca } from '@/constants/theme';
import { useInicioGestaoMetas } from '@/hooks/modulos/gestao_metas';
import { useCores } from '@/providers/tema';
import { comAlfa } from '@/utils/formatar';
import { abrirNoPortal } from '@/utils/links';

export default function GestaoMetas() {
  const cores = useCores();
  const { data, isPending, error, refetch, isRefetching } = useInicioGestaoMetas();
  const abertas = data?.competencias.filter((c) => !c.fechada).length ?? 0;

  return (
    <TelaModulo
      titulo="Gestão de metas"
      subtitulo={data ? `Seu acesso: ${data.papel_rotulo}` : 'Meta do mês de cada loja'}
      carregando={isPending}
      erro={error}
      aoAtualizar={refetch}
      atualizando={isRefetching}
      direita={<BotaoIcone icone="open-outline" rotulo="Abrir no portal" onPress={() => abrirNoPortal('/gestao-metas/')} />}>
      {data ? (
        <>
          <View className="flex-row flex-wrap gap-3">
            <Indicador rotulo="Competências abertas" valor={String(abertas)} icone="flag-outline" cor={marca[500]} />
            <Indicador rotulo="Lojas ativas" valor={String(data.lojas_ativas)} icone="storefront-outline" cor={estado.info} />
          </View>
          {data.competencias.length ? (
            data.competencias.map((c, i) => (
              <Entrada key={c.codigo} indice={i}>
                <Cartao
                  onPress={() => router.push({ pathname: '/modulo/gestao_metas/[codigo]', params: { codigo: c.codigo } } as unknown as Href)}
                  acessibilidade={`Competência ${c.rotulo}`}
                  style={{ padding: 16, flexDirection: 'row', alignItems: 'center', gap: 14 }}>
                  <View style={{ width: 46, height: 46, borderRadius: 14, backgroundColor: comAlfa(c.fechada ? '#6B7280' : marca[500], 0.13), alignItems: 'center', justifyContent: 'center' }}>
                    <Icone nome={c.fechada ? 'lock-closed-outline' : 'calendar-outline'} tamanho={21} cor={c.fechada ? '#6B7280' : marca[500]} />
                  </View>
                  <View className="flex-1 gap-1">
                    <Texto variante="corpoForte">{c.rotulo.charAt(0).toUpperCase() + c.rotulo.slice(1)}</Texto>
                    <Texto variante="legenda" tom="fraco">
                      {c.metas ? `${c.metas} metas lançadas` : 'Nenhuma meta lançada'}
                      {c.observacao ? ` · ${c.observacao}` : ''}
                    </Texto>
                  </View>
                  <Selo texto={c.status_nome} cor={c.fechada ? '#6B7280' : estado.sucesso} />
                  <Icone nome="chevron-forward" tamanho={18} cor={cores.texto3} />
                </Cartao>
              </Entrada>
            ))
          ) : (
            <EstadoVazio icone="flag-outline" titulo="Nenhuma competência" texto="Abra a primeira competência pelo portal." />
          )}
          <Texto variante="legenda" tom="fraco">
            Abrir competência nova, importar realizado e metas da Vivo, as lojas e os acessos continuam no portal.
          </Texto>
        </>
      ) : null}
    </TelaModulo>
  );
}
