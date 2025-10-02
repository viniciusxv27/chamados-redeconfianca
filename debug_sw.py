#!/usr/bin/env python3
import requests
import json

def test_sw_endpoint():
    """Testa se o endpoint do Service Worker está funcionando"""
    try:
        response = requests.get('http://localhost:8000/sw.js')
        print(f"Status Code: {response.status_code}")
        print(f"Content-Type: {response.headers.get('Content-Type', 'Not Set')}")
        print(f"Service-Worker-Allowed: {response.headers.get('Service-Worker-Allowed', 'Not Set')}")
        print(f"Content Length: {len(response.text)} chars")
        print(f"First 200 chars: {response.text[:200]}...")
        
        if response.status_code == 200:
            print("✅ Service Worker endpoint está funcionando")
        else:
            print("❌ Service Worker endpoint com problema")
            
    except Exception as e:
        print(f"❌ Erro ao testar endpoint: {e}")

def test_notification_api():
    """Testa se as APIs de notificação estão funcionando"""
    try:
        # Testar página de configurações
        response = requests.get('http://localhost:8000/notifications/settings/')
        print(f"\nSettings page status: {response.status_code}")
        
        if response.status_code == 200:
            print("✅ Página de configurações carregando")
        else:
            print("❌ Problema na página de configurações")
            
    except Exception as e:
        print(f"❌ Erro ao testar APIs: {e}")

if __name__ == "__main__":
    print("🔧 Testando Service Worker e APIs de Notificação...")
    test_sw_endpoint()
    test_notification_api()