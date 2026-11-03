"""Botão Importar de /financeiro/pagar/: sobe o CSV, confere o que vai entrar e confirma."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import redirect, render

from . import importacao_titulos as imp
from .permissoes import exige, lojas_permitidas
from .views import _contexto_base

TAMANHO_MAXIMO = 5 * 1024 * 1024    # o conteúdo volta inteiro na página de conferência


@login_required
@exige('pagar.editar')
def importar(request):
    contexto = _contexto_base(request, 'pagar')
    if request.method == 'POST':
        confirmando = request.POST.get('etapa') == 'confirmar'
        if confirmando:
            texto, nome = request.POST.get('conteudo') or '', (request.POST.get('nome') or '')[:200]
        else:
            arquivo = request.FILES.get('arquivo')
            if not arquivo:
                messages.error(request, 'Escolha o arquivo CSV.')
                return redirect('financeiro:pagar_importar')
            if arquivo.size > TAMANHO_MAXIMO:
                messages.error(request, 'O arquivo passa de 5 MB: divida por período.')
                return redirect('financeiro:pagar_importar')
            texto, nome = imp.decodificar(arquivo.read()), arquivo.name[:200]
        try:
            analise = imp.analisar(texto, lojas_permitidas(request.user))
        except imp.ArquivoInvalido as exc:
            messages.error(request, str(exc))
            return redirect('financeiro:pagar_importar')
        if confirmando:
            n = imp.importar(analise, nome_arquivo=nome, usuario=request.user, request=request)
            messages.success(request, f'{n} conta(s) a pagar importada(s) de {nome}.' if n else 'Nada novo para importar.')
            return redirect('financeiro:pagar')
        contexto.update({'analise': analise, 'conteudo': texto, 'nome': nome, 'amostra': analise.novos[:15]})
    return render(request, 'financeiro/importar_titulos.html', contexto)


@login_required
@exige('pagar.editar')
def importar_modelo(request):
    resposta = HttpResponse(imp.modelo_csv(), content_type='text/csv; charset=iso-8859-1')
    resposta['Content-Disposition'] = 'attachment; filename="modelo_contas_a_pagar.csv"'
    return resposta
