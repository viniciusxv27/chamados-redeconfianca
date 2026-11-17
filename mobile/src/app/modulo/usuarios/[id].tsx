/**
 * Ficha do colaborador: identificação, lotação, grupos e situação, com atalhos
 * de contato do próprio aparelho (ligar, WhatsApp, e-mail) — o servidor não
 * envia nada. Editar e o resto do cadastro abrem no portal.
 */
import { useLocalSearchParams } from 'expo-router';
import { Linking, ScrollView, View } from 'react-native';
import Animated, { FadeInDown } from 'react-native-reanimated';

import { EsqueletoBloco } from '@/components/modulos/comum';
import { COR_HIERARQUIA, dataCurta, SeloSituacao } from '@/components/modulos/usuarios/pecas';
import { Avatar, BarraDetalhe, Botao, BotaoIcone, Cartao, EstadoErro, Icone, type NomeIcone, Pressionavel, Selo, Tela, Texto } from '@/components/ui';
import { fontes, marca } from '@/constants/theme';
import { useUsuario } from '@/hooks/modulos/usuarios';
import { useCores } from '@/providers/tema';
import { mensagemDoErro } from '@/services/api';
import type { UsuarioDetalhe } from '@/services/modulos/usuarios';
import { comAlfa } from '@/utils/formatar';
import { abrirNoPortal } from '@/utils/links';

const WHATSAPP = '#25D366';

export default function DetalheUsuario() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { data, isPending, isError, error, refetch } = useUsuario(Number(id));

  return (
    <Tela>
      <BarraDetalhe
        titulo={data?.primeiro_nome}
        direita={data ? <BotaoIcone icone="open-outline" rotulo="Abrir no portal" onPress={() => abrirNoPortal(data.url_web)} /> : undefined}
      />
      {isPending ? (
        <View className="gap-4 p-5">
          <EsqueletoBloco linhas={3} />
          <EsqueletoBloco linhas={5} />
        </View>
      ) : isError || !data ? (
        <EstadoErro mensagem={mensagemDoErro(error)} aoTentar={refetch} />
      ) : (
        <ScrollView contentContainerStyle={{ padding: 20, paddingTop: 8, gap: 16, paddingBottom: 40 }}>
          <Cabecalho u={data} />
          <Contatos u={data} />
          <Dados u={data} />
          {data.grupos.length ? (
            <Animated.View entering={FadeInDown.delay(160).duration(350)}>
              <Cartao style={{ padding: 18, gap: 12 }}>
                <Texto variante="rotulo" tom="suave">
                  Grupos de comunicação
                </Texto>
                <View className="flex-row flex-wrap gap-2">
                  {data.grupos.map((g) => (
                    <Selo key={g} texto={g} cor={marca[600]} ponto={false} />
                  ))}
                </View>
              </Cartao>
            </Animated.View>
          ) : null}
          <Botao titulo="Editar no portal" variante="secundario" icone="create-outline" onPress={() => abrirNoPortal(data.url_web)} />
        </ScrollView>
      )}
    </Tela>
  );
}

function Cabecalho({ u }: { u: UsuarioDetalhe }) {
  return (
    <Animated.View entering={FadeInDown.duration(350)}>
      <Cartao style={{ padding: 20, alignItems: 'center', gap: 10 }}>
        <View style={{ opacity: u.acesso_ativo ? 1 : 0.6 }}>
          <Avatar nome={u.nome} foto={u.foto} tamanho={88} />
        </View>
        <Texto variante="titulo2" className="text-center" selectable>
          {u.nome}
        </Texto>
        {u.cargo ? (
          <Texto tom="suave" className="text-center">
            {u.cargo}
          </Texto>
        ) : null}
        <View className="flex-row flex-wrap justify-center gap-2">
          <SeloSituacao usuario={u} />
          <Selo texto={u.hierarquia_nome} cor={COR_HIERARQUIA[u.hierarquia] ?? '#6B7280'} ponto={false} />
        </View>
      </Cartao>
    </Animated.View>
  );
}

