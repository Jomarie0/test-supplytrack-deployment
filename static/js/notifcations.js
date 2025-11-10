// // static/js/notifications.js
// class NotificationManager {
//     constructor() {
//         this.socket = null;
//         this.reconnectAttempts = 0;
//         this.maxReconnectAttempts = 5;
//         this.reconnectDelay = 1000;
//         this.notifications = [];
//         this.init();
//     }

//     init() {
//         this.createNotificationContainer();
//         this.connectWebSocket();
//         this.bindEvents();
//     }

//     createNotificationContainer() {
//         if (!document.getElementById('notification-container')) {
//             const container = document.createElement('div');
//             container.id = 'notification-container';
//             container.className = 'notification-container';
//             container.style.cssText = `
//                 position: fixed;
//                 top: 20px;
//                 right: 20px;
//                 z-index: 10000;
//                 max-width: 400px;
//             `;
//             document.body.appendChild(container);
//         }
//     }

//     connectWebSocket() {
//         const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
//         const wsUrl = `${protocol}//${window.location.host}/ws/notifications/`;
        
//         try {
//             this.socket = new WebSocket(wsUrl);
            
//             this.socket.onopen = (e) => {
//                 console.log('Notification WebSocket connected');
//                 this.reconnectAttempts = 0;
                
//                 // Send ping to keep connection alive
//                 setInterval(() => {
//                     if (this.socket.readyState === WebSocket.OPEN) {
//                         this.socket.send(JSON.stringify({type: 'ping'}));
//                     }
//                 }, 30000);
//             };
            
//             this.socket.onmessage = (e) => {
//                 const data = JSON.parse(e.data);
//                 this.handleNotification(data);
//             };
            
//             this.socket.onclose = (e) => {
//                 console.log('Notification WebSocket closed');
//                 this.reconnect();
//             };
            
//             this.socket.onerror = (e) => {
//                 console.error('WebSocket error:', e);
//             };
            
//         } catch (error) {
//             console.error('Failed to create WebSocket:', error);
//             this.reconnect();
//         }
//     }

//     reconnect() {
//         if (this.reconnectAttempts < this.maxReconnectAttempts) {
//             this.reconnectAttempts++;
//             console.log(`Attempting to reconnect... (${this.reconnectAttempts}/${this.maxReconnectAttempts})`);
            
//             setTimeout(() => {
//                 this.connectWebSocket();
//             }, this.reconnectDelay * this.reconnectAttempts);
//         }
//     }

//     handleNotification(data) {
//         switch (data.type) {
//             case 'restock_notification':
//                 this.showToast(data.notification, 'warning');
//                 this.addToNotificationsList(data.notification);
//                 break;
//             case 'restock_resolved':
//                 this.showToast(data.notification, 'success');
//                 this.updateNotificationStatus(data.notification.id, 'resolved');
//                 break;
//             case 'pong':
//                 // Connection alive confirmation
//                 break;
//         }
//     }

//     showToast(notification, type = 'info') {
//         const toast = document.createElement('div');
//         toast.className = `notification-toast notification-${type}`;
//         toast.style.cssText = `
//             background: ${type === 'warning' ? '#fff3cd' : type === 'success' ? '#d1f2eb' : '#e7f3ff'};
//             border: 1px solid ${type === 'warning' ? '#ffeaa7' : type === 'success' ? '#7dcea0' : '#74b9ff'};
//             border-radius: 8px;
//             padding: 15px;
//             margin-bottom: 10px;
//             box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
//             animation: slideIn 0.3s ease-out;
//             position: relative;
//             max-width: 100%;
//             word-wrap: break-word;
//         `;

//         const icon = type === 'warning' ? '⚠️' : type === 'success' ? '✅' : 'ℹ️';
        
