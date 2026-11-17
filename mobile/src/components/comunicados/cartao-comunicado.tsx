import { Image } from 'expo-image';
import { router } from 'expo-router';
import { View } from 'react-native';

import { Avatar, Cartao, Icone, Selo, Texto } from '@/components/ui';
import { estado, marca } from '@/constants/theme';
import { useCores } from '@/providers/tema';
import type { ComunicadoResumo } from '@/services/tipos';
import { tempoRelativo } from '@/utils/formatar';

import { BotaoCurtir } from './botao-curtir';

/** Comunicado no feed: imagem de capa (se houver), título, resumo, curtir. */
export function CartaoComunicado({ comunicado, compacto = false }: { comunicado: ComunicadoResumo; compacto?: boolean }) {
  const cores = useCores();
  const abrir = () => router.push({ pathname: '/comunicado/[id]', params: { id: String(comunicado.id) } });

  return (
    <Cartao onPress={abrir} acessibilidade={`Comunicado: ${comunicado.titulo}`} style={{ overflow: 'hidden' }}>
      {comunicado.imagem ? (
        <Image source={{ uri: comunicado.imagem }} style={{ width: '100%', aspectRatio: compacto ? 2 : 16 / 9, backgroundColor: cores.superficie3 }} contentFit="cover" transition={250} />
      ) : null}
      <View className="gap-2 p-4">
        <View className="flex-row flex-wrap items-center gap-2">
          {comunicado.fixado ? <Selo texto="Fixado" cor={marca[500]} ponto={false} /> : null}
          {comunicado.obrigatorio && comunicado.situacao !== 'ESTOU_CIENTE' ? <Selo texto="Leitura obrigatória" cor={estado.perigo} /> : null}
          {comunicado.situacao === 'ESTOU_CIENTE' ? <Selo texto="Ciente" cor={estado.sucesso} ponto={false} /> : null}
        </View>
        <Texto variante="titulo3" numberOfLines={2} style={comunicado.cor_titulo && comunicado.cor_titulo !== '#000000' ? { color: comunicado.cor_titulo } : undefined}>
          {comunicado.titulo}
        </Texto>
        {!compacto && comunicado.resumo ? (
          <Texto tom="suave" numberOfLines={3}>
            {comunicado.resumo}
          </Texto>
        ) : null}
        <View className="mt-1 flex-row items-center justify-between">
          <View className="flex-1 flex-row items-center gap-2">
            {comunicado.autor ? <Avatar nome={comunicado.autor.nome} foto={comunicado.autor.foto} tamanho={22} /> : <Icone nome="megaphone-outline" tamanho={18} cor={cores.texto3} />}
            <Texto variante="legenda" tom="fraco" numberOfLines={1} className="flex-1">
              {comunicado.autor?.primeiro_nome ?? 'Rede Confiança'} · {tempoRelativo(comunicado.criado_em)}
            </Texto>
          </View>
          <BotaoCurtir id={comunicado.id} curti={comunicado.curti} curtidas={comunicado.curtidas} />
        </View>
      </View>
    </Cartao>
  );
}
