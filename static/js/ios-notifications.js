/**
 * iOS Push Notification Support
 * Handles iOS-specific push notification functionality
 */

class iOSNotificationManager {
    constructor() {
        this.isIOS = this.detectIOS();
        this.isStandalone = this.detectStandalone();
        this.supportsPWA = this.detectPWASupport();
        
        if (this.isIOS) {
            console.log('iOS detected, initializing iOS notification manager');
            this.init();
        }
    }
    
    detectIOS() {
        return /iPad|iPhone|iPod/.test(navigator.userAgent) || 
               (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
    }
    
    detectStandalone() {
        return window.navigator.standalone || 
               window.matchMedia('(display-mode: standalone)').matches;
    }
    
    detectPWASupport() {
        return 'serviceWorker' in navigator && 
               'PushManager' in window && 
               'Notification' in window;
    }
    
    init() {
        // Check iOS version for push notification support
        this.checkiOSVersion();
        
        // Add iOS-specific event listeners
        this.setupiOSEventListeners();
        
        // Show install prompt if needed
        this.handleInstallPrompt();
    }
    
    checkiOSVersion() {
        const match = navigator.userAgent.match(/OS (\d+)_(\d+)/);
        if (match) {
            const majorVersion = parseInt(match[1]);
            const minorVersion = parseInt(match[2]);
            
            console.log(`iOS Version: ${majorVersion}.${minorVersion}`);
            
            // Push notifications são suportadas no iOS 16.4+
            this.supportsPushNotifications = majorVersion > 16 || 
                                           (majorVersion === 16 && minorVersion >= 4);
            
            if (!this.supportsPushNotifications) {
                console.warn('Push notifications not supported on this iOS version. iOS 16.4+ required.');
                this.showUnsupportedMessage();
            }
        }
    }
    
    setupiOSEventListeners() {
        // Listen for app installation
        window.addEventListener('beforeinstallprompt', (e) => {
            e.preventDefault();
            this.deferredPrompt = e;
            this.showInstallBanner();
        });
        
        // Listen for app installation completion
        window.addEventListener('appinstalled', () => {
            console.log('PWA was installed');
            this.hideInstallBanner();
            // Request notification permission after installation
            setTimeout(() => this.requestNotificationPermission(), 1000);
        });
        
        // Handle visibility change for iOS
        document.addEventListener('visibilitychange', () => {
            if (!document.hidden && this.isStandalone) {
                // App became visible, check for notifications
                this.checkPendingNotifications();
            }
        });
    }
    
    async requestNotificationPermission() {
        if (!this.supportsPWA) {
            console.log('PWA features not supported');
            return false;
        }
        
        try {
            // For iOS, request permission through the service worker
            const registration = await navigator.serviceWorker.ready;
            
            // Check current permission
            let permission = Notification.permission;
            
            if (permission === 'default') {
                // Show custom iOS-friendly permission dialog
                const userConsent = await this.showCustomPermissionDialog();
                
                if (userConsent) {
                    permission = await Notification.requestPermission();
                }
            }
            
            if (permission === 'granted') {
                console.log('Notification permission granted');
                
                // For iOS, we need to make sure the app is added to home screen
                if (!this.isStandalone) {
                    this.showAddToHomeScreenPrompt();
                    return false;
                }
                
                // Subscribe to push notifications
                return await this.subscribeToPush(registration);
            } else {
                console.log('Notification permission denied');
                this.showPermissionDeniedMessage();
                return false;
            }
            
        } catch (error) {
            console.error('Error requesting notification permission:', error);
            this.showErrorMessage();
            return false;
        }
    }
    
    async subscribeToPush(registration) {
        try {
            // Get VAPID public key from server
            const response = await fetch('/api/notifications/vapid-key/');
            const data = await response.json();
            
            if (!data.success) {
                throw new Error('Failed to get VAPID key');
            }
            
            const subscription = await registration.pushManager.subscribe({
                userVisibleOnly: true,
                applicationServerKey: this.urlB64ToUint8Array(data.vapid_public_key)
            });
            
            // Send subscription to server
            const subscribeResponse = await fetch('/api/notifications/subscribe/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': document.querySelector('[name=csrfmiddlewaretoken]').value
                },
                body: JSON.stringify({
                    subscription: subscription.toJSON(),
                    platform: 'ios'
                })
            });
            
            const subscribeData = await subscribeResponse.json();
            
            if (subscribeData.success) {
                console.log('Successfully subscribed to push notifications');
                this.showSuccessMessage();
                return true;
            } else {
                throw new Error(subscribeData.error);
            }
            
        } catch (error) {
            console.error('Error subscribing to push notifications:', error);
            this.showSubscriptionErrorMessage();
            return false;
        }
    }
    
    showCustomPermissionDialog() {
        return new Promise((resolve) => {
            // Create custom iOS-style dialog
            const dialog = document.createElement('div');
            dialog.className = 'fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 px-4';
            dialog.innerHTML = `
                <div class="bg-white rounded-2xl p-6 max-w-sm w-full shadow-2xl">
                    <div class="text-center mb-6">
                        <div class="w-16 h-16 bg-blue-500 rounded-full flex items-center justify-center mx-auto mb-4">
                            <i class="fas fa-bell text-white text-2xl"></i>
                        </div>
                        <h3 class="text-lg font-semibold text-gray-900 mb-2">Permitir Notificações?</h3>
                        <p class="text-gray-600 text-sm leading-relaxed">
                            O Rede Confiança gostaria de enviar notificações sobre novos chamados, 
                            atualizações importantes e lembretes.
                        </p>
                    </div>
                    
                    <div class="space-y-3">
                        <button id="allow-notifications" 
                                class="w-full bg-blue-500 text-white py-3 px-4 rounded-xl font-medium hover:bg-blue-600 transition-colors">
                            Permitir
                        </button>
                        <button id="deny-notifications" 
                                class="w-full bg-gray-100 text-gray-700 py-3 px-4 rounded-xl font-medium hover:bg-gray-200 transition-colors">
                            Não permitir
                        </button>
                    </div>
                </div>
            `;
            
            document.body.appendChild(dialog);
            
            dialog.querySelector('#allow-notifications').addEventListener('click', () => {
                document.body.removeChild(dialog);
                resolve(true);
            });
            
            dialog.querySelector('#deny-notifications').addEventListener('click', () => {
                document.body.removeChild(dialog);
                resolve(false);
            });
        });
    }
    
    showAddToHomeScreenPrompt() {
        const prompt = document.createElement('div');
        prompt.className = 'fixed bottom-4 left-4 right-4 bg-blue-500 text-white p-4 rounded-lg shadow-lg z-50';
        prompt.innerHTML = `
            <div class="flex items-start space-x-3">
                <div class="flex-shrink-0">
                    <i class="fas fa-mobile-alt text-xl"></i>
                </div>
                <div class="flex-1">
                    <h4 class="font-medium mb-1">Adicionar à Tela de Início</h4>
                    <p class="text-sm opacity-90 mb-3">
                        Para receber notificações no iOS, adicione o app à tela de início:
                    </p>
                    <div class="text-xs opacity-80 space-y-1">
                        <div>1. Toque no botão de compartilhar <i class="fas fa-share"></i></div>
                        <div>2. Selecione "Adicionar à Tela de Início"</div>
                        <div>3. Toque em "Adicionar"</div>
                    </div>
                </div>
                <button onclick="this.parentElement.parentElement.remove()" 
                        class="flex-shrink-0 text-white hover:text-gray-200">
                    <i class="fas fa-times"></i>
                </button>
            </div>
        `;
        
        document.body.appendChild(prompt);
        
        // Auto-remove after 15 seconds
        setTimeout(() => {
            if (prompt.parentElement) {
                prompt.parentElement.removeChild(prompt);
            }
        }, 15000);
    }
    
    showUnsupportedMessage() {
        const message = document.createElement('div');
        message.className = 'fixed top-4 left-4 right-4 bg-yellow-500 text-white p-4 rounded-lg shadow-lg z-50';
        message.innerHTML = `
            <div class="flex items-start space-x-3">
                <div class="flex-shrink-0">
                    <i class="fas fa-exclamation-triangle text-xl"></i>
                </div>
                <div class="flex-1">
                    <h4 class="font-medium mb-1">Notificações Limitadas</h4>
                    <p class="text-sm opacity-90">
                        Seu dispositivo iOS não suporta notificações push. 
                        Atualize para iOS 16.4 ou superior para receber notificações.
                    </p>
                </div>
                <button onclick="this.parentElement.parentElement.remove()" 
                        class="flex-shrink-0 text-white hover:text-gray-200">
                    <i class="fas fa-times"></i>
                </button>
            </div>
        `;
        
        document.body.appendChild(message);
        
        // Auto-remove after 10 seconds
        setTimeout(() => {
            if (message.parentElement) {
                message.parentElement.removeChild(message);
            }
        }, 10000);
    }
    
    showSuccessMessage() {
        const message = document.createElement('div');
        message.className = 'fixed top-4 left-4 right-4 bg-green-500 text-white p-4 rounded-lg shadow-lg z-50';
        message.innerHTML = `
            <div class="flex items-center space-x-3">
                <i class="fas fa-check-circle text-xl"></i>
                <div>
                    <h4 class="font-medium">Notificações Ativadas!</h4>
                    <p class="text-sm opacity-90">Você receberá notificações sobre atualizações importantes.</p>
                </div>
            </div>
        `;
        
        document.body.appendChild(message);
        
        setTimeout(() => {
            if (message.parentElement) {
                message.parentElement.removeChild(message);
            }
        }, 5000);
    }
    
    showErrorMessage() {
        const message = document.createElement('div');
        message.className = 'fixed top-4 left-4 right-4 bg-red-500 text-white p-4 rounded-lg shadow-lg z-50';
        message.innerHTML = `
            <div class="flex items-center space-x-3">
                <i class="fas fa-times-circle text-xl"></i>
                <div>
                    <h4 class="font-medium">Erro nas Notificações</h4>
                    <p class="text-sm opacity-90">Não foi possível ativar as notificações. Tente novamente.</p>
                </div>
            </div>
        `;
        
        document.body.appendChild(message);
        
        setTimeout(() => {
            if (message.parentElement) {
                message.parentElement.removeChild(message);
            }
        }, 5000);
    }
    
    checkPendingNotifications() {
        // Check for any pending notifications when app becomes visible
        if (this.isStandalone && 'serviceWorker' in navigator) {
            navigator.serviceWorker.ready.then(registration => {
                return registration.getNotifications();
            }).then(notifications => {
                console.log(`Found ${notifications.length} pending notifications`);
            }).catch(error => {
                console.error('Error checking pending notifications:', error);
            });
        }
    }
    
    urlB64ToUint8Array(base64String) {
        const padding = '='.repeat((4 - base64String.length % 4) % 4);
        const base64 = (base64String + padding)
            .replace(/\-/g, '+')
            .replace(/_/g, '/');
        
        const rawData = window.atob(base64);
        const outputArray = new Uint8Array(rawData.length);
        
        for (let i = 0; i < rawData.length; ++i) {
            outputArray[i] = rawData.charCodeAt(i);
        }
        return outputArray;
    }
    
    // Public method to request permissions
    async enableNotifications() {
        return await this.requestNotificationPermission();
    }
}

// Initialize iOS notification manager
const iOSNotifications = new iOSNotificationManager();

// Export for use in other scripts
window.iOSNotifications = iOSNotifications;