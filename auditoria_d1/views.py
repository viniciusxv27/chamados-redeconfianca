# -*- coding: utf-8 -*-
"""Telas e APIs da Auditoria D-1 (/auditoria-d1/). As APIs são as mesmas do app.py do sistema Flask
(/api/...), agora sob /auditoria-d1/api/, com o login do portal e o papel de cada pessoa (permissoes.py).

O autor de cada gravação (verificado_por, usuario do log, contestacao_por, usuario da importação) é o nome do
usuário do portal — no sistema antigo era o nome digitado na tela.
"""
import datetime as dt
import json
import logging
import uuid
from decimal import Decimal
from functools import wraps
from urllib.parse import quote

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from . import categorias, cruzamento_fixa, cruzamento_sva, importacao_vivogo
from . import mysql as db
from . import regras, whatsapp_lojas
from .models import AcessoAuditoriaD1, ConfiguracaoAuditoriaD1, DisparoWhatsappD1
from .permissoes import AUDITOR, CONSULTA, GESTOR, SUPERADMIN, capacidades, exige

logger = logging.getLogger(__name__)
User = get_user_model()

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ERRO_BANCO = "Sem conexão com o banco de dados no momento. Tente novamente em instantes."


def nome_usuario(user):
    return ((getattr(user, "full_name", None) or user.get_full_name() or user.username or "—").strip())[:100]


def _json(dados, status=200):
    return JsonResponse(dados, status=status, safe=False, json_dumps_params={"ensure_ascii": False})


def _corpo(request):
    try:
        return json.loads(request.body or b"{}") or {}
    except ValueError:
        return {}


def api(minimo, metodos=("GET",)):
    """Permissão + método + erros como no Flask: banco fora do ar vira 503 com mensagem, nunca a página 500."""
    def decorador(view):
        @exige(minimo, api=True)
        @require_http_methods(list(metodos))
        @wraps(view)
        def _view(request, *args, **kwargs):
            try:
                return view(request, *args, **kwargs)
            except db.D1Indisponivel:
                return _json({"erro": ERRO_BANCO}, 503)
            except Exception as e:  # noqa: BLE001
                if db._erro_de_conexao(e):
                    logger.warning("Auditoria D-1: MySQL caiu: %s", e)
                    return _json({"erro": ERRO_BANCO}, 503)
                logger.exception("Auditoria D-1: erro em %s", request.path)
                return _json({"erro": f"Erro interno: {e}"}, 500)
        return _view
    return decorador


# ---------------------------------------------------------------- páginas
@exige(CONSULTA)
def esteira(request):
    return render(request, "auditoria_d1/esteira.html", {
        "pode": capacidades(request.user), "usuario_nome": nome_usuario(request.user), "aba": "esteira",
        "mysql_configurado": db.configurado(),
    })


@exige(SUPERADMIN)
def acessos(request):
    """SUPERADMIN libera pessoas como Consulta, Auditor ou Gestor."""
    if request.method == "POST":
        acao = request.POST.get("acao")
        papel_novo = request.POST.get("papel")
        if papel_novo not in dict(AcessoAuditoriaD1.PAPEIS):
            papel_novo = AcessoAuditoriaD1.CONSULTA
        if acao == "adicionar":
            pk = request.POST.get("user") or ""
            if pk.isdigit() and User.objects.filter(pk=pk, is_active=True).exists():
                AcessoAuditoriaD1.objects.update_or_create(user_id=int(pk),
                                                           defaults={"papel": papel_novo, "criado_por": request.user})
                messages.success(request, "Pessoa liberada.")
            else:
                messages.error(request, "Escolha uma pessoa.")
        elif acao in ("papel", "remover"):
            acesso = get_object_or_404(AcessoAuditoriaD1, pk=request.POST.get("acesso"))
            if acao == "remover":
                acesso.delete()
                messages.success(request, "Acesso removido.")
            else:
                acesso.papel = papel_novo
                acesso.save(update_fields=["papel", "atualizado_em"])
                messages.success(request, "Papel atualizado.")
        return redirect("auditoria_d1:acessos")

    lista = list(AcessoAuditoriaD1.objects.select_related("user", "user__sector").order_by("-papel", "id"))
    usados = {a.user_id for a in lista}
    return render(request, "auditoria_d1/acessos.html", {
        "acessos": lista, "papeis": AcessoAuditoriaD1.PAPEIS, "aba": "acessos", "pode": capacidades(request.user),
        "usuarios": User.objects.filter(is_active=True).exclude(pk__in=usados).select_related("sector")
                                .order_by("first_name", "last_name"),
    })


