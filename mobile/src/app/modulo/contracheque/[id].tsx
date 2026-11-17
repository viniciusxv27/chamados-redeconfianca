import { useLocalSearchParams } from 'expo-router';
import { useState } from 'react';
import { View } from 'react-native';
import Animated, { FadeInDown } from 'react-native-reanimated';

import { Indicador, LinhaValor } from '@/components/modulos/comum';
import { useAbrirPdf } from '@/components/modulos/contracheque/abrir-pdf';
import { QuadroAssinatura } from '@/components/modulos/contracheque/quadro-assinatura';
import { RegistroAssinatura } from '@/components/modulos/contracheque/registro-assinatura';
import { Secao, TelaDetalhe } from '@/components/modulos/contracheque/tela-detalhe';
import { Botao, Cartao, Selo, Texto } from '@/components/ui';
import { estado } from '@/constants/theme';
import { useAssinarContracheque, useContracheque } from '@/hooks/modulos/contracheque';
import { mensagemDoErro } from '@/services/api';
import type { HoleriteDetalhe } from '@/services/modulos/contracheque';
import { reais } from '@/utils/dinheiro';

export default function DetalheContracheque() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const numero = Number(id);
  const consulta = useContracheque(numero);
  const { data } = consulta;
  const assinar = useAssinarContracheque(numero);
  const pdf = useAbrirPdf<HoleriteDetalhe>(consulta.refetch);
  const [desenhando, setDesenhando] = useState(false);

  return (
    <TelaDetalhe
      titulo={data ? `Contracheque ${data.competencia}` : 'Contracheque'}
      caminhoWeb={data?.caminho_web}
      carregando={consulta.isPending}
      erro={consulta.error}
      aoAtualizar={consulta.refetch}
      atualizando={consulta.isRefetching}
      rolagemTravada={desenhando}>
      {data ? (
        <>
          <Animated.View entering={FadeInDown.duration(320)}>
            <Cartao style={{ padding: 18, gap: 12 }}>
              <View className="flex-row items-center justify-between gap-3">
                <View className="flex-1">
                  <Texto variante="titulo2">{data.competencia}</Texto>
                  {data.de_outra_pessoa ? (
                    <Texto variante="legenda" tom="suave">
                      {data.de_outra_pessoa.nome}
                    </Texto>
                  ) : null}
                </View>
                <Selo texto={data.assinado ? 'Assinado' : 'Não assinado'} cor={data.assinado ? estado.sucesso : estado.perigo} />
              </View>
              <View>
                <Texto variante="legenda" tom="fraco">
                  Líquido a receber
                </Texto>
                <Texto variante="display" style={{ color: estado.sucesso }} numberOfLines={1} adjustsFontSizeToFit>
                  {reais(data.liquido)}
                </Texto>
              </View>
              {data.pdf ? (
                <Botao titulo="Ver PDF" icone="document-text-outline" carregando={pdf.abrindo === 'pdf'} onPress={() => pdf.abrir('pdf', (d) => d.pdf)} />
              ) : (
                <Texto variante="legenda" tom="fraco">
                  PDF não disponível para este contracheque.
                </Texto>
              )}
              {pdf.erro ? (
                <Texto variante="legenda" tom="perigo">
                  {pdf.erro}
                </Texto>
              ) : null}
            </Cartao>
          </Animated.View>

          <View className="flex-row flex-wrap gap-3">
            <Indicador rotulo="Proventos" valor={reais(data.proventos)} icone="add-circle-outline" cor={estado.sucesso} />
            <Indicador rotulo="Descontos" valor={reais(data.descontos)} icone="remove-circle-outline" cor={estado.perigo} />
            <Indicador rotulo="Salário base" valor={reais(data.salario_base)} icone="briefcase-outline" cor={estado.info} />
            <Indicador rotulo="Líquido" valor={reais(data.liquido)} icone="wallet-outline" cor="#6366F1" />
          </View>

          <DadosFuncionario dados={data.funcionario} />

          {data.itens_proventos.length ? (
            <Secao icone="add-circle-outline" titulo="Proventos" cor={estado.sucesso} indice={1}>
              {data.itens_proventos.map((item, i) => (
                <LinhaValor key={`p${i}`} rotulo={item.descricao} valor={valorItem(item.valor)} cor={estado.sucesso} />
              ))}
            </Secao>
          ) : null}

          {data.itens_descontos.length ? (
            <Secao icone="remove-circle-outline" titulo="Descontos" cor={estado.perigo} indice={2}>
              {data.itens_descontos.map((item, i) => (
                <LinhaValor key={`d${i}`} rotulo={item.descricao} valor={valorItem(item.valor)} cor={estado.perigo} />
              ))}
            </Secao>
          ) : null}

          {data.bases.length ? (
            <Secao icone="calculator-outline" titulo="Bases de cálculo" indice={3}>
              {data.bases.map((b) => (
                <LinhaValor key={b.rotulo} rotulo={b.rotulo} valor={reais(b.valor)} />
              ))}
            </Secao>
          ) : null}

          {data.assinatura ? (
            <Secao icone="shield-checkmark-outline" titulo="Assinatura digital" cor={estado.sucesso} indice={4}>
              <RegistroAssinatura
                titulo="Contracheque assinado digitalmente"
                {...data.assinatura}
                aoAbrirAssinado={data.pdf_assinado ? () => pdf.abrir('assinado', (d) => d.pdf_assinado) : undefined}
                abrindo={pdf.abrindo === 'assinado'}
              />
            </Secao>
          ) : data.pode_assinar ? (
            <Secao icone="create-outline" titulo="Assinatura digital" cor={estado.info} indice={4}>
              <QuadroAssinatura
                aviso="Ao assinar, você confirma o recebimento e a ciência dos valores deste contracheque."
                detalhe="A assinatura fica registrada com data, hora, endereço IP e um código de verificação."
                rotulo="Assinar contracheque"
                enviando={assinar.isPending}
                erro={assinar.isError ? mensagemDoErro(assinar.error) : null}
                aoAssinar={(png) => assinar.mutate(png)}
                aoDesenhar={setDesenhando}
              />
            </Secao>
          ) : data.assinado && data.pdf_assinado ? (
            <Botao titulo="Abrir PDF assinado" icone="document-attach-outline" variante="secundario" carregando={pdf.abrindo === 'assinado'} onPress={() => pdf.abrir('assinado', (d) => d.pdf_assinado)} />
          ) : null}
        </>
      ) : null}
    </TelaDetalhe>
  );
}

/** Os itens vêm como texto decimal ("2800.00"); se não for número, mostra como veio (igual à web). */
function valorItem(valor: string) {
  return valor && !Number.isNaN(Number(valor)) ? reais(valor) : valor || '—';
}

function DadosFuncionario({ dados }: { dados: HoleriteDetalhe['funcionario'] }) {
  const linhas = [
    ['Nome', dados.nome],
    ['CPF', dados.cpf],
    ['Cargo', dados.cargo],
    ['Admissão', dados.admissao],
    ['Departamento', dados.departamento],
  ].filter((l): l is [string, string] => !!l[1]);
  if (!linhas.length) return null;
  return (
    <Secao icone="person-outline" titulo="Dados do funcionário">
      {linhas.map(([rotulo, valor]) => (
        <LinhaValor key={rotulo} rotulo={rotulo} valor={valor} />
      ))}
    </Secao>
  );
}

