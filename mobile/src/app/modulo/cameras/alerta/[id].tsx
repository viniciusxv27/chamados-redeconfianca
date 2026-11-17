import { Image } from 'expo-image';
import { useLocalSearchParams } from 'expo-router';
import { useState } from 'react';
import { ScrollView, View } from 'react-native';
import Animated, { FadeInDown } from 'react-native-reanimated';

import { COR_STATUS_ALERTA, Faixa, FotoCamera, irParaLoja, Secao, TelaDetalheCameras } from '@/components/modulos/cameras/pecas';
import { abrirArquivo, LinhaValor } from '@/components/modulos/comum';
import { Botao, Campo, Cartao, Chip, Pressionavel, Selo, Texto } from '@/components/ui';
import { estado } from '@/constants/theme';
import { useAlertaCamera, useTratarAlerta } from '@/hooks/modulos/cameras';
import { useCores } from '@/providers/tema';
import { mensagemDoErro } from '@/services/api';
import type { AlertaDetalhe, Momento } from '@/services/modulos/cameras';
import { dataHora, tamanhoArquivo } from '@/utils/formatar';

/** Um alerta: imagem, vídeo de 3 min, quadros-chave, a segunda olhada da IA e a decisão (como na web). */
export default function AlertaCamera() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const numero = Number(id);
  const consulta = useAlertaCamera(numero);
  const { data } = consulta;
  const [abrindo, setAbrindo] = useState<string | null>(null);
  const [erroAbrir, setErroAbrir] = useState<string | null>(null);

  /** Os links valem poucos minutos: pede o alerta de novo antes de abrir. */
  async function abrir(qual: string, pegar: (d: AlertaDetalhe) => string | null | undefined) {
    setErroAbrir(null);
    setAbrindo(qual);
    try {
      const { data: novo } = await consulta.refetch();
      const url = novo ? pegar(novo) : null;
      if (!url) {
        setErroAbrir('Arquivo indisponível. Tente pelo portal.');
        return;
      }
      await abrirArquivo(url);
    } catch (e) {
      setErroAbrir(mensagemDoErro(e));
    } finally {
      setAbrindo(null);
    }
  }

  return (
    <TelaDetalheCameras
      titulo={data ? `Alerta #${data.id}` : 'Alerta'}
      caminhoWeb={data?.caminho_web}
      carregando={consulta.isPending}
      erro={consulta.error}
      aoAtualizar={consulta.refetch}
      atualizando={consulta.isRefetching}>
      {data ? (
        <>
          <Animated.View entering={FadeInDown.duration(320)}>
            <Cartao style={{ padding: 14, gap: 12 }}>
              {data.foto ? (
                <Pressionavel onPress={() => void abrir('foto', (d) => d.foto)} accessibilityLabel="Abrir a foto do alerta" escala={0.99}>
                  <FotoCamera url={data.foto} rotulo={`Foto do alerta ${data.id}`} proporcao={4 / 3} />
                </Pressionavel>
              ) : null}
              <View className="flex-row flex-wrap items-center gap-2">
                <Selo texto={data.status_rotulo} cor={COR_STATUS_ALERTA[data.status] ?? '#6B7280'} />
                {data.ia_classificacao ? <Selo texto={`IA: ${data.ia_classificacao}`} cor={estado.info} ponto={false} /> : null}
              </View>
              <Texto variante="titulo2">{data.tipo_rotulo}</Texto>
              <Pressionavel onPress={() => irParaLoja(data.loja.id)} accessibilityLabel={`Abrir a loja ${data.loja.nome}`}>
                <Texto variante="corpoForte" tom="marca">
                  {data.loja.nome}
                  {data.canal ? ` · ${data.canal}` : ''}
                </Texto>
              </Pressionavel>
              {data.motivo ? (
                <Texto variante="legenda" tom="suave">
                  {data.motivo}
                </Texto>
              ) : null}
              <View>
                <LinhaValor rotulo="Quando" valor={dataHora(data.criado_em)} />
                {data.repeticoes > 1 ? <LinhaValor rotulo="Repetiu" valor={`${data.repeticoes} vezes (último ${dataHora(data.ultimo_em)})`} /> : null}
                <LinhaValor rotulo="Confiança do detector" valor={`${data.confianca}%`} />
                <LinhaValor rotulo="Prioridade" valor={String(data.prioridade)} />
                {data.historico.chance != null ? <LinhaValor rotulo="Chance de ser real (decisões anteriores)" valor={`${data.historico.chance}%`} /> : null}
              </View>
              {data.na_tolerancia ? (
                <Faixa icone="time-outline" cor={estado.info} texto="Aconteceu na margem de abrir ou fechar a loja: provavelmente a equipe." />
              ) : null}
            </Cartao>
          </Animated.View>

          <Secao icone="film-outline" titulo="Vídeo do alerta" indice={1}>
            {data.video ? (
              <>
                <Botao titulo="Ver o vídeo" icone="play-outline" carregando={abrindo === 'video'} onPress={() => void abrir('video', (d) => d.video)} />
                <Texto variante="legenda" tom="fraco">
                  {Math.round(data.video_segundos / 60)} min · o acontecimento está em {formatarSegundo(data.video_evento_segundo)}
                  {data.video_tamanho ? ` · ${tamanhoArquivo(data.video_tamanho)}` : ''}
                </Texto>
              </>
            ) : (
              <Texto variante="legenda" tom="suave">
                {data.video_rotulo}
              </Texto>
            )}
            {erroAbrir ? (
              <Texto variante="legenda" tom="perigo">
                {erroAbrir}
              </Texto>
            ) : null}
            {data.quadros.length ? (
              <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 10 }}>
                {data.quadros.map((q) => (
                  <Pressionavel key={q.indice} onPress={() => void abrir(`q${q.indice}`, (d) => d.quadros[q.indice]?.imagem)} accessibilityLabel={`Quadro em ${relativo(q.segundo)}`} style={{ width: 140, gap: 4 }}>
                    <Image source={{ uri: q.imagem }} style={{ width: 140, aspectRatio: 16 / 9, borderRadius: 10 }} contentFit="cover" cachePolicy="memory" />
                    <Texto variante="legenda" tom="fraco">
                      {relativo(q.segundo)} · no vídeo {formatarSegundo(q.no_video)}
                    </Texto>
                  </Pressionavel>
                ))}
              </ScrollView>
            ) : null}
          </Secao>

          <AnaliseIa dados={data} />

          {data.medidas.length ? (
            <Secao icone="analytics-outline" titulo="O que o detector mediu" indice={3}>
              {data.medidas.map((m) => (
                <LinhaValor key={m.rotulo} rotulo={m.rotulo} valor={m.valor} />
              ))}
              <LinhaValor rotulo="Rigor desta câmera" valor={data.rigor} />
            </Secao>
          ) : null}

          {data.equipe.length ? (
            <Secao icone="people-outline" titulo="Equipe de ponto batido no momento" indice={4}>
              {data.equipe.map((p, i) => (
                <LinhaValor key={i} rotulo={p.nome} valor={`${p.como}${p.horarios.length ? ` · ${p.horarios.join(' ')}` : ''}`} />
              ))}
            </Secao>
          ) : null}

          {data.motivos_camera.length ? (
            <Secao icone="school-outline" titulo="Falsos alarmes desta câmera" indice={5}>
              {data.motivos_camera.map((m) => (
                <LinhaValor key={m.rotulo} rotulo={m.rotulo} valor={String(m.quantos)} />
              ))}
            </Secao>
          ) : null}

          <Tratar alerta={data} />
        </>
      ) : null}
    </TelaDetalheCameras>
  );
}

