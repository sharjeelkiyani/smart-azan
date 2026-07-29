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
          --bg:#07130f; --card:#0f231b; --text:#eef7f1; --muted:#9db8a9;
          --primary:#2fbf82; --primary-dark:#0b6e4f; --gold:#e0bf5a;
          --success:#34d399; --danger:#f87171; --border:#1c3a2c;
          --shadow:0 6px 20px rgba(0,0,0,.5);
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
