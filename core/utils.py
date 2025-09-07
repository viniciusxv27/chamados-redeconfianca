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
