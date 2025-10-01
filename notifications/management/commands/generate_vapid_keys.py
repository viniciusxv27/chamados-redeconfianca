from django.core.management.base import BaseCommand
import os
import sys


class Command(BaseCommand):
    help = 'Generate VAPID keys for Web Push notifications'

    def add_arguments(self, parser):
        parser.add_argument(
            '--output-file',
            type=str,
            help='File to save the VAPID keys (default: print to console)',
        )
    
    def handle(self, *args, **options):
        try:
            # Gerar chaves usando cryptography diretamente
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import ec
            import base64
            
            # Gerar chave privada ECDSA P-256
            private_key = ec.generate_private_key(ec.SECP256R1())
            public_key = private_key.public_key()
            
            # Serializar chave privada
            private_pem = private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()
            )
            
            # Serializar chave pública em formato comprimido
            public_bytes = public_key.public_bytes(
                encoding=serialization.Encoding.X962,
                format=serialization.PublicFormat.UncompressedPoint
            )
            
            # Converter para base64 URL-safe
            public_key_b64 = base64.urlsafe_b64encode(public_bytes).decode('utf-8').rstrip('=')
            
            # Formatar as configurações
            config_text = f"""
# VAPID Configuration for Web Push Notifications
# Add these to your Django settings.py file

VAPID_PRIVATE_KEY = \"\"\"{private_pem.decode()}\"\"\"
VAPID_PUBLIC_KEY = "{public_key_b64}"
VAPID_CLAIMS = {{
    "sub": "mailto:admin@redeconfianca.com"
}}

# JavaScript code for frontend:
# const vapidPublicKey = '{public_key_b64}';
"""
            
            if options['output_file']:
                with open(options['output_file'], 'w') as f:
                    f.write(config_text)
                self.stdout.write(
                    self.style.SUCCESS(f'VAPID keys saved to {options["output_file"]}')
                )
            else:
                self.stdout.write(config_text)
            
            self.stdout.write(
                self.style.SUCCESS('VAPID keys generated successfully!')
            )
            self.stdout.write(
                self.style.WARNING('Remember to add these keys to your settings.py file!')
            )
            
        except ImportError as e:
            self.stdout.write(
                self.style.ERROR(f'Required library not found: {str(e)}')
            )
            sys.exit(1)
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'Error generating VAPID keys: {str(e)}')
            )
            sys.exit(1)