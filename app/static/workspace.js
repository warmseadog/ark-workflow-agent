(() => {
  const sidebar=document.getElementById('workspace-sidebar');
  if(!sidebar)return;
  const toggle=document.querySelector('.workspace-toggle'),backdrop=document.querySelector('.workspace-backdrop');
  const isProduction=['/','/studio','/v1'].includes(location.pathname);
  const mobile=window.matchMedia('(max-width:800px)');
  function closeNav(){delete document.body.dataset.sidebarOpen;toggle.setAttribute('aria-expanded','false');toggle.setAttribute('aria-label','展开导航');backdrop.hidden=true;sidebar.inert=mobile.matches;}
  toggle.addEventListener('click',()=>{if(document.body.hasAttribute('data-sidebar-open'))closeNav();else{sidebar.inert=false;document.body.dataset.sidebarOpen='';toggle.setAttribute('aria-expanded','true');toggle.setAttribute('aria-label','收起导航');backdrop.hidden=false;}});
  mobile.addEventListener('change',closeNav);
  backdrop.addEventListener('click',closeNav);
  function sync(){
    const view=isProduction?(location.hash==='#tasks'?'tasks':'create'):({'/videos':'videos','/people':'people','/admin/settings':'settings','/admin/users':'users'}[location.pathname]||'account');
    document.body.dataset.workspaceView=view;
    sidebar.querySelectorAll('[data-workspace-page]').forEach(link=>{if(link.dataset.workspacePage===view)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');});
    if(isProduction){const title=document.querySelector('.production-brand h1');if(title)title.textContent=view==='tasks'?'任务记录':'制作流程';}
    closeNav();
  }
  sidebar.addEventListener('click',event=>{
    const link=event.target.closest('[data-workspace-page]');
    if(link&&isProduction&&['create','tasks'].includes(link.dataset.workspacePage)&&!event.ctrlKey&&!event.metaKey&&!event.shiftKey&&!event.altKey&&event.button===0){
      event.preventDefault();history.pushState(null,'',link.dataset.workspacePage==='tasks'?'#tasks':location.pathname);sync();window.scrollTo({top:0});
      if(link.dataset.workspacePage==='create')document.querySelectorAll('.production-runs video').forEach(video=>video.pause());
    }
  });
  document.addEventListener('click',event=>sidebar.querySelectorAll('.workspace-account[open]').forEach(menu=>{if(!menu.contains(event.target))menu.open=false;}));
  document.addEventListener('keydown',event=>{if(event.key==='Escape'){sidebar.querySelectorAll('.workspace-account[open]').forEach(menu=>menu.open=false);if(document.body.hasAttribute('data-sidebar-open')){closeNav();toggle.focus();}}});
  document.addEventListener('production-draft-selected',()=>{if(isProduction){history.pushState(null,'',location.pathname);sync();window.scrollTo({top:0});}});
  window.addEventListener('hashchange',sync);window.addEventListener('popstate',sync);sync();
})();
