# -*- coding: utf-8 -*-
"""Envio dos SVAs não ativados (Flag 0) para o grupo de WhatsApp de cada loja — porte do whatsapp_lojas.py do D-1.

Mesma mensagem do sistema antigo (montar_mensagens, sem mudança). O que mudou no portal:
- o grupo de cada loja é o grupo de gestão dela, `cursos.GrupoWhatsappLoja` (loja = Sector, casada com o PDV
  pelo nome normalizado, como a cobrança da Renova faz);
- o envio é por `core.evolution.enviar_texto`;
- nada sai com `ConfiguracaoAuditoriaD1.whatsapp_ativo` desligado (nasce desligado; só o SUPERADMIN liga), nem o
  teste; o teste vai para o grupo digitado na configuração;
- cada mensagem é gravada em `DisparoWhatsappD1` antes de sair;
- o disparo roda numa thread daemon (1 min entre lojas) e o andamento fica no cache (consultado pela tela, de
  qualquer worker); cancelar também passa pelo cache.
"""
import datetime as dt
import logging
import threading
import time

from django.core.cache import cache
from django.db import close_old_connections
from django.utils import timezone

logger = logging.getLogger(__name__)

LIMITE_MSG = 3500   # caracteres por mensagem
PAUSA_LOJAS = 60    # segundos entre uma loja e outra
PAUSA_MSGS = 2      # segundos entre as mensagens de uma mesma loja
IGNORAR_LOJAS = {"SEDE ADMINISTRATIVA"}
MESES = ["", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto", "Setembro", "Outubro",
         "Novembro", "Dezembro"]

CHAVE_ESTADO = "auditoria_d1:zap:estado"
CHAVE_CANCELAR = "auditoria_d1:zap:cancelar"
_ESTADO_LOCAL = {"rodando": False}   # reserva se o Redis estiver fora
_CANCELAR_LOCAL = [False]
_trava = threading.Lock()


def norm(s):
    from auditoria_sap.permissions import normalizar_loja
    return " ".join(normalizar_loja(s).split())


# ---------------------------------------------------------------- estado (cache compartilhado)
def estado():
    e = cache.get(CHAVE_ESTADO)
    return dict(e) if isinstance(e, dict) else dict(_ESTADO_LOCAL)


def _gravar_estado(e):
    _ESTADO_LOCAL.clear()
    _ESTADO_LOCAL.update(e)
    cache.set(CHAVE_ESTADO, dict(e), 86400)


def pedir_cancelamento():
    cache.set(CHAVE_CANCELAR, True, 86400)
    _CANCELAR_LOCAL[0] = True


def _cancelado():
    return bool(cache.get(CHAVE_CANCELAR)) or _CANCELAR_LOCAL[0]


# ---------------------------------------------------------------- grupos e mensagens
def grupos_lojas():
    """{loja normalizada: (setor, jid)} dos grupos de gestão ativos (cursos.GrupoWhatsappLoja)."""
    from cursos.models import GrupoWhatsappLoja, jid_do_grupo
    return {norm(g.setor.name): (g.setor, jid_do_grupo(g.grupo))
            for g in GrupoWhatsappLoja.objects.filter(ativo=True).select_related("setor") if g.grupo}


def _data(v):
    if isinstance(v, (dt.date, dt.datetime)):
        return v
    return dt.date.fromisoformat(str(v)[:10])


def nome_curto(n):
    p = str(n or "-").title().split()
    return " ".join(p[:1] + p[-1:]) if len(p) > 1 else (p[0] if p else "-")


def montar_mensagens(pdv, linhas, anomes):
    """Uma mensagem por loja: vendas agrupadas por vendedor, com o nº da venda VivoGo."""
    vendas = {}
    for l in linhas:  # noqa: E741
        v = vendas.setdefault(l["VG_ID_da_venda"], {"id": l["VG_ID_da_venda"], "data": _data(l["VG_Data_venda"]),
                                                     "vendedor": l["VG_Vendedor"], "cliente": l["VG_Nome_cliente"],
                                                     "acesso": l["VG_N_acesso"], "svas": [], "qtd": 0})
        if l["DS_SRVC"] not in v["svas"]:
            v["svas"].append(l["DS_SRVC"])
        v["qtd"] += 1
    svas = lambda n: f"{n} SVA" + ("s" if n != 1 else "")  # noqa: E731
    nao_ativ = "não ativado" + ("s" if len(linhas) != 1 else "")
    cab = (f"⚠️ *{pdv} · SVA não ativado*\n"
           f"📅 {MESES[int(anomes[4:])]}/{anomes[:4]}\n\n"
           f"❌ *{svas(len(linhas))} {nao_ativ}* em {len(vendas)} venda" + ("s" if len(vendas) != 1 else "") + "\n")
    por_vendedor = {}
    for v in sorted(vendas.values(), key=lambda x: (str(x["vendedor"]), x["data"])):
        por_vendedor.setdefault(v["vendedor"], []).append(v)
    blocos = []
    for vend, vs in por_vendedor.items():
        b = f"\n👤 *{nome_curto(vend)}* · {svas(sum(v['qtd'] for v in vs))}\n"
        for v in vs:
            b += (f"\n*Nº:* {v['id']}\n"
                  f"*Data:* {v['data']:%d/%m/%Y}\n"
                  f"*Cliente:* {str(v['cliente'] or '-').title()}\n"
                  f"*Plano:* {' + '.join(v['svas'])}\n"
                  f"*Acesso:* {v['acesso'] or '-'}\n"
                  f"*Qtd:* {v['qtd']}\n")
        blocos.append(b)
    msgs, atual = [], cab
    for b in blocos:
        if len(atual) + len(b) > LIMITE_MSG:
            msgs.append(atual)
            atual = f"⚠️ *{pdv}* (continuação)\n"
        atual += b
    return msgs + [atual]


