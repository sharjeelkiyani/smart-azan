(function(){
  const KEY='smartazan-theme';
  function apply(theme){
    document.documentElement.setAttribute('data-theme', theme);
    // Inject dark overrides if needed
    if (theme==='dark'){
      let el=document.getElementById('dark-vars');
      if(!el){
        el=document.createElement('style'); el.id='dark-vars';
        el.textContent=`
        :root[data-theme="dark"]{
          --bg:#0b1020; --card:#141a2a; --text:#f1f5f9; --muted:#94a3b8;
          --primary:#7aa2ff; --success:#22c55e; --shadow:0 6px 20px rgba(0,0,0,.4);
        }`;
        document.head.appendChild(el);
      }
    }
    try{ localStorage.setItem(KEY, theme); }catch(e){}
    const btn=document.getElementById('theme-toggle');
    if(btn) btn.textContent = (theme==='dark') ? '🌙 Dark' : '☀️ Light';
  }
  function init(){
    let saved=null;
    try{ saved=localStorage.getItem(KEY);}catch(e){}
    const prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    apply(saved || (prefersDark ? 'dark' : 'light'));
    const btn=document.getElementById('theme-toggle');
    if(btn){ btn.addEventListener('click', ()=>{
      const now=document.documentElement.getAttribute('data-theme')==='dark'?'light':'dark';
      apply(now);
    });}
  }
  document.addEventListener('DOMContentLoaded', init);
})();
