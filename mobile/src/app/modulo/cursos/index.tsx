import { type Href, router } from 'expo-router';
import { useState } from 'react';
import { View } from 'react-native';

import { CartaoCurso } from '@/components/modulos/cursos/pecas';
import { Indicador, TelaModulo } from '@/components/modulos/comum';
import { BotaoIcone, Cartao, Entrada, EstadoVazio, Icone, Segmentado, Texto } from '@/components/ui';
import { estado, marca } from '@/constants/theme';
import { useMeusCursos } from '@/hooks/modulos/cursos';
import { useCores } from '@/providers/tema';
import { comAlfa } from '@/utils/formatar';

type Filtro = 'pendentes' | 'todos';

export default function Cursos() {
  const cores = useCores();
  const { data, isPending, error, refetch, isRefetching } = useMeusCursos();
  const [escolhido, setEscolhido] = useState<Filtro | null>(null);

  const r = data?.resumo;
  // Abre no que pede ação; sem nada pendente, mostra tudo.
  const filtro: Filtro = escolhido ?? (r && r.pendentes > 0 ? 'pendentes' : 'todos');
  const lista = (data?.cursos ?? []).filter((c) => filtro === 'todos' || !c.entregue);

  return (
    <TelaModulo
      titulo="Cursos"
      subtitulo="Faça o curso na Vivo e envie o comprovante aqui"
      carregando={isPending}
      erro={error}
      aoAtualizar={refetch}
      atualizando={isRefetching}
      esqueleto="painel"
      direita={data?.gestor ? <BotaoIcone icone="people-outline" rotulo="Quadro das lojas" onPress={() => router.push('/modulo/cursos/gestao' as Href)} /> : undefined}>
      {data && r ? (
        <>
          {r.total > 0 ? (
            <View className="flex-row flex-wrap gap-4">
              <Indicador rotulo="A enviar" valor={String(r.nao_fez)} icone="hourglass-outline" cor={estado.alerta} />
              <Indicador rotulo="Recusados" valor={String(r.recusados)} icone="arrow-undo-outline" cor={estado.perigo} />
              <Indicador rotulo="Em conferência" valor={String(r.conferir)} icone="time-outline" cor={estado.info} />
              <Indicador rotulo="Aprovados" valor={String(r.aprovados)} icone="checkmark-circle-outline" cor={estado.sucesso} />
            </View>
          ) : null}

          {r.pendentes > 0 ? (
            <Cartao style={{ padding: 18, gap: 6, borderColor: comAlfa(r.atrasados ? estado.perigo : estado.alerta, 0.4), backgroundColor: comAlfa(r.atrasados ? estado.perigo : estado.alerta, 0.08) }}>
              <View className="flex-row items-center gap-2">
                <Icone nome="warning-outline" tamanho={18} cor={r.atrasados ? estado.perigo : estado.alerta} />
                <Texto variante="corpoForte" className="flex-1">
                  {r.pendentes === 1 ? '1 curso sem comprovante' : `${r.pendentes} cursos sem comprovante`}
                </Texto>
              </View>
              <Texto variante="legenda" tom="suave">
                {r.atrasados ? `${r.atrasados === 1 ? '1 deles está' : `${r.atrasados} deles estão`} com o prazo vencido. ` : ''}
                {data.trava_portal ? 'Depois do prazo o portal fica travado até o comprovante chegar.' : 'Envie antes do prazo.'}
              </Texto>
            </Cartao>
          ) : null}

          {data.gestor ? (
            <Cartao onPress={() => router.push('/modulo/cursos/gestao' as Href)} acessibilidade="Abrir o quadro das lojas" style={{ padding: 18 }}>
              <View className="flex-row items-center gap-3">
                <View style={{ width: 40, height: 40, borderRadius: 12, backgroundColor: comAlfa(marca[500], 0.13), alignItems: 'center', justifyContent: 'center' }}>
                  <Icone nome="people-outline" tamanho={20} cor={marca[500]} />
                </View>
                <View className="flex-1">
                  <Texto variante="corpoForte">Quadro das lojas</Texto>
                  <Texto variante="legenda" tom="suave">
                    Quem fez e quem não fez, loja por loja
                  </Texto>
                </View>
                <Icone nome="chevron-forward" tamanho={18} cor={cores.texto3} />
              </View>
            </Cartao>
          ) : null}

          {r.total > 0 ? (
            <Segmentado
              opcoes={[
                { valor: 'pendentes', rotulo: `Sem comprovante (${r.pendentes})` },
                { valor: 'todos', rotulo: `Todos (${r.total})` },
              ]}
              valor={filtro}
              aoMudar={setEscolhido}
            />
          ) : null}

          {lista.length === 0 ? (
            <EstadoVazio
              icone="school-outline"
              titulo={r.total > 0 ? 'Tudo enviado' : 'Nenhum curso para você'}
              texto={r.total > 0 ? 'Todos os seus cursos já têm comprovante.' : 'Quando um curso for publicado para você, ele aparece aqui.'}
            />
          ) : (
            lista.map((c, i) => (
              <Entrada key={c.id} indice={i}>
                <CartaoCurso curso={c} aoAbrir={() => router.push({ pathname: '/modulo/cursos/[id]', params: { id: String(c.id) } } as unknown as Href)} />
              </Entrada>
            ))
          )}
        </>
      ) : null}
    </TelaModulo>
  );
}