@exige(SUPERADMIN)
def configuracao(request):
    """Interruptor do WhatsApp (nasce desligado) e o grupo de teste."""
    cfg = ConfiguracaoAuditoriaD1.get()
    if request.method == "POST":
        grupo = (request.POST.get("grupo_teste") or "").strip()[:120]
        ativo = request.POST.get("whatsapp_ativo") == "1"
        ConfiguracaoAuditoriaD1.objects.filter(pk=cfg.pk).update(whatsapp_ativo=ativo, grupo_teste=grupo,
                                                                 atualizado_por=request.user)
        messages.success(request, "Configuração salva. WhatsApp " + ("LIGADO." if ativo else "desligado."))
        return redirect("auditoria_d1:configuracao")
    try:
        grupos = sorted(((s.name, jid) for s, jid in whatsapp_lojas.grupos_lojas().values()), key=lambda x: x[0])
    except Exception:  # noqa: BLE001
        grupos = []
    return render(request, "auditoria_d1/configuracao.html", {
        "cfg": cfg, "aba": "configuracao", "pode": capacidades(request.user), "grupos": grupos,
        "disparos": DisparoWhatsappD1.objects.select_related("disparado_por")[:30],
        "estado": whatsapp_lojas.estado(),
    })


# ---------------------------------------------------------------- API: esteira
@api(CONSULTA)
def api_meta(request):
    c = categorias.CATEGORIAS
    return _json({"hoje": regras.hoje().isoformat(), "correcoes": c["correcoes"], "penalidades": c["penalidades"],
                  "regras": c["regras"], "origem_categorias": c["origem"], "usuario": nome_usuario(request.user),
                  "pode": capacidades(request.user)})


@api(CONSULTA)
def api_dias(request):
    inicio, fim = regras.mes(request.GET.get("mes"))
    return _json({"mes": inicio.strftime("%Y-%m"), "dias": regras.resumo_dias(inicio, fim)})


@api(CONSULTA)
def api_vendas(request):
    try:
        dia = dt.date.fromisoformat(request.GET.get("data") or regras.hoje().isoformat())
    except ValueError:
        return _json({"erro": "Data inválida"}, 400)
    return _json({"data": dia.isoformat(), "itens": regras.vendas_do_dia(dia)})


@api(CONSULTA)
def api_busca(request):
    """Busca geral: nº da venda/serviço ou CPF/CNPJ em qualquer dia (regras.buscar). ?ampliar=1 → 12 meses."""
    try:
        return _json(regras.buscar(request.GET.get("q"), ampliar=request.GET.get("ampliar") == "1"))
    except ValueError as e:
        return _json({"erro": str(e)}, 400)


@api(AUDITOR)
def api_planos(request):
    """Os mesmos planos da venda nova em /vendas/ (vendas.Plano ativos), para o 'Plano incorreto'. Grava-se o nome."""
    from vendas.models import Plano
    return _json([{"nome": p.nome, "segmentacao": p.get_segmentacao_display(), "valor": float(p.valor)}
                  for p in Plano.objects.filter(ativo=True).order_by("segmentacao", "nome", "valor")])


def _sem_acento(s):
    return regras._nome(s).lower()


CHAVE_USUARIOS = "auditoria_d1:usuarios_ativos"


def _usuarios_ativos():
    """Usuários ativos do portal (nome completo + setor principal), em cache por 5 minutos."""
    lista = cache.get(CHAVE_USUARIOS)
    if lista is None:
        lista = []
        for u in (User.objects.filter(is_active=True).select_related("sector")
                  .only("first_name", "last_name", "username", "sector__name").order_by("first_name", "last_name")):
            nome = nome_usuario(u)
            setor = u.sector.name if u.sector_id else ""
            lista.append({"nome": nome, "setor": setor, "_busca": _sem_acento(" ".join((nome, setor, u.username or "")))})
        cache.set(CHAVE_USUARIOS, lista, 300)
    return lista


@api(AUDITOR)
def api_usuarios(request):
    """Quem contestou: busca nos usuários ATIVOS do portal (todas as palavras, sem acento). Até 20, com o setor."""
    termos = _sem_acento(request.GET.get("q")).split()
    if not termos:
        return _json([])
    achados = [u for u in _usuarios_ativos() if all(t in u["_busca"] for t in termos)]
    return _json([{"nome": u["nome"], "setor": u["setor"]} for u in achados[:20]])


