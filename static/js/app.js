// JavaScript principal para Rede Confiança

document.addEventListener('DOMContentLoaded', function() {
    // Inicializar componentes
    initializeTooltips();
    initializeModals();
    initializeDropdowns();
    initializeNotifications();
    
    // Auto-hide alerts after 5 seconds
    setTimeout(() => {
        const alerts = document.querySelectorAll('.alert-auto-hide');
        alerts.forEach(alert => {
            fadeOut(alert);
        });
    }, 5000);
});

// Tooltips
function initializeTooltips() {
    const tooltipTriggers = document.querySelectorAll('[data-tooltip]');
    
    tooltipTriggers.forEach(trigger => {
        trigger.addEventListener('mouseenter', showTooltip);
        trigger.addEventListener('mouseleave', hideTooltip);
    });
}

function showTooltip(event) {
    const text = event.target.getAttribute('data-tooltip');
    const tooltip = document.createElement('div');
    tooltip.className = 'absolute bg-gray-800 text-white text-xs px-2 py-1 rounded shadow-lg z-50';
    tooltip.textContent = text;
    tooltip.id = 'tooltip';
    
    document.body.appendChild(tooltip);
    
    const rect = event.target.getBoundingClientRect();
    tooltip.style.left = rect.left + 'px';
    tooltip.style.top = (rect.top - tooltip.offsetHeight - 5) + 'px';
}

function hideTooltip() {
    const tooltip = document.getElementById('tooltip');
    if (tooltip) {
        tooltip.remove();
    }
}

// Modals
function initializeModals() {
    const modalTriggers = document.querySelectorAll('[data-modal-target]');
    const modalCloses = document.querySelectorAll('[data-modal-close]');
    
    modalTriggers.forEach(trigger => {
        trigger.addEventListener('click', function() {
            const targetId = this.getAttribute('data-modal-target');
            const modal = document.getElementById(targetId);
            if (modal) {
                showModal(modal);
            }
        });
    });
    
    modalCloses.forEach(close => {
        close.addEventListener('click', function() {
            const modal = this.closest('.modal');
            if (modal) {
                hideModal(modal);
            }
        });
    });
}

function showModal(modal) {
    modal.classList.remove('hidden');
    modal.classList.add('flex');
    document.body.style.overflow = 'hidden';
    
    // Fade in animation
    setTimeout(() => {
        modal.classList.add('opacity-100');
        const content = modal.querySelector('.modal-content');
        if (content) {
            content.classList.add('scale-100');
        }
    }, 10);
}

function hideModal(modal) {
    modal.classList.remove('opacity-100');
    const content = modal.querySelector('.modal-content');
    if (content) {
        content.classList.remove('scale-100');
    }
    
    setTimeout(() => {
        modal.classList.add('hidden');
        modal.classList.remove('flex');
        document.body.style.overflow = '';
    }, 200);
}

// Dropdowns
function initializeDropdowns() {
    const dropdownTriggers = document.querySelectorAll('[data-dropdown-toggle]');
    
    dropdownTriggers.forEach(trigger => {
        trigger.addEventListener('click', function(e) {
            e.stopPropagation();
            const targetId = this.getAttribute('data-dropdown-toggle');
            const dropdown = document.getElementById(targetId);
            
            // Fechar outros dropdowns
            document.querySelectorAll('.dropdown-menu').forEach(menu => {
                if (menu !== dropdown) {
                    menu.classList.add('hidden');
                }
            });
            
            // Toggle dropdown atual
            if (dropdown) {
                dropdown.classList.toggle('hidden');
            }
        });
    });
    
    // Fechar dropdown ao clicar fora
    document.addEventListener('click', function() {
        document.querySelectorAll('.dropdown-menu').forEach(menu => {
            menu.classList.add('hidden');
        });
    });
}

// Notifications
function initializeNotifications() {
    // API para mostrar notificações
    window.showNotification = function(message, type = 'info', duration = 5000) {
        const notification = createNotification(message, type);
        document.body.appendChild(notification);
        
        // Auto-remove after duration
        setTimeout(() => {
            removeNotification(notification);
        }, duration);
        
        return notification;
    };
}

