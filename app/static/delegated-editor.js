/* Explicit scoped editor requests keep the administrator's real session identity. */
(() => {
  const params = new URLSearchParams(location.search);
  const userId = params.get('delegate_user'), draftId = params.get('draft');
  if (!/^[a-f0-9]{32}$/.test(userId || '') || !/^[a-f0-9]{32}$/.test(draftId || '')) return;
  window.delegatedEditor = {userId, draftId};
  const scoped = value => {
    const url = new URL(value, location.href);
    if (url.origin === location.origin && /^\/api\/(production|portrait|previews)(\/|$)/.test(url.pathname)) {
      url.pathname = '/api/admin/delegated/'+userId+url.pathname.slice(4);
    }
    return url;
  };
  const previous = window.fetch.bind(window);
  window.fetch = async (input, init) => {
    if (input instanceof Request) {
      const url = scoped(input.url);
      return previous(url.href === input.url ? input : new Request(url, input), init);
    }
    return previous(scoped(String(input)), init);
  };
  document.addEventListener('DOMContentLoaded', async () => {
    const banner = document.createElement('p'); banner.className='account-banner'; banner.id='delegated-editor-banner';
    const label = document.createElement('strong'); label.textContent='正在读取代操作用户…';
    const exit = document.createElement('a'); exit.textContent='退出代操作'; exit.href=location.pathname;
    exit.addEventListener('click', event => {
      const state=document.getElementById('draft-save-status')?.textContent || '';
      if (!state.includes('已保存') && !confirm('当前草稿尚未保存完成，确定退出代操作？')) event.preventDefault();
    });
    banner.append(label,document.createTextNode('　'),exit); document.body.prepend(banner);
    try {
      await window.accountReady;
      const response=await previous('/api/admin/users',{cache:'no-store'});
      const data=await response.json();
      const owner=data.items?.find(user=>user.id===userId);
      if (!response.ok || !owner) throw new Error('无权代操作该用户');
      label.textContent=`正在协助用户「${owner.username}」编辑草稿`;
      document.getElementById('studio-generate-submit').textContent='为该用户重新生成 →';
    } catch (error) { label.textContent=error.message; }
  });
})();
