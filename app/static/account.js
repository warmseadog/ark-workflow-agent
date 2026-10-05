/* Load synchronously before application scripts. Session secrets stay in memory. */
(() => {
  'use strict';
  const originalFetch = window.fetch.bind(window);
  const loginPage = window.location.pathname === '/login';
  const publicHelp = window.location.pathname === '/help';
  const cacheNames = ['production-current-draft-v1', 'production-pending-submit-v1'];
  const identityEventKey = 'ark-account-identity';
  let identity = null;
  let invalidated = false;
  window.currentAccount = null;

  function authError(message) {
    const error = new Error(message);
    error.name = 'AccountSessionError';
    return error;
  }
  function redirect(path) {
    invalidated = true;
    if (window.location.pathname !== path) window.location.replace(path);
  }
  function clearDraftCaches(user = window.currentAccount) {
    try {
      for (const key of cacheNames) {
        localStorage.removeItem(key);
        if (user?.id != null) localStorage.removeItem(`${key}:${user.id}`);
      }
    } catch (_) { /* Storage can be unavailable in private browsing. */ }
  }
  function logoutKey() { return `ark-account-logout:${window.currentAccount?.id}`; }
  function reloadIdentity() {
    invalidated = true;
    clearDraftCaches();
    window.location.reload();
  }
  function finishSession() {
    clearDraftCaches();
    if (window.currentAccount?.id != null) {
      try { localStorage.setItem(logoutKey(), `${Date.now()}:${Math.random()}`); } catch (_) { /* Best effort cross-tab notification. */ }
    }
    redirect('/login');
  }
  async function inspectResponse(response, isLogin = false) {
    if (response.status === 401) {
      if (isLogin && loginPage) throw authError('用户名或密码不正确，请重试。');
      clearDraftCaches();
      redirect('/login');
      throw authError('登录已失效，请重新登录。');
    }
    if (!response.ok) {
      // Read a clone so each caller retains its normal error handling and body.
      const data = await response.clone().json().catch(() => ({}));
      const safe = value => typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$/.test(value) ? value : '';
      const id = safe(data.request_id) || safe(response.headers.get('X-Request-ID'));
      if (id) {
        if (window.supportUI) window.supportUI.reportProblem(id);
        else window.pendingSupportProblems = [id];
      }
    }
    return response;
  }

  // Install the wrapper immediately; even a request from the next script waits.
  window.fetch = async (input, init) => {
    const request = input instanceof Request ? input : null;
    const url = new URL(request ? request.url : String(input), window.location.href);
    if (url.origin !== window.location.origin || !url.pathname.startsWith('/api/')) {
      return originalFetch(input, init);
    }
    const account = await window.accountReady;
    if (invalidated) throw authError('账号状态已变化，请重新登录。');
    const method = String(init?.method || request?.method || 'GET').toUpperCase();
    let options = init;
    // Redirected media reads use the existing host-only session cookie on the
    // dedicated HTTPS port. Ordinary API credentials keep their caller policy.
    const mediaRead = /^\/api\/(?:production\/assets\/[a-f0-9]{32}\/file|production\/runs\/(?:legacy-)?[a-f0-9]{32}\/(?:download|defaced|playback\/(?:original|smooth))|previews\/[a-f0-9]{32}\/file|portrait\/photos\/[a-f0-9]{32}\/file)$/.test(url.pathname);
    if (mediaRead && ['GET','HEAD'].includes(method)) options = {...init, credentials:'include'};
    if (!['GET', 'HEAD', 'OPTIONS'].includes(method) && account.auth_enabled && account.csrf_token) {
      const headers = new Headers(init?.headers === undefined ? request?.headers : init.headers);
      headers.set('X-CSRF-Token', account.csrf_token);
      options = {...init, headers};
    }
    const response = await originalFetch(input, options);
    if (invalidated) throw authError('账号状态已变化，请重新登录。');
    const responseAccount = response.headers.get('X-Account-ID');
    if (window.currentAccount && responseAccount !== null && responseAccount !== String(window.currentAccount.id)) {
      reloadIdentity();
      throw authError('当前账号已切换，正在重新加载工作台。');
    }
    return inspectResponse(response, url.pathname === '/api/auth/login');
  };

  window.accountReady = (async () => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 8000);
    try {
    const response = await originalFetch('/api/auth/me', {cache:'no-store', credentials:'same-origin', signal:controller.signal});
    if (response.status === 401 && (loginPage || publicHelp)) {
      identity = {auth_enabled:true, user:null, csrf_token:''};
    } else {
      await inspectResponse(response);
      if (!response.ok) throw new Error('暂时无法确认账号状态，请刷新页面重试。');
      identity = await response.json();
    }
    window.currentAccount = identity.user || null;
    if (identity.auth_enabled && !identity.user && !loginPage && !publicHelp) {
      redirect('/login');
      throw authError('请先登录工作台。');
    }
    return identity;
    } catch (error) {
      if (controller.signal.aborted) throw new Error('网络较慢，暂时无法确认登录状态，请点击重试。');
      if (error instanceof TypeError) throw new Error('无法连接工作台，请检查网络后重试。');
      throw error;
    } finally { clearTimeout(timeout); }
  })();
  // Handle bootstrap rejection even on pages whose application scripts do not await it.
  window.accountReady.catch(() => {});

  window.accountUI = {
    clearDraftCaches,
    finishSession,
    announceLogin(user) {
      if (user?.id == null) return;
      try { localStorage.setItem(identityEventKey, JSON.stringify({user_id:String(user.id), nonce:`${Date.now()}:${Math.random()}`})); } catch (_) { /* Response identity headers still protect existing tabs. */ }
    },
    async readJSON(response) {
      const data = await response.json().catch(() => ({}));
      if (!response.ok) {
        const detail = data.detail;
        throw new Error(typeof detail === 'string' ? detail : detail?.message || data.message || `请求未完成（${response.status}），请重试。`);
      }
      return data;
    },
  };
  window.addEventListener('storage', event => {
    if (event.key === identityEventKey && event.newValue) {
      // Even a new session for the same user has a different CSRF token.
      reloadIdentity();
      return;
    }
    if (window.currentAccount?.id != null && event.key === logoutKey() && event.newValue) {
      clearDraftCaches();
      redirect('/login');
    }
  });
  // A restored back/forward-cache page must not retain the previous account's UI.
  window.addEventListener('pageshow', event => { if (event.persisted) window.location.reload(); });

  function showError(message, retry = false) {
    if (!document.querySelector('[data-account-error]')) {
      const node = document.createElement('p');
      node.dataset.accountError = ''; node.className = 'account-banner'; node.setAttribute('role','alert');
      document.body.prepend(node);
    }
    document.querySelectorAll('[data-account-error]').forEach(node => {
      node.textContent = message;
      node.hidden = false;
      if (retry) {
        const button = document.createElement('button');
        button.type = 'button'; button.dataset.accountRetry = ''; button.textContent = '重试连接';
        button.addEventListener('click', () => window.location.reload());
        node.append(document.createTextNode(' '), button);
      }
    });
  }
  async function mount() {
    try {
      const account = await window.accountReady;
      const signedIn = account.auth_enabled && Boolean(account.user);
      const admin = !account.auth_enabled || account.user?.is_admin === true || account.user?.role === 'admin';
      document.documentElement.dataset.accountRole = admin ? 'admin' : 'user';
      // These links live in the shared production panel, outside this module's templates.
      document.querySelectorAll('.standalone-production a[href="/admin/settings#people"]').forEach(node => { node.href = '/people'; });
      document.querySelectorAll('.standalone-production a[href^="/admin/"]').forEach(node => { node.hidden = !admin || (node.hasAttribute('data-auth-only') && !signedIn); });
      const linkSettings = document.querySelector('.standalone-production #link-settings');
      if (linkSettings) { linkSettings.hidden = !admin; linkSettings.dataset.adminOnly = ''; }
      document.querySelectorAll('[data-auth-only], [data-admin-only]').forEach(node => {
        node.hidden = (node.hasAttribute('data-auth-only') && !signedIn) || (node.hasAttribute('data-admin-only') && !admin);
      });
      document.querySelectorAll('[data-account-name]').forEach(node => { node.textContent = account.user?.username || ''; });
      document.querySelectorAll('[data-account-logout]').forEach(button => {
        button.addEventListener('click', async () => {
          button.disabled = true;
          try {
            await window.accountUI.readJSON(await fetch('/api/auth/logout', {method:'POST'}));
            finishSession();
          } catch (error) { showError(error.message); button.disabled = false; }
        });
      });
    } catch (error) { if (!invalidated) showError(error.message, true); }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', mount, {once:true});
  else mount();
})();
