window.SmartAzanHome = (function(){
  function $(s){ return document.querySelector(s); }
  async function getJSON(url){ const r = await fetch(url); return r.json(); }
  async function post(url, data){ const b=new URLSearchParams(); Object.entries(data||{}).forEach(([k,v])=>b.append(k,v)); return fetch(url,{method:'POST', body:b}); }
  function setDot(el, state){ if(!el) return; el.classList.remove('ok','warn','err'); el.classList.add(state); }

  function init(cfg){
    refreshAudio(cfg); refreshNet(cfg);
    setInterval(()=>refreshAudio(cfg), 9000);
    setInterval(()=>refreshNet(cfg), 6000);

    const scanBtn = $('#btn-scan-bt-home');
    const list = $('#bt-devices-home');
    if (scanBtn && list){
      scanBtn.addEventListener('click', async () => {
        scanBtn.disabled = true; list.innerHTML = '';
        try{
          await post(cfg.endpoints.btScan, {});
          const start = Date.now();
          async function poll(){
            const data = await getJSON(cfg.endpoints.btDevices);
            list.innerHTML = '';
            if (data.devices && data.devices.length){
              data.devices.forEach(d => {
                const div = document.createElement('div'); div.className='item';
                div.textContent = (d.name || 'Unknown') + ' — ' + d.mac;
                list.appendChild(div);
              });
              return;
            }
            if (Date.now() - start < 25000){ setTimeout(poll, 1200); }
          } poll();
        } finally { setTimeout(()=> scanBtn.disabled = false, 1500); }
      });
    }
  }

  async function refreshAudio(cfg){
    try{
      const a = await getJSON(cfg.endpoints.audioStatus);
      const out = a.output_device ? a.output_device.toUpperCase() : 'unknown';
      $('#pill-output').textContent = 'Output: ' + out;
      $('#pill-sink').textContent   = 'Sink: ' + (a.resolved_sink || a.default_sink || 'n/a');
    }catch(e){}
  }

  async function refreshNet(cfg){
    try{
      const n = await getJSON(cfg.endpoints.netDetail);

      // Ethernet
      setDot($('#eth-dot'), n.ethernet && n.ethernet.up ? 'ok' : 'err');
      $('#eth-sub').textContent = (n.ethernet && n.ethernet.up)
        ? `${n.ethernet.device || 'eth'} · ${n.ethernet.ip || 'no IP'}`
        : 'Not connected';

      // Wi-Fi
      const wifi = n.wifi || {};
      if (wifi.state === 'disabled'){
        setDot($('#wifi-dot'),'err');
        $('#wifi-sub').textContent = 'Disabled';
        show('#wifi-on-form'); hide('#wifi-off-form'); hide('#wifi-scan-form');
      } else if (wifi.unavailable){
        setDot($('#wifi-dot'),'warn');
        $('#wifi-sub').textContent = 'Unavailable (driver/offline/AP mode)';
        hide('#wifi-on-form'); show('#wifi-off-form'); hide('#wifi-scan-form');
      } else if (wifi.connected){
        setDot($('#wifi-dot'),'ok');
        const bits = [];
        if (wifi.ssid)   bits.push(`SSID “${wifi.ssid}”`);
        if (wifi.device) bits.push(wifi.device);
        if (wifi.ip)     bits.push(wifi.ip);
        if (wifi.signal) bits.push(wifi.signal + '%');
        $('#wifi-sub').textContent = bits.join(' · ');
        hide('#wifi-on-form'); show('#wifi-off-form'); show('#wifi-scan-form');
      } else {
        setDot($('#wifi-dot'),'warn');
        $('#wifi-sub').textContent = 'Enabled, not connected';
        hide('#wifi-on-form'); show('#wifi-off-form'); show('#wifi-scan-form');
      }

      // Hotspot
      const hs = n.hotspot || {};
      setDot($('#hotspot-dot'), hs.active ? 'ok':'err');
      $('#hotspot-sub').textContent = hs.active ? ('ON' + (hs.ip? ' ('+hs.ip+')':'')) : 'OFF';
      if (hs.active){ hide('#hs-on-form'); show('#hs-off-form'); }
      else          { show('#hs-on-form'); hide('#hs-off-form'); }

    }catch(e){}
  }

  function show(sel){ const el=$(sel); if(el) el.style.display=''; }
  function hide(sel){ const el=$(sel); if(el) el.style.display='none'; }

  return { init };
})();