def planejar(sinalizar, loja=None, grupos=None):
    """Lojas que receberiam mensagem, e as que não têm grupo cadastrado."""
    grupos = grupos_lojas() if grupos is None else grupos
    lojas = sorted({l["VG_PDV"] for l in sinalizar if l.get("VG_PDV") and norm(l["VG_PDV"]) not in IGNORAR_LOJAS})  # noqa: E741
    if loja:
        lojas = [p for p in lojas if norm(p) == norm(loja)]
    return [p for p in lojas if norm(p) in grupos], [p for p in lojas if norm(p) not in grupos]


# ---------------------------------------------------------------- envio
def _ligado():
    from .models import ConfiguracaoAuditoriaD1
    return ConfiguracaoAuditoriaD1.get().whatsapp_ativo


def _enviar(sinalizar, anomes, teste, loja, user_id, importacao_id, destino_teste, est):
    from core import evolution

    from .models import DisparoWhatsappD1

    def log(txt):
        est["log"].append(txt)
        _gravar_estado(est)

    close_old_connections()
    try:
        grupos = grupos_lojas()
        lojas, _ = planejar(sinalizar, loja, grupos)
        for i, pdv in enumerate(lojas):
            if _cancelado():
                log("Envio cancelado.")
                break
            if i:  # pausa entre lojas para o número não ser bloqueado
                est["atual"] = f"aguardando {PAUSA_LOJAS}s antes de {pdv}"
                _gravar_estado(est)
                for _ in range(PAUSA_LOJAS):
                    if _cancelado():
                        break
                    time.sleep(1)
                if _cancelado():
                    log("Envio cancelado.")
                    break
            if not _ligado():
                log("Envio interrompido: o WhatsApp da Auditoria D-1 foi desligado na configuração.")
                break
            est["atual"] = pdv
            _gravar_estado(est)
            setor, jid = grupos[norm(pdv)]
            destino = destino_teste if teste else jid
            ok = True
            for msg in montar_mensagens(pdv, [l for l in sinalizar if l["VG_PDV"] == pdv], anomes):  # noqa: E741
                if teste:
                    msg = f"[TESTE — iria para {jid}]\n" + msg
                disparo = DisparoWhatsappD1.objects.create(
                    importacao_id=importacao_id, anomes=anomes, teste=teste, loja=pdv[:120], setor=setor,
                    destino=destino[:120], texto=msg, disparado_por_id=user_id)
                try:
                    enviado, detalhe = evolution.enviar_texto(destino, msg, timeout=30)
                except Exception as exc:  # noqa: BLE001
                    enviado, detalhe = False, f"Erro inesperado: {exc}"
                DisparoWhatsappD1.objects.filter(pk=disparo.pk).update(enviado=bool(enviado),
                                                                       detalhe=str(detalhe or "")[:255])
                ok = ok and bool(enviado)
                if not enviado:
                    log(f"{pdv}: erro {str(detalhe or '')[:150]}")
                time.sleep(PAUSA_MSGS)
            est["enviadas"] += 1
            if ok:
                log(f"{pdv}: enviado" + (" (teste)" if teste else ""))
            else:
                _gravar_estado(est)
    except Exception as e:  # noqa: BLE001 — o erro aparece na tela
        logger.exception("Auditoria D-1: falha no envio do WhatsApp")
        est["erro"] = str(e)
    finally:
        est["rodando"] = False
        est["atual"] = None
        est["fim"] = timezone.localtime().replace(tzinfo=None).isoformat(timespec="seconds")
        _gravar_estado(est)
        close_old_connections()


def iniciar(sinalizar, anomes, teste, loja, user, importacao_id, em_thread=True):
    """Começa o disparo (teste = tudo no grupo de teste da configuração). Devolve o estado inicial."""
    from cursos.models import jid_do_grupo

    from .models import ConfiguracaoAuditoriaD1
    cfg = ConfiguracaoAuditoriaD1.get()
    if not cfg.whatsapp_ativo:
        raise RuntimeError("O WhatsApp da Auditoria D-1 está desligado. O SUPERADMIN liga em Configuração.")
    destino_teste = jid_do_grupo(cfg.grupo_teste)
    if teste and not destino_teste:
        raise RuntimeError("Informe o ID do grupo de teste na Configuração da Auditoria D-1.")
    with _trava:
        if estado().get("rodando"):
            raise RuntimeError("Já existe um envio em andamento.")
        lojas, sem_grupo = planejar(sinalizar, loja)
        if not lojas:
            raise RuntimeError("Nenhuma loja com SVA a sinalizar" + (f" ({loja})" if loja else "") + ".")
        cache.delete(CHAVE_CANCELAR)
        _CANCELAR_LOCAL[0] = False
        nome = (getattr(user, "full_name", None) or user.get_full_name() or user.username)[:100]
        est = dict(rodando=True, teste=teste, total=len(lojas), enviadas=0, atual=None, log=[], erro=None,
                   sem_grupo=sem_grupo, usuario=nome, importacao_id=importacao_id,
                   inicio=timezone.localtime().replace(tzinfo=None).isoformat(timespec="seconds"), fim=None,
                   cancelar=False)
        _gravar_estado(est)
    args = (sinalizar, anomes, teste, loja, user.pk, importacao_id, destino_teste, est)
    if em_thread:
        threading.Thread(target=_enviar, args=args, daemon=True, name="auditoria-d1-whatsapp").start()
    else:
        _enviar(*args)
    return estado()
