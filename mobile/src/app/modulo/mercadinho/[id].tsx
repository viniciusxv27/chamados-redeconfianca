import { useLocalSearchParams } from 'expo-router';
import { Alert, ScrollView, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { cs, dataCurta, FotoPremio, irParaResgates, VERDE_MERCADINHO } from '@/components/modulos/mercadinho/pecas';
import { BarraDetalhe, Botao, Cartao, EsqueletoDetalhe, EstadoErro, Icone, type NomeIcone, Selo, Tela, Texto } from '@/components/ui';
import { estado, fontes } from '@/constants/theme';
import { useCores } from '@/providers/tema';
import { usePremio, useResgatar } from '@/hooks/modulos/mercadinho';
import { mensagemDoErro } from '@/services/api';
import type { Premio } from '@/services/modulos/mercadinho';

/** Por que o botão não resgata (as mesmas condições do botão da web, marketplace.html:236). */
function impedimento(p: Premio) {
  if (!p.disponivel) return p.restantes === 0 ? 'Esgotado' : 'Indisponível no momento';
  if (!p.saldo_suficiente) return `Faltam ${cs(p.valor_final - p.saldo)} para resgatar`;
  return null;
}

function Info({ icone, texto, cor }: { icone: NomeIcone; texto: string; cor?: string }) {
  const cores = useCores();
  return (
    <View className="flex-row items-center gap-2">
      <Icone nome={icone} tamanho={17} cor={cor ?? cores.texto3} />
      <Texto variante="legenda" tom="suave" className="flex-1">
        {texto}
      </Texto>
    </View>
  );
}

export default function DetalhePremio() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const premioId = Number(id) || 0;
  const { data: p, isPending, error, refetch } = usePremio(premioId);
  const resgatar = useResgatar(premioId);
  const insets = useSafeAreaInsets();
  const cores = useCores();

  const confirmar = (premio: Premio) => {
    // A web pede confirmação antes de resgatar (o modal de marketplace.html:313).
    Alert.alert(
      'Confirmar resgate',
      `Resgatar "${premio.nome}" por ${cs(premio.valor_final)}? O valor sai do seu saldo agora e o resgate fica aguardando aprovação.`,
      [
        { text: 'Voltar', style: 'cancel' },
        {
          text: 'Resgatar',
          onPress: () =>
            resgatar.mutate(undefined, {
              onSuccess: (r) =>
                Alert.alert('Prêmio resgatado!', `${r.mensagem}\nSaldo agora: ${cs(r.saldo)}.`, [
                  { text: 'OK', style: 'cancel' },
                  { text: 'Ver meus resgates', onPress: irParaResgates },
                ]),
              onError: (erro) => Alert.alert('Não deu para resgatar', mensagemDoErro(erro)),
            }),
        },
      ],
    );
  };

  if (isPending || !p) {
    return (
      <Tela>
        <BarraDetalhe titulo="Prêmio" />
        <View style={{ padding: 20 }}>{error ? <EstadoErro mensagem={mensagemDoErro(error)} aoTentar={() => void refetch()} /> : <EsqueletoDetalhe />}</View>
      </Tela>
    );
  }

  const bloqueio = impedimento(p);
  const estoque = p.restantes == null ? 'Estoque à vontade' : p.restantes === 0 ? 'Sem estoque' : `${p.restantes} ${p.restantes === 1 ? 'unidade restante' : 'unidades restantes'}`;

  return (
    <Tela>
      <BarraDetalhe titulo={p.nome} />
      <ScrollView contentContainerStyle={{ paddingHorizontal: 20, paddingTop: 8, paddingBottom: 140, gap: 18 }}>
        <View style={{ borderRadius: 20, overflow: 'hidden' }}>
          <FotoPremio uri={p.imagem} />
        </View>

        <View style={{ gap: 8 }}>
          <View className="flex-row flex-wrap gap-2">
            {p.categoria ? <Selo texto={p.categoria.nome} cor={VERDE_MERCADINHO} ponto={false} /> : null}
            {p.desconto_percentual != null ? <Selo texto={`${Math.round(p.desconto_percentual)}% OFF`} cor={estado.alerta} ponto={false} /> : null}
            {p.destaque ? <Selo texto="Destaque" cor={estado.info} ponto={false} /> : null}
          </View>
          <Texto variante="titulo2">{p.nome}</Texto>
          <View className="flex-row flex-wrap items-baseline gap-x-3">
            <Texto variante="titulo1" style={{ color: VERDE_MERCADINHO, fontFamily: fontes.extranegrito }}>
              {cs(p.valor_final)}
            </Texto>
            {p.desconto_percentual != null ? (
              <Texto variante="corpo" tom="fraco" style={{ textDecorationLine: 'line-through' }}>
                {cs(p.valor)}
              </Texto>
            ) : null}
          </View>
        </View>

        <Cartao style={{ padding: 18, gap: 12 }}>
          <Info icone="wallet-outline" texto={`Seu saldo: ${cs(p.saldo)}`} cor={p.saldo_suficiente ? VERDE_MERCADINHO : estado.perigo} />
          <Info icone="cube-outline" texto={estoque} />
          {p.valido_ate ? <Info icone="time-outline" texto={`Válido até ${dataCurta(p.valido_ate)}`} /> : null}
          <Info icone="location-outline" texto="Retirada na SEDE depois da aprovação" />
        </Cartao>

        {p.descricao ? (
          <Cartao style={{ padding: 18, gap: 8 }}>
            <Texto variante="rotulo" tom="fraco">
              Descrição
            </Texto>
            <Texto>{p.descricao}</Texto>
          </Cartao>
        ) : null}

        {p.termos ? (
          <Cartao style={{ padding: 18, gap: 8 }}>
            <Texto variante="rotulo" tom="fraco">
              Termos e condições
            </Texto>
            <Texto variante="legenda" tom="suave">
              {p.termos}
            </Texto>
          </Cartao>
        ) : null}
      </ScrollView>

      <View
        style={{
          position: 'absolute',
          left: 0,
          right: 0,
          bottom: 0,
          paddingHorizontal: 20,
          paddingTop: 14,
          paddingBottom: insets.bottom + 14,
          gap: 8,
          backgroundColor: cores.superficie,
          borderTopWidth: 1,
          borderTopColor: cores.borda,
        }}>
        {bloqueio ? (
          <Texto variante="legenda" tom="suave" className="text-center">
            {bloqueio}
          </Texto>
        ) : null}
        <Botao
          titulo={bloqueio ? 'Resgatar' : `Resgatar por ${cs(p.valor_final)}`}
          icone="bag-check-outline"
          tamanho="lg"
          desativado={!!bloqueio}
          carregando={resgatar.isPending}
          onPress={() => confirmar(p)}
          style={bloqueio ? { opacity: 0.5 } : { backgroundColor: VERDE_MERCADINHO, boxShadow: '0px 10px 24px rgba(39,174,96,0.32)' }}
        />
      </View>
    </Tela>
  );
}
