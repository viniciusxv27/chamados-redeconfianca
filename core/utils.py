import os
import uuid
from django.utils.text import slugify
from django.utils import timezone


def generate_unique_filename(instance, filename):
    """
    Gera um nome único para arquivo baseado em UUID para evitar conflitos
    """
    # Obter a extensão do arquivo original
    name, ext = os.path.splitext(filename)
    
    # Gerar um UUID único
    unique_id = str(uuid.uuid4())
    
    # Criar nome único mantendo a extensão
    unique_filename = f"{unique_id}{ext.lower()}"
    
    return unique_filename


def generate_unique_filename_with_prefix(prefix):
    """
    Gera uma função de upload que inclui um prefixo no nome do arquivo
    """
    def upload_to(instance, filename):
        # Obter a extensão do arquivo
        name, ext = os.path.splitext(filename)
        
        # Gerar UUID único
        unique_id = str(uuid.uuid4())
        
        # Criar nome com prefixo
        unique_filename = f"{prefix}_{unique_id}{ext.lower()}"
        
        return unique_filename
    
    return upload_to


def upload_user_profile_photo(instance, filename):
    """Upload path para fotos de perfil de usuário"""
    ext = os.path.splitext(filename)[1].lower()
    unique_filename = f"profile_{uuid.uuid4()}{ext}"
    return f"profiles/{unique_filename}"


def upload_asset_photo(instance, filename):
    """Upload path para fotos de ativos"""
    ext = os.path.splitext(filename)[1].lower()
    unique_filename = f"asset_{uuid.uuid4()}{ext}"
    return f"assets/{unique_filename}"


def upload_communication_attachment(instance, filename):
    """Upload path para anexos de comunicados"""
    ext = os.path.splitext(filename)[1].lower()
    unique_filename = f"comm_{uuid.uuid4()}{ext}"
    return f"communications/{unique_filename}"


def upload_prize_image(instance, filename):
    """Upload path para imagens de prêmios"""
    ext = os.path.splitext(filename)[1].lower()
    unique_filename = f"prize_{uuid.uuid4()}{ext}"
    return f"prizes/{unique_filename}"


def upload_ticket_attachment(instance, filename):
    """Upload path para anexos de tickets"""
    ext = os.path.splitext(filename)[1].lower()
    unique_filename = f"ticket_{uuid.uuid4()}{ext}"
    return f"tickets/{unique_filename}"


def upload_report_evidence(instance, filename):
    """Upload path para evidências de denúncias"""
    ext = os.path.splitext(filename)[1].lower()
    unique_filename = f"report_{uuid.uuid4()}{ext}"
    return f"reports/evidence/{unique_filename}"


def upload_tutorial_pdf(instance, filename):
    """Upload path para PDFs de tutorial"""
    ext = os.path.splitext(filename)[1].lower()
    unique_filename = f"tutorial_{uuid.uuid4()}{ext}"
    return f"tutorials/{unique_filename}"


def upload_exit_interview_video(instance, filename):
    """Upload path para vídeos de entrevista de desligamento"""
    ext = os.path.splitext(filename)[1].lower()
    unique_filename = f"entrevista_{uuid.uuid4()}{ext}"
    return f"entrevistas_desligamento/{unique_filename}"


def processo_de_teste():
    """Este processo é um script de teste do projeto (`teste_*.py`)?

    Os testes daqui rodam como scripts soltos contra o banco de dev, que tem
    colaboradores de verdade com telefone e e-mail. Um teste que criou
    comunicado "para todos" sem neutralizar o envio chegou a disparar WhatsApp
    real. Os pontos de saída de WhatsApp e e-mail perguntam isto antes de
    enviar — assim nenhum teste manda mensagem, nem o que esquecer do mock.
    """
    import os
    import sys
    nome = os.path.basename(sys.argv[0]) if sys.argv else ''
    return nome.startswith('teste_') and nome.endswith('.py')


def em_segundo_plano(funcao, *args, nome='segundo-plano', **kwargs):
    """Roda ``funcao`` numa thread depois do commit — quem clicou não espera o envio.

    Feito para avisos (push, OneSignal, e-mail, WhatsApp): com o envio dentro do clique,
    assumir ou mudar o status de um chamado levava ~21 s (limite de 30 s do OneSignal) e
    segurava um worker do portal (25/11/2026). A thread abre a própria conexão com o
    banco e a fecha no fim; erro vai para o log, nunca para a tela.

    Em script de teste (``teste_*.py``) roda na hora: os testes rodam numa transação
    desfeita, onde o on_commit nunca dispara, e conferem o efeito logo em seguida.
    """
    import logging
    import threading

    from django.db import close_old_connections, transaction

    if processo_de_teste():
        return funcao(*args, **kwargs)

    def _rodar():
        try:
            close_old_connections()
            funcao(*args, **kwargs)
        except Exception:  # noqa: BLE001
            logging.getLogger('core.segundo_plano').exception('%s falhou', nome)
        finally:
            close_old_connections()

    transaction.on_commit(lambda: threading.Thread(target=_rodar, name=nome, daemon=True).start())