/** Atalhos do aparelho: abrem o discador, o WhatsApp e o app de e-mail. */
function Contatos({ u }: { u: UsuarioDetalhe }) {
  const cores = useCores();
  const digitos = (u.telefone ?? '').replace(/\D/g, '');
  const acoes: { icone: NomeIcone; rotulo: string; cor: string; url: string | null }[] = [
    { icone: 'call', rotulo: 'Ligar', cor: marca[500], url: digitos ? `tel:${digitos}` : null },
    { icone: 'logo-whatsapp', rotulo: 'WhatsApp', cor: WHATSAPP, url: u.whatsapp ? `https://wa.me/${u.whatsapp}` : null },
    { icone: 'mail', rotulo: 'E-mail', cor: '#3B82F6', url: u.email ? `mailto:${u.email}` : null },
  ];
  return (
    <Animated.View entering={FadeInDown.delay(80).duration(350)} className="flex-row gap-3">
      {acoes.map((a) => (
        <Pressionavel
          key={a.rotulo}
          onPress={() => a.url && void Linking.openURL(a.url)}
          disabled={!a.url}
          vibrar
          escala={0.95}
          accessibilityLabel={a.url ? `${a.rotulo} para ${u.primeiro_nome}` : `${a.rotulo}: sem dado no cadastro`}
          style={{
            flex: 1,
            alignItems: 'center',
            gap: 6,
            paddingVertical: 14,
            borderRadius: 16,
            backgroundColor: cores.superficie,
            borderWidth: 1,
            borderColor: cores.bordaSuave,
            opacity: a.url ? 1 : 0.45,
          }}>
          <View style={{ width: 40, height: 40, borderRadius: 20, backgroundColor: comAlfa(a.cor, 0.14), alignItems: 'center', justifyContent: 'center' }}>
            <Icone nome={a.icone} tamanho={20} cor={a.cor} />
          </View>
          <Texto variante="legenda" style={{ fontFamily: fontes.seminegrito }}>
            {a.rotulo}
          </Texto>
        </Pressionavel>
      ))}
    </Animated.View>
  );
}

function Dados({ u }: { u: UsuarioDetalhe }) {
  const setores = u.setores.filter((s) => s !== u.setor_principal);
  return (
    <Animated.View entering={FadeInDown.delay(120).duration(350)}>
      <Cartao style={{ padding: 18, gap: 14 }}>
        <Linha icone="at" rotulo="Login" valor={u.login} />
        <Linha icone="mail-outline" rotulo="E-mail" valor={u.email} />
        <Linha icone="call-outline" rotulo="Telefone" valor={u.telefone ?? 'Não informado'} fraco={!u.telefone} />
        <Linha icone="business-outline" rotulo="Setor principal" valor={u.setor_principal ?? 'Sem setor'} fraco={!u.setor_principal} />
        {setores.length ? <Linha icone="git-branch-outline" rotulo="Outros setores" valor={setores.join(', ')} /> : null}
        <Linha icone="shield-outline" rotulo="Hierarquia" valor={u.hierarquia_nome} />
        {u.admissao ? <Linha icone="calendar-outline" rotulo="Admissão" valor={dataCurta(u.admissao) ?? '—'} /> : null}
        {u.status === 'FERIAS' ? (
          <Linha icone="sunny-outline" rotulo="Férias" valor={`${dataCurta(u.ferias_inicio) ?? '?'} a ${dataCurta(u.ferias_fim) ?? '?'}`} />
        ) : null}
        {u.status === 'AFASTADO' && u.afastamento_retorno ? <Linha icone="time-outline" rotulo="Retorno" valor={dataCurta(u.afastamento_retorno) ?? '—'} /> : null}
        {!u.acesso_ativo ? <Linha icone="lock-closed-outline" rotulo="Portal" valor="Sem acesso (login desativado)" /> : null}
        {u.saldo_cs !== undefined ? <Linha icone="wallet-outline" rotulo="Saldo C$" valor={`C$ ${u.saldo_cs}`} /> : null}
      </Cartao>
    </Animated.View>
  );
}

function Linha({ icone, rotulo, valor, fraco }: { icone: NomeIcone; rotulo: string; valor: string; fraco?: boolean }) {
  const cores = useCores();
  return (
    <View className="flex-row items-start gap-3">
      <Icone nome={icone} tamanho={18} cor={cores.texto3} style={{ marginTop: 1 }} />
      <Texto variante="legenda" tom="fraco" style={{ width: 110 }}>
        {rotulo}
      </Texto>
      <Texto variante="legenda" tom={fraco ? 'fraco' : 'padrao'} className="flex-1" style={{ fontFamily: fontes.seminegrito }} selectable>
        {valor}
      </Texto>
    </View>
  );
}