//         toast.innerHTML = `
//             <div style="display: flex; align-items: flex-start; justify-content: space-between;">
//                 <div style="flex: 1;">
//                     <div style="font-weight: bold; margin-bottom: 5px;">
//                         ${icon} ${type === 'warning' ? 'Restock Alert' : type === 'success' ? 'Issue Resolved' : 'Notification'}
//                     </div>
//                     <div style="font-size: 14px; color: #666;">
//                         ${notification.message}
//                     </div>
//                     <div style="font-size: 12px; color: #999; margin-top: 5px;">
//                         ${new Date(notification.timestamp || Date.now()).toLocaleString()}
//                     </div>
//                 </div>
//                 <button onclick="this.parentElement.parentElement.remove()" 
//                         style="background: none; border: none; font-size: 18px; cursor: pointer; padding: 0; margin-left: 10px;">
//                     ×
//                 </button>
//             </div>
//         `;

//         const container = document.getElementById('notification-container');
//         container.appendChild(toast);

//         // Auto-remove after 10 seconds for warnings, 5 seconds for success
//         const autoRemoveTime = type === 'warning' ? 10000 : 5000;
//         setTimeout(() => {
//             if (toast.parentNode) {
//                 toast.style.animation = 'slideOut 0.3s ease-out';
//                 setTimeout(() => toast.remove(), 300);
//             }
//         }, autoRemoveTime);

//         // Add CSS animation if not already added
//         if (!document.getElementById('notification-styles')) {
//             const style = document.createElement('style');
//             style.id = 'notification-styles';
//             style.textContent = `
//                 @keyframes slideIn {
//                     from { transform: translateX(100%); opacity: 0; }
//                     to { transform: translateX(0); opacity: 1; }
//                 }
//                 @keyframes slideOut {
//                     from { transform: translateX(0); opacity: 1; }
//                     to { transform: translateX(100%); opacity: 0; }
//                 }
//                 .notification-container {
//                     pointer-events: none;
//                 }
//                 .notification-toast {
//                     pointer-events: all;
//                 }
//             `;
//             document.head.appendChild(style);
//         }
//     }

//     addToNotificationsList(notification) {
//         // Store notification for the notifications page
//         this.notifications.unshift(notification);
        
//         // Update notification badge if exists
//         const badge = document.querySelector('.notification-badge');
//         if (badge) {
//             const count = parseInt(badge.textContent) || 0;
//             badge.textContent = count + 1;
//             badge.style.display = 'inline';
//         }

//         // Trigger custom event for other parts of the app
//         window.dispatchEvent(new CustomEvent('newNotification', {
//             detail: notification
//         }));
//     }

//     updateNotificationStatus(notificationId, status) {
//         // Update local notifications array
//         const notification = this.notifications.find(n => n.id === notificationId);
//         if (notification) {
//             notification.status = status;
//         }

//         // Trigger custom event
//         window.dispatchEvent(new CustomEvent('notificationStatusChanged', {
//             detail: { id: notificationId, status: status }
//         }));
//     }

//     // Method to manually trigger a test notification
//     testNotification() {
//         this.showToast({
//             message: 'This is a test notification',
//             timestamp: new Date().toISOString()
//         }, 'info');
//     }

//     // Method to get all notifications
//     getNotifications() {
//         return this.notifications;
//     }

//     // Method to clear all notifications
//     clearNotifications() {
//         this.notifications = [];
//         const container = document.getElementById('notification-container');
//         if (container) {
//             container.innerHTML = '';
//         }
//     }
// }

// // Initialize notification manager when DOM is loaded
// document.addEventListener('DOMContentLoaded', function() {
//     window.notificationManager = new NotificationManager();
    
//     // Add global methods for easy access
//     window.showNotification = function(message, type = 'info') {
//         window.notificationManager.showToast({
//             message: message,
//             timestamp: new Date().toISOString()
//         }, type);
//     };
// });

// // Export for module systems
// if (typeof module !== 'undefined' && module.exports) {
//     module.exports = NotificationManager;
// }