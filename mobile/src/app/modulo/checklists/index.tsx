import { router, type Href } from 'expo-router';
import { View } from 'react-native';

import { Indicador, TelaModulo } from '@/components/modulos/comum';
import { Atalho, CartaoExecucao, COR_CHECKLIST, diaPorExtenso, isoDoDia } from '@/components/modulos/checklists/pecas';
import { BotaoIcone, Cartao, Entrada, Icone, Texto } from '@/components/ui';
import { estado } from '@/constants/theme';
import { useInicioChecklists } from '@/hooks/modulos/checklists';
import { useCores } from '@/providers/tema';
import { abrirNoPortal } from '@/utils/links';

const ir = (pathname: string, params?: Record<string, string>) => router.push({ pathname, params } as unknown as Href);

/** Entrada do módulo: os checklists de hoje, o que ficou para trás e (para quem a web mostra) o acompanhamento. */
export default function Checklists() {
  const cores = useCores();
  const { data, isPending, isError, error, refetch, isRefetching } = useInicioChecklists();
  const c = data?.contadores;

  return (
    <TelaModulo
      esqueleto="painel"
      titulo="Checklists"
      subtitulo={diaPorExtenso(data?.hoje ?? isoDoDia(new Date()))}
      carregando={isPending}
      erro={isError ? error : undefined}
      aoAtualizar={() => void refetch()}
      atualizando={isRefetching}
      direita={<BotaoIcone icone="open-outline" rotulo="Abrir o portal completo" onPress={() => void abrirNoPortal(data?.url_web ?? '/checklists/')} />}>
      {data && c ? (
        <>
          <Entrada indice={0}>
            <View className="flex-row flex-wrap gap-3">
              <Indicador icone="list-outline" rotulo="Para fazer hoje" valor={String(c.hoje_abertos)} cor={c.hoje_abertos ? COR_CHECKLIST : cores.texto3} />
              <Indicador icone="checkmark-done-outline" rotulo="Enviados hoje" valor={String(c.hoje_enviados)} cor={estado.sucesso} />
            </View>
          </Entrada>

          {c.atrasados ? (
            <Entrada indice={1}>
              <Cartao onPress={() => ir('/modulo/checklists/lista', { filtro: 'pendentes' })} acessibilidade="Ver checklists atrasados" style={{ padding: 14, flexDirection: 'row', alignItems: 'center', gap: 10, borderColor: estado.perigo }}>
                <Icone nome="alert-circle-outline" tamanho={20} cor={estado.perigo} />
                <Texto variante="legenda" className="flex-1" style={{ color: estado.perigo }}>
                  {c.atrasados} checklist{c.atrasados > 1 ? 's' : ''} dos últimos {c.atrasados_dias} dias {c.atrasados > 1 ? 'ficaram' : 'ficou'} sem enviar
                </Texto>
                <Icone nome="chevron-forward" tamanho={18} cor={estado.perigo} />
              </Cartao>
            </Entrada>
          ) : null}

          <View className="gap-3">
            <Texto variante="rotulo" tom="fraco">
              Hoje
            </Texto>
            {data.execucoes.length ? (
              data.execucoes.map((e, i) => (
                <Entrada key={e.id} indice={i + 2}>
                  <CartaoExecucao e={e} mostrarData={false} />
                </Entrada>
              ))
            ) : (
              <Entrada indice={2}>
                <Cartao style={{ padding: 18, alignItems: 'center', gap: 6 }}>
                  <Icone nome="sunny-outline" tamanho={26} cor={cores.texto3} />
                  <Texto variante="corpoForte">Nenhum checklist para hoje</Texto>
                  <Texto variante="legenda" tom="suave" className="text-center">
                    Quando alguém atribuir um checklist para você, ele aparece aqui.
                  </Texto>
                </Cartao>
              </Entrada>
            )}
          </View>

          <View className="gap-3">
            <Entrada indice={6}>
              <Atalho
                icone="albums-outline"
                titulo="Meus checklists"
                detalhe={c.aguardando_aprovacao ? `${c.aguardando_aprovacao} aguardando aprovação` : 'Pendentes, enviados e o histórico'}
                onPress={() => ir('/modulo/checklists/lista')}
              />
            </Entrada>
            {data.gestor ? (
              <Entrada indice={7}>
                <Atalho icone="storefront-outline" titulo="Acompanhamento das lojas" detalhe="Quem fez e quem não fez no dia" cor={estado.info} onPress={() => ir('/modulo/checklists/acompanhamento')} />
              </Entrada>
            ) : null}
          </View>

          <Texto variante="legenda" tom="fraco" className="text-center">
            Modelos, atribuições e aprovações continuam no portal.
          </Texto>
        </>
      ) : null}
    </TelaModulo>
  );
}