@api(AUDITOR, ("POST",))
def api_verificacoes(request):
    """Corpo: {itens: [{id_servico, data_venda, status, correcao, penalidade, receita_correta, ...}]}"""
    itens = _corpo(request).get("itens") or []
    if not itens:
        return _json({"erro": "Nada para salvar"}, 400)
    usuario = nome_usuario(request.user)
    try:
        datas = [dt.date.fromisoformat(str(i.get("data_venda"))[:10]) for i in itens]
    except ValueError:
        return _json({"erro": "Informe a data da venda de cada serviço"}, 400)
    origem = {o["id_servico"]: o for o in db.servicos_por_id([str(i.get("id_servico")) for i in itens], min(datas), max(datas))}
    registros = []
    for i in itens:
        o = origem.get(str(i.get("id_servico")))
        if not o:
            return _json({"erro": f"Serviço {i.get('id_servico')} não encontrado"}, 404)
        try:
            reg = regras.registro(i, o)
        except ValueError as e:
            return _json({"erro": str(e), "id_servico": o["id_servico"]}, 400)
        reg["verificado_por"] = usuario
        registros.append(reg)
    db.gravar_verificacoes(registros, usuario)
    return _json({"ok": True, "salvos": len(registros),
                  "itens": [{"id_servico": r["id_servico"], "acao": r["acao_necessaria"],
                             "receita_validada": r["receita_validada"]} for r in registros]})


@api(AUDITOR, ("DELETE",))
def api_reabrir(request, id_servico):
    db.reabrir(id_servico, nome_usuario(request.user))
    return _json({"ok": True})


DECISOES = ("PROCEDENTE", "IMPROCEDENTE")


@api(AUDITOR, ("POST",))
def api_contestacoes(request):
    """Corpo: {id_servico, decisao: PROCEDENTE|IMPROCEDENTE, solicitante, motivo}.
    Procedente: a venda volta (sai de novo no Exportar para desfazer a alteração no VivoGo)."""
    body = _corpo(request)
    decisao = (body.get("decisao") or "").upper()
    if decisao not in DECISOES:
        return _json({"erro": "Escolha se a contestação é procedente ou improcedente."}, 400)
    solicitante, motivo = regras._txt(body.get("solicitante"), 120), regras._txt(body.get("motivo"), 500)
    if not solicitante or not motivo:
        return _json({"erro": "Informe quem contestou e o motivo da contestação."}, 400)
    try:
        dados = db.contestar(str(body.get("id_servico")), decisao, solicitante, motivo, nome_usuario(request.user))
    except ValueError as e:
        return _json({"erro": str(e)}, 400)
    return _json({"ok": True, **{k: regras._iso(v) if isinstance(v, dt.datetime) else (regras._num(v) if isinstance(v, Decimal) else v)
                                 for k, v in dados.items() if k != "divergencia_contestada"}})


@api(AUDITOR, ("DELETE",))
def api_desfazer_contestacao(request, id_servico):
    try:
        db.desfazer_contestacao(id_servico, nome_usuario(request.user))
    except ValueError as e:
        return _json({"erro": str(e)}, 400)
    return _json({"ok": True})


@api(CONSULTA)
def api_historico(request, id_servico):
    return _json([{**h, "em": regras._iso(h["em"])} for h in db.historico(id_servico)])


# ---------------------------------------------------------------- API: exportações
def _arquivo(conteudo, nome, tipo):
    resp = HttpResponse(conteudo, content_type=tipo)
    resp["Content-Disposition"] = f"attachment; filename=\"{nome}\"; filename*=UTF-8''{quote(nome)}"
    return resp


@api(CONSULTA)
def api_exportar_planilha(request):
    inicio, fim = regras.mes(request.GET.get("mes"))
    return _arquivo(regras.planilha_auditoria(inicio, fim), f"Auditoria D-1 {inicio:%Y-%m}.xlsx", XLSX)


