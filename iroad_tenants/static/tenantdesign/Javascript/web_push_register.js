const CONFIG_NODE_ID = 'tenant-web-push-config';
const TOKEN_STORAGE_KEY = 'tenant_web_push_token_v1';
const TOKEN_ENDPOINT = '/tenant/push/tokens/upsert/';
const SW_PATH = '/static/tenantdesign/Javascript/tenant-push-sw.js';

function readConfig() {
  const node = document.getElementById(CONFIG_NODE_ID);
  if (!node) return null;
  try {
    return JSON.parse(node.textContent || '{}');
  } catch (_err) {
    return null;
  }
}

function getCsrfToken() {
  const parts = (document.cookie || '').split(';').map((v) => v.trim());
  const match = parts.find((item) => item.startsWith('csrftoken='));
  return match ? decodeURIComponent(match.split('=').slice(1).join('=')) : '';
}

async function persistToken(token) {
  const csrf = getCsrfToken();
  const response = await fetch(TOKEN_ENDPOINT, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-CSRFToken': csrf,
    },
    credentials: 'same-origin',
    body: JSON.stringify({ device_token: token }),
  });
  if (!response.ok) {
    throw new Error(`token_upsert_failed_${response.status}`);
  }
  return response.json();
}

function hasFirebaseConfig(cfg) {
  return Boolean(
    cfg &&
      cfg.apiKey &&
      cfg.projectId &&
      cfg.messagingSenderId &&
      cfg.appId &&
      cfg.vapidKey
  );
}

async function registerTenantWebPush() {
  const cfg = readConfig();
  if (!hasFirebaseConfig(cfg)) {
    return;
  }
  if (!('serviceWorker' in navigator) || !('Notification' in window)) {
    return;
  }
  if (Notification.permission === 'denied') {
    return;
  }

  const { initializeApp } = await import('https://www.gstatic.com/firebasejs/10.12.2/firebase-app.js');
  const { getMessaging, getToken } = await import('https://www.gstatic.com/firebasejs/10.12.2/firebase-messaging.js');

  const swRegistration = await navigator.serviceWorker.register(SW_PATH);
  const app = initializeApp({
    apiKey: cfg.apiKey,
    authDomain: cfg.authDomain || undefined,
    projectId: cfg.projectId,
    storageBucket: cfg.storageBucket || undefined,
    messagingSenderId: cfg.messagingSenderId,
    appId: cfg.appId,
  });

  if (Notification.permission !== 'granted') {
    const permission = await Notification.requestPermission();
    if (permission !== 'granted') {
      return;
    }
  }

  const messaging = getMessaging(app);
  const token = await getToken(messaging, {
    vapidKey: cfg.vapidKey,
    serviceWorkerRegistration: swRegistration,
  });
  if (!token) {
    return;
  }

  const previousToken = localStorage.getItem(TOKEN_STORAGE_KEY);
  if (previousToken === token) {
    return;
  }
  await persistToken(token);
  localStorage.setItem(TOKEN_STORAGE_KEY, token);
}

registerTenantWebPush().catch((_err) => {
  // Tenant workspace should stay usable even if push registration fails.
});
