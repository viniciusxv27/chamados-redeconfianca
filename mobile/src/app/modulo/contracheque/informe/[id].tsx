import { useLocalSearchParams } from 'expo-router';
import { View } from 'react-native';
import Animated, { FadeInDown } from 'react-native-reanimated';

import { Indicador, LinhaValor } from '@/components/modulos/comum';
import { useAbrirPdf } from '@/components/modulos/contracheque/abrir-pdf';
import { Secao, TelaDetalhe } from '@/components/modulos/contracheque/tela-detalhe';
import { Botao, Cartao, type NomeIcone, Texto } from '@/components/ui';
import { estado } from '@/constants/theme';
import { useInforme } from '@/hooks/modulos/contracheque';
import type { InformeDetalhe } from '@/services/modulos/contracheque';
import { reais } from '@/utils/dinheiro';

const ICONES: NomeIcone[] = ['cash-outline', 'shield-checkmark-outline', 'ribbon-outline'];

export default function DetalheInforme() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const consulta = useInforme(Number(id));
  const { data } = consulta;
  const pdf = useAbrirPdf<InformeDetalhe>(consulta.refetch);

  return (
    <TelaDetalhe
      titulo={data ? `Informe ${data.ano_base}` : 'Informe de rendimentos'}
      caminhoWeb={data?.caminho_web}
      carregando={consulta.isPending}
      erro={consulta.error}
      aoAtualizar={consulta.refetch}
      atualizando={consulta.isRefetching}>
      {data ? (
        <>
          <Animated.View entering={FadeInDown.duration(320)}>
            <Cartao style={{ padding: 18, gap: 12 }}>
              <View>
                <Texto variante="titulo2">Informe de rendimentos {data.ano_base}</Texto>
                <Texto variante="legenda" tom="suave">
                  Exercício {data.exercicio}
                  {data.de_outra_pessoa ? ` · ${data.de_outra_pessoa.nome}` : ''}
                </Texto>
              </View>
              {data.beneficiario.nome || data.beneficiario.cpf ? (
                <View>
                  {data.beneficiario.nome ? <LinhaValor rotulo="Nome" valor={data.beneficiario.nome} /> : null}
                  {data.beneficiario.cpf ? <LinhaValor rotulo="CPF" valor={data.beneficiario.cpf} /> : null}
                </View>
              ) : null}
              {data.pdf ? <Botao titulo="Ver PDF" icone="document-text-outline" carregando={pdf.abrindo === 'pdf'} onPress={() => pdf.abrir('pdf', (d) => d.pdf)} /> : null}
              {pdf.erro ? (
                <Texto variante="legenda" tom="perigo">
                  {pdf.erro}
                </Texto>
              ) : null}
            </Cartao>
          </Animated.View>

          <View className="flex-row flex-wrap gap-3">
            <Indicador rotulo="Rendimentos tributáveis" valor={reais(data.total_rendimentos)} icone="trending-up-outline" cor={estado.alerta} />
            <Indicador rotulo="Isentos" valor={reais(data.total_isentos)} icone="shield-checkmark-outline" cor={estado.sucesso} />
            <Indicador rotulo="IRRF retido" valor={reais(data.irrf)} icone="receipt-outline" cor={estado.perigo} />
          </View>

          {data.secoes.map((secao, i) => (
            <Secao key={secao.titulo} icone={ICONES[i] ?? 'list-outline'} titulo={secao.titulo} indice={i + 1}>
              {secao.linhas.map((l, j) => (
                <LinhaValor key={`${i}-${j}`} rotulo={`${j + 1}. ${l.rotulo}`} valor={reais(l.valor)} />
              ))}
            </Secao>
          ))}
        </>
      ) : null}
    </TelaDetalhe>
  );
}