@api(AUDITOR)
def api_exportar(request):
    """CSV no layout de modelo_importacao_ged.csv com as vendas alteradas na auditoria do mês. O resumo (vendas
    no arquivo e as que precisam de ajuste manual no VivoGo) vai no cabeçalho X-Resumo."""
    inicio, fim = regras.mes(request.GET.get("mes"))
    dados, resumo = importacao_vivogo.gerar(db.vendas_divergentes(inicio, fim))
    resp = _arquivo(dados, f"Importacao VivoGo {inicio:%Y-%m}.csv", "text/csv; charset=windows-1252")
    resp["X-Resumo"] = quote(json.dumps(resumo, ensure_ascii=False, default=str))
    resp["Access-Control-Expose-Headers"] = "X-Resumo"
    return resp


# ---------------------------------------------------------------- API: cruzamento SVA
def _ler_upload(request, leitor, msg_sem, msg_erro):
    arq = request.FILES.get("base")
    if not arq or not arq.name:
        return None, None, _json({"erro": msg_sem}, 400)
    try:
        base = leitor(arq.read())
    except ValueError as e:
        return None, None, _json({"erro": str(e)}, 400)
    except Exception:  # noqa: BLE001
        return None, None, _json({"erro": msg_erro}, 400)
    return arq.name, base, None


@api(GESTOR, ("POST",))
def api_sva_previa(request):
    """Cruza a base enviada e mostra o que mudaria, sem gravar nada (o arquivo não é guardado)."""
    nome, base, erro = _ler_upload(request, cruzamento_sva.ler_base,
                                   "Envie a Base Analítica de Vendas de SVA (.xlsx).",
                                   "Não consegui ler o arquivo. Envie a base em .xlsx, como baixada da Vivo.")
    if erro:
        return erro
    resultados, _ = cruzamento_sva.cruzar(base)
    res = cruzamento_sva.resumo(resultados, len(base))
    linhas = [regras.resumir_linha(r) for r in resultados]
    marcacoes, sugestoes = regras.marcacoes_sva(linhas, res["anomes"])
    impacto = db.previa_cruzamento(cruzamento_sva.ORIGEM, res["anomes"], [m["id_servico"] for m in marcacoes])
    token = uuid.uuid4().hex
    regras.guardar_previa("sva", token, {"arquivo": nome, "resultados": linhas, "resumo": res})
    ultima = db.importacoes(cruzamento_sva.ORIGEM, 1)
    return _json({"token": token, "arquivo": nome, **res, **impacto,
                  "ultima": regras.imp_json(ultima[0]) if ultima else None,
                  "sugestoes_lista": [{"id_venda": s["id_venda"], "data": regras._iso(s["data_venda"]),
                                       "servico": s["servico_base"], "motivo": s["motivo"]} for s in sugestoes]})


@api(GESTOR, ("POST",))
def api_sva_aplicar(request):
    previa = regras.tirar_previa("sva", _corpo(request).get("token"))
    if not previa:
        return _json({"erro": "A prévia expirou. Importe a base de novo."}, 404)
    res = previa["resumo"]
    marcacoes, sugestoes = regras.marcacoes_sva(previa["resultados"], res["anomes"])
    meta = {"arquivo": previa["arquivo"], "base_de": res["base_de"], "base_ate": res["base_ate"],
            "linhas": res["linhas"], "nao_existe": res["nao_existe"]}
    out = db.aplicar_cruzamento(cruzamento_sva.ORIGEM, res["anomes"], meta, marcacoes, sugestoes,
                                previa["resultados"], nome_usuario(request.user))
    return _json({"ok": True, **out, "anomes": res["anomes"]})


@api(GESTOR)
def api_sva_importacoes(request):
    return _json([regras.imp_json(i) for i in db.importacoes(cruzamento_sva.ORIGEM)])


def _planilha_resultado(imp_id, abas, titulo):
    imp, linhas = db.resultado_importacao(imp_id)
    if not imp:
        return _json({"erro": "Importação não encontrada"}, 404)
    return _arquivo(regras.planilha_resultado(imp, linhas, abas),
                    f"{titulo} {imp['anomes']} (importação {imp_id}).xlsx", XLSX)


@api(GESTOR)
def api_sva_planilha(request, imp_id):
    return _planilha_resultado(imp_id, regras.ABAS_SVA, "Cruzamento SVA")


@api(GESTOR)
def api_fixa_planilha(request, imp_id):
    return _planilha_resultado(imp_id, regras.ABAS_FIXA, "Cruzamento Fixa")


def _imp_id(valor):
    try:
        return int(valor or 0)
    except (TypeError, ValueError):
        return 0


