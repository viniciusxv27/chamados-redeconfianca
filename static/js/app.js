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

// Export functions to global scope for inline handlers
window.showNotification = showNotification;
window.removeNotification = removeNotification;
window.toggleSidebar = toggleSidebar;
window.copyToClipboard = copyToClipboard;