function createNotification(message, type) {
    const notification = document.createElement('div');
    notification.className = `notification ${getNotificationClass(type)}`;
    
    const icon = getNotificationIcon(type);
    
    notification.innerHTML = `
        <div class="flex items-center">
            <div class="flex-shrink-0">
                ${icon}
            </div>
            <div class="ml-3">
                <p class="text-sm font-medium">${message}</p>
            </div>
            <div class="ml-auto pl-3">
                <button onclick="removeNotification(this.parentElement.parentElement)" 
                        class="text-gray-400 hover:text-gray-600">
                    <i class="fas fa-times"></i>
                </button>
            </div>
        </div>
    `;
    
    return notification;
}

function getNotificationClass(type) {
    const classes = {
        'success': 'bg-green-100 border border-green-400 text-green-700',
        'error': 'bg-red-100 border border-red-400 text-red-700',
        'warning': 'bg-yellow-100 border border-yellow-400 text-yellow-700',
        'info': 'bg-blue-100 border border-blue-400 text-blue-700'
    };
    return classes[type] || classes['info'];
}

function getNotificationIcon(type) {
    const icons = {
        'success': '<i class="fas fa-check-circle text-green-400"></i>',
        'error': '<i class="fas fa-exclamation-circle text-red-400"></i>',
        'warning': '<i class="fas fa-exclamation-triangle text-yellow-400"></i>',
        'info': '<i class="fas fa-info-circle text-blue-400"></i>'
    };
    return icons[type] || icons['info'];
}

function removeNotification(notification) {
    notification.style.transform = 'translateX(100%)';
    notification.style.opacity = '0';
    
    setTimeout(() => {
        if (notification.parentElement) {
            notification.parentElement.removeChild(notification);
        }
    }, 300);
}

// Utility functions
function fadeOut(element) {
    element.style.opacity = '0';
    element.style.transform = 'translateY(-10px)';
    
    setTimeout(() => {
        element.style.display = 'none';
    }, 300);
}

function fadeIn(element) {
    element.style.display = '';
    element.style.opacity = '0';
    element.style.transform = 'translateY(-10px)';
    
    setTimeout(() => {
        element.style.opacity = '1';
        element.style.transform = 'translateY(0)';
    }, 10);
}

// API AJAX helpers
function apiRequest(url, method = 'GET', data = null) {
    const config = {
        method: method,
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': getCsrfToken()
        }
    };
    
    if (data) {
        config.body = JSON.stringify(data);
    }
    
    return fetch(url, config)
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            return response.json();
        });
}

function getCsrfToken() {
    const tokenElement = document.querySelector('[name=csrfmiddlewaretoken]');
    return tokenElement ? tokenElement.value : '';
}

// Form helpers
function serializeForm(form) {
    const formData = new FormData(form);
    const data = {};
    
    for (let [key, value] of formData.entries()) {
        data[key] = value;
    }
    
    return data;
}

// Loading states
function showLoading(element) {
    const spinner = document.createElement('div');
    spinner.className = 'spinner inline-block mr-2';
    spinner.id = 'loading-spinner';
    
    element.prepend(spinner);
    element.disabled = true;
}

function hideLoading(element) {
    const spinner = element.querySelector('#loading-spinner');
    if (spinner) {
        spinner.remove();
    }
    element.disabled = false;
}

// Sidebar toggle for mobile
function toggleSidebar() {
    const sidebar = document.querySelector('.sidebar');
    if (sidebar) {
        sidebar.classList.toggle('open');
    }
}

// Search functionality
function initializeSearch() {
    const searchInput = document.querySelector('#search-input');
    if (searchInput) {
        let searchTimeout;
        
        searchInput.addEventListener('input', function() {
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(() => {
                performSearch(this.value);
            }, 300);
        });
    }
}

function performSearch(query) {
    if (query.length < 2) return;
    
    // Implementar busca conforme necessário
    console.log('Searching for:', query);
}