@api(GESTOR)
def api_sva_lojas(request):
    """Lojas que receberiam mensagem de uma importação (para o teste por loja)."""
    imp, linhas = db.resultado_importacao(_imp_id(request.GET.get("importacao_id")))
    if not imp:
        return _json({"erro": "Importação não encontrada"}, 404)
    lojas, sem_grupo = whatsapp_lojas.planejar(regras.sinalizar(linhas))
    cfg = ConfiguracaoAuditoriaD1.get()
    return _json({"lojas": lojas, "sem_grupo": sem_grupo, "ligado": cfg.whatsapp_ativo,
                  "grupo_teste": bool(cfg.grupo_teste)})


@api(GESTOR, ("GET", "POST"))
def api_sva_whatsapp(request):
    """GET: andamento do envio. POST: envia os SVAs a sinalizar de uma importação (teste=true → grupo de teste)."""
    if request.method == "GET":
        return _json(whatsapp_lojas.estado())
    body = _corpo(request)
    imp, linhas = db.resultado_importacao(_imp_id(body.get("importacao_id")))
    if not imp:
        return _json({"erro": "Importação não encontrada"}, 404)
    teste = bool(body.get("teste"))
    try:
        est = whatsapp_lojas.iniciar(regras.sinalizar(linhas), imp["anomes"], teste,
                                     regras._txt(body.get("loja")) if teste else None, request.user, imp["id"])
    except RuntimeError as e:
        return _json({"erro": str(e)}, 400)
    return _json(est)


@api(GESTOR, ("POST",))
def api_sva_whatsapp_cancelar(request):
    whatsapp_lojas.pedir_cancelamento()
    return _json({"ok": True})


# ---------------------------------------------------------------- API: cruzamento Fixa
@api(GESTOR, ("POST",))
def api_fixa_previa(request):
    """Cruza o Analítico de Vendas enviado com as vendas de fixa e mostra o que mudaria, sem gravar nada."""
    nome, base, erro = _ler_upload(request, cruzamento_fixa.ler_base,
                                   "Envie o ANALÍTICO DE VENDAS da Vivo (.xlsx).",
                                   "Não consegui ler o arquivo. Envie o Analítico em .xlsx, como baixado da Vivo.")
    if erro:
        return erro
    resultados, _ = cruzamento_fixa.cruzar(base)
    res = cruzamento_fixa.resumo(resultados, base)
    linhas = [regras.resumir_fixa(r) for r in resultados]
    marcacoes, sugestoes = regras.marcacoes_fixa(linhas)
    impacto = db.previa_cruzamento(cruzamento_fixa.ORIGEM, res["meses"], [m["id_servico"] for m in marcacoes])
    token = uuid.uuid4().hex
    regras.guardar_previa("fixa", token, {"arquivo": nome, "resultados": linhas, "resumo": res})
    ultima = db.importacoes(cruzamento_fixa.ORIGEM, 1)

    def lista(acao):
        return [{"id_venda": r["VG_ID_da_venda"], "data": r["VG_Data_venda"],
                 "servico": r.get("VG_Servico_alvo") or r["VG_Servicos"], "motivo": r["observacao"]}
                for r in linhas if r["acao"] == acao]

    return _json({"token": token, "arquivo": nome, **res, **impacto, "aplicar_servicos": len(marcacoes),
                  "ultima": regras.imp_json(ultima[0]) if ultima else None,
                  "aplicar_lista": lista("APLICAR"), "sugestoes_lista": lista("SUGESTAO")})


@api(GESTOR, ("POST",))
def api_fixa_aplicar(request):
    previa = regras.tirar_previa("fixa", _corpo(request).get("token"))
    if not previa:
        return _json({"erro": "A prévia expirou. Importe a base de novo."}, 404)
    res = previa["resumo"]
    marcacoes, sugestoes = regras.marcacoes_fixa(previa["resultados"])
    meta = {"arquivo": previa["arquivo"], "base_de": res["base_de"], "base_ate": res["base_ate"],
            "linhas": res["linhas"], "nao_existe": res["nao_localizadas"]}
    out = db.aplicar_cruzamento(cruzamento_fixa.ORIGEM, res["meses"], meta, marcacoes, sugestoes,
                                previa["resultados"], nome_usuario(request.user))
    return _json({"ok": True, **out, "meses": res["meses"]})


@api(GESTOR)
def api_fixa_importacoes(request):
    return _json([regras.imp_json(i) for i in db.importacoes(cruzamento_fixa.ORIGEM)])
