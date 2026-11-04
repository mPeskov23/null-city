// PWA lifecycle, Service Worker registration, and offline readiness
'use strict';

(function() {
  if (typeof window === 'undefined') return;

  // Detect standalone display mode
  const isStandalone = window.matchMedia('(display-mode: standalone)').matches || window.navigator.standalone === true;
  if (isStandalone) {
    document.documentElement.setAttribute('data-pwa-standalone', 'true');
  }

  // Register Service Worker
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('/sw.js', { scope: '/' })
        .then(reg => {
          // Check for service worker updates periodically
          reg.onupdatefound = () => {
            const installing = reg.installing;
            if (installing) {
              installing.onstatechange = () => {
                if (installing.state === 'installed' && navigator.serviceWorker.controller) {
                  console.info('[PWA] Новая версия доступна. Перезагрузите страницу для обновления.');
                }
              };
            }
          };
        })
        .catch(err => {
          console.warn('[PWA] Регистрация Service Worker не удалась:', err.message);
        });
    });
  }

  // Handle install prompt (A2HS - Add to Home Screen)
  let deferredPrompt = null;
  window.addEventListener('beforeinstallprompt', e => {
    e.preventDefault();
    deferredPrompt = e;
    window.pwaInstallPrompt = deferredPrompt;
    document.dispatchEvent(new CustomEvent('pwa-installable'));
  });

  window.installPWA = async function() {
    if (!deferredPrompt) return false;
    deferredPrompt.prompt();
    const { outcome } = await deferredPrompt.userChoice;
    deferredPrompt = null;
    window.pwaInstallPrompt = null;
    return outcome === 'accepted';
  };

  // Offline / Online status notification
  function updateOnlineStatus() {
    const isOnline = navigator.onLine;
    let pill = document.getElementById('pwa-offline-indicator');
    if (!isOnline) {
      if (!pill) {
        pill = document.createElement('div');
        pill.id = 'pwa-offline-indicator';
        pill.setAttribute('role', 'status');
        pill.setAttribute('aria-live', 'polite');
        pill.textContent = '● АВТОНОМНЫЙ РЕЖИМ (PWA)';
        pill.style.cssText = 'position:fixed;bottom:12px;left:12px;z-index:9999;background:rgba(14,23,36,0.92);color:#75f5d1;border:1px solid #75f5d1;padding:6px 12px;border-radius:4px;font-size:12px;letter-spacing:0.05em;box-shadow:0 2px 10px rgba(0,0,0,0.5);pointer-events:none;';
        document.body.appendChild(pill);
      }
      pill.hidden = false;
    } else if (pill) {
      pill.hidden = true;
    }
  }

  window.addEventListener('online', updateOnlineStatus);
  window.addEventListener('offline', updateOnlineStatus);
  window.addEventListener('DOMContentLoaded', updateOnlineStatus);
})();