// Copy to clipboard
function copyToClipboard(text) {
    navigator.clipboard.writeText(text).then(() => {
        showNotification('Copiado para a área de transferência!', 'success');
    }).catch(() => {
        // Fallback para navegadores mais antigos
        const textArea = document.createElement('textarea');
        textArea.value = text;
        document.body.appendChild(textArea);
        textArea.select();
        document.execCommand('copy');
        document.body.removeChild(textArea);
        showNotification('Copiado para a área de transferência!', 'success');
    });
}

// =============================================
// SISTEMA DE NOTIFICAÇÕES EM TEMPO REAL
// =============================================

// Variáveis globais para notificações
let notificationBell = null;
let notificationBadge = null;
let notificationDropdown = null;
let currentUnreadCount = 0;

// Inicializar sistema de notificações
function initializeNotificationSystem() {
    // Procurar elementos da UI
    notificationBell = document.getElementById('notification-bell');
    notificationBadge = document.getElementById('notification-badge');
    notificationDropdown = document.getElementById('notification-dropdown');
    
    if (notificationBell) {
        // Adicionar event listener para toggle do dropdown
        notificationBell.addEventListener('click', toggleNotificationDropdown);
        
        // Carregar notificações iniciais
        loadRecentNotifications();
        
        // Atualizar contador a cada 30 segundos
        setInterval(updateUnreadCount, 30000);
    }
    
    // Inicializar suporte a notificações push (incluindo iOS)
    initializePushNotifications();
}

// Inicializar sistema de push notifications com suporte ao iOS
async function initializePushNotifications() {
    // Verificar se o serviço está disponível
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
        console.log('Push notifications não suportadas neste navegador');
        return;
    }
    
    try {
        // Registrar service worker
        const registration = await navigator.serviceWorker.register('/sw.js');
        console.log('Service Worker registrado com sucesso');
        
        // Aguardar o service worker estar pronto
        await navigator.serviceWorker.ready;
        
        // Verificar se é iOS e se tem suporte
        const isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) || 
                     (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
        
        if (isIOS) {
            console.log('iOS detectado - usando gestor específico para iOS');
            // O iOSNotificationManager será inicializado automaticamente
            
            // Verificar se o app está em standalone mode (adicionado à tela inicial)
            const isStandalone = window.navigator.standalone || 
                               window.matchMedia('(display-mode: standalone)').matches;
            
            if (!isStandalone) {
                console.log('App não está em modo standalone - funcionalidades limitadas');
                showIOSInstallPrompt();
                return;
            }
        }
        
        // Verificar permissão atual
        const permission = await Notification.requestPermission();
        
        if (permission === 'granted') {
            console.log('Permissão de notificação concedida');
            
            if (isIOS && window.iOSNotifications) {
                // Usar o gestor específico do iOS
                await window.iOSNotifications.enableNotifications();
            } else {
                // Usar o sistema padrão para outros navegadores
                await subscribeToNotifications(registration);
            }
        } else {
            console.log('Permissão de notificação negada');
        }
        
    } catch (error) {
        console.error('Erro ao inicializar push notifications:', error);
    }
}

// Subscrever às notificações push (para navegadores não-iOS)
async function subscribeToNotifications(registration) {
    try {
        // Obter chave VAPID pública
        const response = await fetch('/api/notifications/vapid-key/');
        const data = await response.json();
        
        if (!data.success) {
            throw new Error('Falha ao obter chave VAPID');
        }
        
        // Subscrever
        const subscription = await registration.pushManager.subscribe({
            userVisibleOnly: true,
            applicationServerKey: urlB64ToUint8Array(data.vapid_public_key)
        });
        
        // Enviar subscrição para o servidor
        const subscribeResponse = await fetch('/api/notifications/subscribe/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': document.querySelector('[name=csrfmiddlewaretoken]').value
            },
            body: JSON.stringify({
                subscription: subscription.toJSON(),
                platform: 'web'
            })
        });
        
        const subscribeData = await subscribeResponse.json();
        
        if (subscribeData.success) {
            console.log('Subscrição realizada com sucesso');
            showNotificationSuccess();
        } else {
            throw new Error(subscribeData.error);
        }
        
    } catch (error) {
        console.error('Erro ao subscrever às notificações:', error);
        showNotificationError();
    }
}

