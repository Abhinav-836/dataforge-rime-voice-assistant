/**
 * In-app Notification / Toast System
 */

class NotificationManager {
  constructor() {
    this.container = null;
  }

  init() {
    if (this.container && document.getElementById('toast-container')) return;
    const existing = document.getElementById('toast-container');
    if (existing) {
      this.container = existing;
      return;
    }
    if (!document.body) return;
    this.container = document.createElement('div');
    this.container.id = 'toast-container';
    this.container.style.cssText = `
      position: fixed;
      top: 20px;
      right: 24px;
      display: flex;
      flex-direction: column;
      gap: 8px;
      z-index: 9999;
      pointer-events: none;
    `;
    document.body.appendChild(this.container);
  }

  show(message, type = 'info', duration = 3200) {
    if (!this.container) this.init();

    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    
    let borderColor = 'var(--border-subtle)';
    let bg = 'rgba(17, 24, 39, 0.9)';
    let color = 'var(--text-primary)';

    if (type === 'success') { borderColor = 'var(--accent-emerald)'; color = 'var(--accent-emerald)'; }
    if (type === 'error') { borderColor = 'var(--accent-rose)'; color = 'var(--accent-rose)'; }
    if (type === 'warning') { borderColor = 'var(--accent-amber)'; color = 'var(--accent-amber)'; }
    if (type === 'purple') { borderColor = 'var(--accent-purple)'; color = '#c4b5fd'; }

    toast.style.cssText = `
      background: ${bg};
      border: 1px solid ${borderColor};
      color: ${color};
      padding: 10px 16px;
      border-radius: var(--radius-sm);
      font-size: 12.5px;
      font-weight: 500;
      backdrop-filter: blur(12px);
      box-shadow: var(--shadow-md);
      pointer-events: auto;
      animation: fadeIn 0.2s ease-out;
      display: flex;
      align-items: center;
      gap: 10px;
      max-width: 320px;
    `;

    toast.innerHTML = message;
    this.container.appendChild(toast);

    setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transform = 'translateY(-6px)';
      toast.style.transition = 'all 0.25s ease';
      setTimeout(() => toast.remove(), 250);
    }, duration);
  }

  info(msg) { this.show(msg, 'info'); }
  success(msg) { this.show(msg, 'success'); }
  warning(msg) { this.show(msg, 'warning'); }
  error(msg) { this.show(msg, 'error'); }
  interruption(msg) { this.show(`⚡ <strong>Interruption:</strong> ${msg}`, 'warning'); }
}

export const notifications = new NotificationManager();