function relativo(segundo: number) {
  return segundo === 0 ? 'no alerta' : `${segundo > 0 ? '+' : ''}${segundo} s`;
}

function formatarSegundo(s: number) {
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

function AnaliseIa({ dados: d }: { dados: AlertaDetalhe }) {
  const ia = d.ia;
  if (!ia.status) return null;
  return (
    <Secao icone="sparkles-outline" titulo="Segunda olhada (IA)" cor={estado.info} indice={2}
      direita={ia.risco != null ? <Selo texto={`Risco ${ia.risco}%`} cor={ia.risco >= 50 ? estado.perigo : estado.sucesso} ponto={false} /> : undefined}>
      {ia.status === 'PENDENTE' ? (
        <Texto variante="legenda" tom="suave">
          Analisando os quadros do vídeo… puxe para atualizar.
        </Texto>
      ) : ia.status === 'ERRO' ? (
        <Texto variante="legenda" tom="perigo">
          {ia.erro || 'Não deu para analisar.'}
        </Texto>
      ) : (
        <>
          {ia.resumo ? <Texto>{ia.resumo}</Texto> : null}
          <ListaMomentos titulo="Linha do tempo" momentos={ia.linha_do_tempo} />
          <ListaTexto titulo="Pesa contra" itens={ia.sinais_de_risco} cor={estado.perigo} />
          <ListaTexto titulo="Parece normal" itens={ia.sinais_normais} cor={estado.sucesso} />
          <ListaMomentos titulo="Confira no vídeo" momentos={ia.conferir_no_video} />
          {ia.recomendacao ? (
            <Texto variante="legenda" tom="suave">
              {ia.recomendacao}
            </Texto>
          ) : null}
          {ia.modelo ? (
            <Texto variante="legenda" tom="fraco">
              {ia.modelo}
              {ia.em ? ` · ${dataHora(ia.em)}` : ''}
            </Texto>
          ) : null}
        </>
      )}
    </Secao>
  );
}

function ListaMomentos({ titulo, momentos }: { titulo: string; momentos: Momento[] }) {
  if (!momentos.length) return null;
  return (
    <View className="gap-1">
      <Texto variante="rotulo" tom="fraco">
        {titulo}
      </Texto>
      {momentos.map((m, i) => (
        <Texto key={i} variante="legenda" tom="suave">
          <Texto variante="legenda" tom="padrao">
            {relativo(m.segundo)}
          </Texto>{' '}
          {m.o_que}
        </Texto>
      ))}
    </View>
  );
}

function ListaTexto({ titulo, itens, cor }: { titulo: string; itens: string[]; cor: string }) {
  if (!itens.length) return null;
  return (
    <View className="gap-1">
      <Texto variante="rotulo" style={{ color: cor }}>
        {titulo}
      </Texto>
      {itens.map((x, i) => (
        <Texto key={i} variante="legenda" tom="suave">
          • {x}
        </Texto>
      ))}
    </View>
  );
}

/** A decisão da web (cameras/alertas/<id>/): situação, motivo do falso alarme (obrigatório) e observação. */
function Tratar({ alerta: a }: { alerta: AlertaDetalhe }) {
  const cores = useCores();
  const tratar = useTratarAlerta(a.id);
  const [status, setStatus] = useState<string>(a.status);
  const [motivo, setMotivo] = useState<string>(a.motivo_falso ?? '');
  const [observacao, setObservacao] = useState<string>(a.observacao ?? '');
  const falta = status === 'FALSO' && !motivo;

  return (
    <Secao icone="checkmark-done-outline" titulo="Decisão" cor={estado.sucesso} indice={6}>
      {a.tratado_por ? (
        <Texto variante="legenda" tom="fraco">
          Por {a.tratado_por.nome}
          {a.tratado_em ? ` em ${dataHora(a.tratado_em)}` : ''}
          {a.motivo_falso_rotulo ? ` · ${a.motivo_falso_rotulo}` : ''}
        </Texto>
      ) : null}
      <View className="flex-row flex-wrap gap-2">
        {a.opcoes.status.map((o) => (
          <Chip key={o.valor} rotulo={o.rotulo} ativo={status === o.valor} onPress={() => setStatus(o.valor)} />
        ))}
      </View>
      {status === 'FALSO' ? (
        <View className="gap-2">
          <Texto variante="legenda" style={{ color: cores.texto2 }}>
            Por que foi falso alarme? É com isso que a análise aprende.
          </Texto>
          <View className="flex-row flex-wrap gap-2">
            {a.opcoes.motivos_falso.map((o) => (
              <Chip key={o.valor} rotulo={o.rotulo} ativo={motivo === o.valor} onPress={() => setMotivo(o.valor)} />
            ))}
          </View>
        </View>
      ) : null}
      <Campo rotulo="Observação" placeholder="O que você viu (opcional)" value={observacao} onChangeText={setObservacao} multiline />
      <Botao
        titulo="Salvar decisão"
        icone="save-outline"
        carregando={tratar.isPending}
        desativado={falta}
        onPress={() => tratar.mutate({ status, motivo_falso: status === 'FALSO' ? motivo : '', observacao })}
      />
      {tratar.isError ? (
        <Texto variante="legenda" tom="perigo">
          {mensagemDoErro(tratar.error)}
        </Texto>
      ) : tratar.data?.mensagem ? (
        <Texto variante="legenda" tom="sucesso">
          {tratar.data.mensagem}
        </Texto>
      ) : null}
    </Secao>
  );
}