// Mostrar prompt para adicionar à tela inicial no iOS
function showIOSInstallPrompt() {
    const prompt = document.createElement('div');
    prompt.className = 'fixed bottom-4 left-4 right-4 bg-blue-500 text-white p-4 rounded-lg shadow-lg z-50';
    prompt.innerHTML = `
        <div class="flex items-start space-x-3">
            <div class="flex-shrink-0">
                <i class="fas fa-mobile-alt text-xl"></i>
            </div>
            <div class="flex-1">
                <h4 class="font-medium mb-1">Instalar App</h4>
                <p class="text-sm opacity-90 mb-2">
                    Para receber notificações, adicione o app à sua tela de início:
                </p>
                <div class="text-xs opacity-80 space-y-1">
                    <div>1. Toque no botão de compartilhar <i class="fas fa-share"></i></div>
                    <div>2. Selecione "Adicionar à Tela de Início"</div>
                </div>
            </div>
            <button onclick="this.parentElement.parentElement.remove()" 
                    class="flex-shrink-0 text-white hover:text-gray-200">
                <i class="fas fa-times"></i>
            </button>
        </div>
    `;
    
    document.body.appendChild(prompt);
    
    // Auto-remove após 20 segundos
    setTimeout(() => {
        if (prompt.parentElement) {
            prompt.parentElement.removeChild(prompt);
        }
    }, 20000);
}

// Mostrar mensagem de sucesso
function showNotificationSuccess() {
    showNotification('Notificações ativadas com sucesso!', 'success');
}

// Mostrar mensagem de erro
function showNotificationError() {
    showNotification('Erro ao ativar notificações. Tente novamente.', 'error');
}

