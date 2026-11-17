import { useLocalSearchParams } from 'expo-router';
import { useState } from 'react';
import { View } from 'react-native';
import Animated, { FadeInDown } from 'react-native-reanimated';

import { useAbrirPdf } from '@/components/modulos/contracheque/abrir-pdf';
import { QuadroAssinatura } from '@/components/modulos/contracheque/quadro-assinatura';
import { RegistroAssinatura } from '@/components/modulos/contracheque/registro-assinatura';
import { Secao, TelaDetalhe } from '@/components/modulos/contracheque/tela-detalhe';
import { Botao, Cartao, Selo, Texto } from '@/components/ui';
import { estado } from '@/constants/theme';
import { useAssinarDocumento, useDocumento } from '@/hooks/modulos/documentos';
import { mensagemDoErro } from '@/services/api';
import type { DocumentoDetalhe } from '@/services/modulos/documentos';
import { dataHora } from '@/utils/formatar';

export default function DetalheDocumento() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const numero = Number(id);
  const consulta = useDocumento(numero);
  const { data } = consulta;
  const assinar = useAssinarDocumento(numero);
  const pdf = useAbrirPdf<DocumentoDetalhe>(consulta.refetch);
  const [desenhando, setDesenhando] = useState(false);

  return (
    <TelaDetalhe
      titulo={data?.titulo ?? 'Documento'}
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
                <Texto variante="rotulo" tom="fraco" className="flex-1" numberOfLines={1}>
                  {data.categoria?.nome ?? 'Documento'}
                </Texto>
                <Selo texto={data.assinado ? 'Assinado' : 'Pendente de assinatura'} cor={data.assinado ? estado.sucesso : estado.alerta} />
              </View>
              <Texto variante="titulo2" selectable>
                {data.titulo}
              </Texto>
              {data.de_outra_pessoa ? (
                <Texto variante="legenda" tom="suave">
                  Signatário: {data.de_outra_pessoa.nome}
                </Texto>
              ) : null}
              {data.descricao ? (
                <Texto tom="suave" selectable>
                  {data.descricao}
                </Texto>
              ) : null}
              {data.atribuido_em ? (
                <Texto variante="legenda" tom="fraco">
                  Recebido em {dataHora(data.atribuido_em)}
                </Texto>
              ) : null}
              {data.pdf ? (
                <Botao
                  titulo={data.pode_assinar ? 'Ler o documento' : 'Ver PDF'}
                  icone="document-text-outline"
                  variante={data.pode_assinar ? 'secundario' : 'primario'}
                  carregando={pdf.abrindo === 'pdf'}
                  onPress={() => pdf.abrir('pdf', (d) => d.pdf)}
                />
              ) : null}
              {pdf.erro ? (
                <Texto variante="legenda" tom="perigo">
                  {pdf.erro}
                </Texto>
              ) : null}
            </Cartao>
          </Animated.View>

          {data.assinatura ? (
            <Secao icone="shield-checkmark-outline" titulo="Assinatura digital registrada" cor={estado.sucesso} indice={1}>
              <RegistroAssinatura
                titulo="Documento assinado"
                {...data.assinatura}
                aoAbrirAssinado={data.pdf_assinado ? () => pdf.abrir('assinado', (d) => d.pdf_assinado) : undefined}
                abrindo={pdf.abrindo === 'assinado'}
              />
            </Secao>
          ) : data.pode_assinar ? (
            <Secao icone="create-outline" titulo="Assinatura digital" cor={estado.info} indice={1}>
              <QuadroAssinatura
                aviso="Leia o documento antes. Ao confirmar, ficam registrados data, hora e IP, e um certificado de assinatura digital é anexado ao documento."
                rotulo="Assinar documento"
                enviando={assinar.isPending}
                erro={assinar.isError ? mensagemDoErro(assinar.error) : null}
                aoAssinar={(png) => assinar.mutate(png)}
                aoDesenhar={setDesenhando}
              />
            </Secao>
          ) : null}
        </>
      ) : null}
    </TelaDetalhe>
  );
}
