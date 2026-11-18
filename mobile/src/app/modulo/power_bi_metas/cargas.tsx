/**
 * Cargas de metas (só SUPERADMIN, como o /power-bi/manage/metas/): cada
 * planilha enviada por competência. Subir, excluir e sincronizar com o MySQL
 * ficam no portal.
 */
import { View } from 'react-native';

import { TelaModulo } from '@/components/modulos/comum';
import { Botao, BotaoIcone, Cartao, Entrada, EstadoVazio, Icone, Selo, Texto } from '@/components/ui';
import { estado, marca, raios } from '@/constants/theme';
import { useCargasMetas } from '@/hooks/modulos/power_bi_metas';
import { useCores } from '@/providers/tema';
import type { CargaMetas } from '@/services/modulos/power_bi_metas';
import { numero } from '@/utils/dinheiro';
import { comAlfa, dataHora } from '@/utils/formatar';
import { abrirNoPortal } from '@/utils/links';

export default function CargasMetas() {
  const { data, isPending, error, refetch, isRefetching } = useCargasMetas();
  const lista = data ?? [];
  return (
    <TelaModulo
      titulo="Cargas de metas"
      subtitulo={data ? `${lista.length} ${lista.length === 1 ? 'competência' : 'competências'}` : 'Planilhas enviadas'}
      carregando={isPending}
      erro={error}
      aoAtualizar={refetch}
      atualizando={isRefetching}
      direita={<BotaoIcone icone="open-outline" rotulo="Abrir no portal" onPress={() => abrirNoPortal('/power-bi/manage/metas/')} />}>
      <Botao titulo="Subir planilha no portal" icone="cloud-upload-outline" variante="secundario" onPress={() => abrirNoPortal('/power-bi/manage/metas/')} />
      {lista.length === 0 ? (
        <EstadoVazio icone="cloud-upload-outline" titulo="Nenhuma carga ainda" texto="Suba a planilha de metas pelo portal." />
      ) : (
        lista.map((c, i) => (
          <Entrada key={c.id} indice={i < 10 ? i : 0}>
            <CartaoCarga carga={c} />
          </Entrada>
        ))
      )}
    </TelaModulo>
  );
}

function CartaoCarga({ carga: c }: { carga: CargaMetas }) {
  const cores = useCores();
  return (
    <Cartao style={{ padding: 18, gap: 12 }}>
      <View className="flex-row items-center" style={{ gap: 14 }}>
        <View style={{ width: 44, height: 44, borderRadius: raios.md, backgroundColor: comAlfa(marca[500], 0.13), alignItems: 'center', justifyContent: 'center' }}>
          <Icone nome="calendar-outline" tamanho={20} cor={marca[500]} />
        </View>
        <View className="flex-1" style={{ gap: 2 }}>
          <Texto variante="titulo3">{c.rotulo}</Texto>
          <Texto variante="legenda" tom="suave" numberOfLines={1}>
            {c.arquivo || 'Arquivo sem nome'}
          </Texto>
        </View>
        {c.fixa_quantidade ? <Selo texto="FIXA em qtd." cor={estado.info} /> : null}
      </View>
      <View style={{ borderTopWidth: 1, borderTopColor: cores.bordaSuave, paddingTop: 10, gap: 4 }}>
        <Texto variante="legenda" tom="suave">
          {`${numero(c.itens)} ${c.itens === 1 ? 'item' : 'itens'} · atualizado ${dataHora(c.atualizado_em)}`}
        </Texto>
        {c.enviado_por ? (
          <Texto variante="legenda" tom="fraco">
            {`Enviado por ${c.enviado_por}`}
          </Texto>
        ) : null}
      </View>
    </Cartao>
  );
}