// Converter chave VAPID
function urlB64ToUint8Array(base64String) {
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

// Toggle do dropdown de notificações
function toggleNotificationDropdown() {
    if (notificationDropdown) {
        notificationDropdown.classList.toggle('hidden');
        
        // Se está abrindo, carregar notificações recentes
        if (!notificationDropdown.classList.contains('hidden')) {
            loadRecentNotifications();
        }
    }
}

// Atualizar contador de notificações não lidas
async function updateUnreadCount() {
    try {
        const response = await fetch('/notifications/api/unread-count/');
        const data = await response.json();
        
        if (data.success) {
            currentUnreadCount = data.unread_count;
            updateNotificationBadge(currentUnreadCount);
        }
    } catch (error) {
        console.log('Erro ao carregar contador de notificações:', error);
    }
}

// Carregar notificações recentes
async function loadRecentNotifications() {
    try {
        const response = await fetch('/notifications/api/recent/');
        const data = await response.json();
        
        if (data.success) {
            renderNotificationDropdown(data.notifications);
            currentUnreadCount = data.notifications.filter(n => !n.is_read).length;
            updateNotificationBadge(currentUnreadCount);
        }
    } catch (error) {
        console.log('Erro ao carregar notificações:', error);
    }
}

// Atualizar badge de notificações
function updateNotificationBadge(count) {
    if (notificationBadge) {
        if (count > 0) {
            notificationBadge.textContent = count > 99 ? '99+' : count;
            notificationBadge.classList.remove('hidden');
        } else {
            notificationBadge.classList.add('hidden');
        }
    }
}

// Renderizar dropdown de notificações
function renderNotificationDropdown(notifications) {
    if (!notificationDropdown) return;
    
    let html = '<div class="p-4 border-b border-gray-200"><h3 class="text-sm font-medium text-gray-900">Notificações Recentes</h3></div>';
    
    if (notifications.length === 0) {
        html += '<div class="p-4 text-center text-gray-500"><p class="text-sm">Nenhuma notificação</p></div>';
    } else {
        html += '<div class="max-h-80 overflow-y-auto">';
        
        notifications.forEach(notification => {
            const bgClass = notification.is_read ? 'bg-white' : 'bg-blue-50';
            const iconColor = notification.type.color || 'blue';
            
            html += `
                <div class="${bgClass} p-4 border-b border-gray-100 hover:bg-gray-50 cursor-pointer" onclick="handleNotificationClick(${notification.id}, '${notification.action_url || ''}')">
                    <div class="flex items-start space-x-3">
                        <div class="flex-shrink-0">
                            <div class="w-8 h-8 bg-${iconColor}-100 rounded-full flex items-center justify-center">
                                <i class="${notification.type.icon} text-${iconColor}-600 text-sm"></i>
                            </div>
                        </div>
                        <div class="flex-1 min-w-0">
                            <p class="text-sm font-medium text-gray-900">${notification.title}</p>
                            <p class="text-xs text-gray-600 mt-1">${notification.message}</p>
                            <p class="text-xs text-gray-400 mt-1">${formatNotificationTime(notification.sent_at)}</p>
                        </div>
                        ${!notification.is_read ? '<div class="w-2 h-2 bg-blue-600 rounded-full"></div>' : ''}
                    </div>
                </div>
            `;
        });
        
        html += '</div>';
        
        // Footer com link para ver todas
        html += `
            <div class="p-3 border-t border-gray-200 bg-gray-50">
                <a href="/notifications/my/" class="text-sm text-blue-600 hover:text-blue-800 font-medium">
                    Ver todas as notificações
                </a>
            </div>
        `;
    }
    
    notificationDropdown.innerHTML = html;
}

// Lidar com clique em notificação
async function handleNotificationClick(notificationId, actionUrl) {
    try {
        // Marcar como lida
        await fetch(`/notifications/api/${notificationId}/mark-read/`, {
            method: 'POST',
            headers: {
                'X-CSRFToken': document.querySelector('[name=csrfmiddlewaretoken]').value,
                'Content-Type': 'application/json',
            },
        });
        
        // Atualizar contador
        updateUnreadCount();
        
        // Fechar dropdown
        if (notificationDropdown) {
            notificationDropdown.classList.add('hidden');
        }
        
        // Redirecionar se há URL de ação
        if (actionUrl && actionUrl !== '') {
            window.location.href = actionUrl;
        }
        
    } catch (error) {
        console.log('Erro ao processar notificação:', error);
    }
}

// Marcar todas como lidas
async function markAllNotificationsAsRead() {
    try {
        const response = await fetch('/notifications/api/mark-all-read/', {
            method: 'POST',
            headers: {
                'X-CSRFToken': document.querySelector('[name=csrfmiddlewaretoken]').value,
                'Content-Type': 'application/json',
            },
        });
        
        const data = await response.json();
        if (data.success) {
            updateNotificationBadge(0);
            loadRecentNotifications();
            showNotification('Todas as notificações foram marcadas como lidas', 'success');
        }
    } catch (error) {
        console.log('Erro ao marcar notificações como lidas:', error);
    }
}

// Formatar tempo da notificação
function formatNotificationTime(timestamp) {
    const date = new Date(timestamp);
    const now = new Date();
    const diff = now - date;
    
    if (diff < 60000) return 'agora';
    if (diff < 3600000) return `${Math.floor(diff / 60000)}m atrás`;
    if (diff < 86400000) return `${Math.floor(diff / 3600000)}h atrás`;
    
    return date.toLocaleDateString();
}

// Export functions to global scope for inline handlers
// showNotification já está definida como window.showNotification na função initializeNotifications
window.removeNotification = removeNotification;
window.toggleSidebar = toggleSidebar;
window.copyToClipboard = copyToClipboard;
window.initializeNotificationSystem = initializeNotificationSystem;
window.handleNotificationClick = handleNotificationClick;
window.markAllNotificationsAsRead = markAllNotificationsAsRead;
