# Test view for upload debugging

from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.conf import settings
from django.views.decorators.csrf import ensure_csrf_cookie
import os

@login_required
@ensure_csrf_cookie
def test_upload_view(request):
    """View de teste para debug de uploads com DEBUG=False"""
    
    if request.method == 'POST':
        try:
            test_text = request.POST.get('test_text', '')
            test_file = request.FILES.get('test_file')
            
            messages.success(request, f'Texto recebido: {test_text}')
            
            if test_file:
                # Salvar arquivo na pasta de testes
                test_dir = os.path.join(settings.MEDIA_ROOT, 'test_uploads')
                os.makedirs(test_dir, exist_ok=True)
                
                file_path = os.path.join(test_dir, test_file.name)
                with open(file_path, 'wb+') as destination:
                    for chunk in test_file.chunks():
                        destination.write(chunk)
                
                messages.success(request, f'Arquivo {test_file.name} salvo com sucesso!')
            else:
                messages.warning(request, 'Nenhum arquivo enviado')
                
        except Exception as e:
            messages.error(request, f'Erro ao processar: {str(e)}')
            
        return redirect('test_upload')
    
    context = {
        'debug_status': settings.DEBUG,
        'user': request.user,
    }
    
    return render(request, 'test_upload.html', context)
