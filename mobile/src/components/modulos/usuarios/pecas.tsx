/** Peças do módulo Usuários: cartão da lista, selos de situação/hierarquia e datas. */
import { View } from 'react-native';

import { Avatar, Cartao, Icone, Selo, Texto } from '@/components/ui';
import { estado } from '@/constants/theme';
import { useCores } from '@/providers/tema';
import type { Hierarquia, SituacaoUsuario, UsuarioResumo } from '@/services/modulos/usuarios';

export const COR_SITUACAO: Record<SituacaoUsuario, string> = {
  ATIVO: estado.sucesso,
  FERIAS: estado.info,
  AFASTADO: estado.alerta,
  INATIVO: estado.perigo,
};

export const COR_HIERARQUIA: Record<Hierarquia, string> = {
  SUPERADMIN: '#8E44AD',
  ADMIN: '#B7791F',
  SUPERVISOR: '#B7791F',
  ADMINISTRATIVO: estado.perigo,
  PADRAO: '#6B7280',
};

/** 'AAAA-MM-DD' → 'DD/MM/AAAA' sem passar por Date (não escorrega de dia pelo fuso). */
export function dataCurta(iso: string | null | undefined) {
  if (!iso) return null;
  const [a, m, d] = iso.slice(0, 10).split('-');
  return a && m && d ? `${d}/${m}/${a}` : null;
}

export function SeloSituacao({ usuario }: { usuario: Pick<UsuarioResumo, 'status' | 'status_nome' | 'acesso_ativo'> }) {
  return (
    <View className="flex-row flex-wrap gap-1.5">
      <Selo texto={usuario.status_nome} cor={COR_SITUACAO[usuario.status] ?? '#6B7280'} />
      {!usuario.acesso_ativo && usuario.status !== 'INATIVO' ? <Selo texto="Sem acesso ao portal" cor={estado.perigo} ponto={false} /> : null}
    </View>
  );
}

export function CartaoUsuario({ usuario, onPress }: { usuario: UsuarioResumo; onPress: () => void }) {
  const cores = useCores();
  const linha2 = [usuario.cargo, usuario.setor].filter(Boolean).join(' · ') || 'Sem setor';
  return (
    <Cartao onPress={onPress} acessibilidade={`Abrir ${usuario.nome}`} style={{ padding: 14, flexDirection: 'row', alignItems: 'center', gap: 12 }}>
      <View style={{ opacity: usuario.acesso_ativo ? 1 : 0.55 }}>
        <Avatar nome={usuario.nome} foto={usuario.foto} tamanho={46} />
      </View>
      <View className="flex-1 gap-1">
        <Texto variante="corpoForte" numberOfLines={1}>
          {usuario.nome}
        </Texto>
        <Texto variante="legenda" tom="suave" numberOfLines={1}>
          {linha2}
        </Texto>
        <View className="mt-0.5 flex-row flex-wrap items-center gap-1.5">
          <Selo texto={usuario.status_nome} cor={COR_SITUACAO[usuario.status] ?? '#6B7280'} />
          {usuario.hierarquia !== 'PADRAO' ? <Selo texto={usuario.hierarquia_nome} cor={COR_HIERARQUIA[usuario.hierarquia] ?? '#6B7280'} ponto={false} /> : null}
          {!usuario.acesso_ativo && usuario.status !== 'INATIVO' ? <Selo texto="Sem acesso" cor={estado.perigo} ponto={false} /> : null}
        </View>
      </View>
      <Icone nome="chevron-forward" tamanho={18} cor={cores.texto3} />
    </Cartao>
  );
}
