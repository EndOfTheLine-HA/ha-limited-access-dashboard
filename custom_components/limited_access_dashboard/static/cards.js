import { createEditor, el } from './designer.js';

export function restrictedHass(payload, sendService, report) {
  const blocked = async message => { report(message); throw Error(message); };
  return {
    states: payload.states || {}, entities: {}, devices: {}, areas: {}, floors: {},
    language: 'en', selectedLanguage: 'en', locale: {language:'en',number_format:'language',time_format:'24',first_weekday:'monday'},
    themes: {darkMode: document.documentElement.dataset.theme !== 'light', themes:{}, default_theme:'default'},
    config: {unit_system:{temperature:payload.temperature_unit || '°C',length:'km',mass:'kg',volume:'L'},time_zone:Intl.DateTimeFormat().resolvedOptions().timeZone},
    user: {name:'Guest',is_admin:false,is_owner:false}, panels:{}, services:{},
    localize: key => key.split('.').pop().replaceAll('_',' '),
    formatEntityState: state => `${state.state}${state.attributes.unit_of_measurement ? ` ${state.attributes.unit_of_measurement}` : ''}`,
    formatEntityName: state => state.attributes.friendly_name || state.entity_id,
    formatEntityAttributeValue: (state,key) => String(state.attributes[key] ?? ''),
    hassUrl: path => new URL(path,location.origin).href,
    callService: (domain, service, data={}, target={}) => sendService(domain,service,{...data,entity_id:target.entity_id ?? data.entity_id}),
    callWS: message => blocked(`Card API not supported yet: ${message.type}`),
    callApi: (_method,path) => blocked(`Card API not supported yet: ${path}`),
    fetchWithAuth: path => blocked(`Authenticated fetch is not available: ${path}`),
  };
}

// Native guest thermostat. All writes go through the existing restricted service API.
const thermostatViews = new WeakMap();
const thermostatIcons = {
  off:'M12 3v9 M7 6a8 8 0 1 0 10 0',
  heat:'M12 22c-5 0-8-3-8-7 0-4 3-6 5-9 0 4 2 5 3 5 3-3 3-6 2-9 5 4 6 9 6 13 0 4-3 7-8 7z M12 21c-2 0-3-2-3-4 0-2 2-4 3-5 0 2 3 3 3 5 0 2-1 4-3 4z',
  cool:'M12 2v20 M3.3 7l17.4 10 M3.3 17L20.7 7 M8 4l4 4 4-4 M8 20l4-4 4 4 M3 11l5-1-1-5 M21 13l-5 1 1 5 M3 13l5 1-1 5 M21 11l-5-1 1-5',
  heat_cool:'M12 2v5 M12 17v5 M2 12h5 M17 12h5 M5 5l3 3 M16 16l3 3 M5 19l3-3 M16 8l3-3 M12 7a5 5 0 1 0 0 10z',
  auto:'M5 8a8 8 0 0 1 14-2l2 3 M21 3v6h-6 M19 16a8 8 0 0 1-14 2l-2-3 M3 21v-6h6',
  dry:'M12 2c-2 4-7 9-7 13a7 7 0 0 0 14 0c0-4-5-9-7-13z',
  fan_only:'M12 10c-4-9 7-10 6-4 0 2-2 4-4 5 M14 12c9-4 10 7 4 6-2 0-4-2-5-4 M12 14c4 9-7 10-6 4 0-2 2-4 4-5 M10 12c-9 4-10-7-4-6 2 0 4 2 5 4 M14 12a2 2 0 1 1-4 0 2 2 0 0 1 4 0',
  thermometer:'M10 14V5a2 2 0 0 1 4 0v9a4 4 0 1 1-4 0z M12 10v8',
};
const svgNS='http://www.w3.org/2000/svg';
function thermostatSvg(tag,attrs={}) { const node=document.createElementNS(svgNS,tag); for(const [k,v] of Object.entries(attrs))node.setAttribute(k,String(v));return node; }
function thermostatIcon(mode) { const svg=thermostatSvg('svg',{viewBox:'0 0 24 24',width:24,height:24,'aria-hidden':'true',fill:'none',stroke:'currentColor','stroke-width':1.9,'stroke-linecap':'round','stroke-linejoin':'round'});svg.append(thermostatSvg('path',{d:thermostatIcons[mode] || thermostatIcons.auto}));return svg; }
const climateLabel = value => ({heat_cool:'Heat / Cool',fan_only:'Fan only',auto:'Auto',off:'Off',heat:'Heat',cool:'Cool',dry:'Dry'}[value] || String(value || '').replaceAll('_',' ').replace(/^./,c=>c.toUpperCase()));
const hvacStatus = value => ({heating:'Heating',cooling:'Cooling',idle:'Idle',off:'Off',drying:'Drying',fan:'Fan',defrosting:'Defrosting',preheating:'Preheating'}[value] || climateLabel(value));
const dialPoint = fraction => {const a=(135+270*fraction)*Math.PI/180;return [150+112*Math.cos(a),139+112*Math.sin(a)];};
function dialArc(start,end) {const a=dialPoint(start),b=dialPoint(end);return `M ${a[0]} ${a[1]} A 112 112 0 ${end-start>2/3?1:0} 1 ${b[0]} ${b[1]}`;}
const finiteTemp = value => value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value));
function mountThermostat(node,sendService,fetchState,applyState) {
  const title=node.querySelector('h2').textContent;
  const readOnly=node.dataset.climateReadOnly==='true';
  node.classList.add('thermostat-card');
  node.dataset.thermostatEnhanced='true';
  const heading=el('h2',title,'thermostat-title');
  const dial=el('div',undefined,'thermostat-dial');
  const svg=thermostatSvg('svg',{viewBox:'0 0 300 240','aria-hidden':'true'});
  const track=thermostatSvg('path',{d:dialArc(0,1),class:'thermostat-track'});
  const fill=thermostatSvg('path',{class:'thermostat-fill'});
  const handle=thermostatSvg('circle',{r:8,class:'thermostat-handle'});
  const second=thermostatSvg('circle',{r:8,class:'thermostat-handle thermostat-second'});
  const hit=thermostatSvg('path',{d:dialArc(0,1),class:'thermostat-hit'});
  svg.append(track,fill,handle,second,hit);
  const center=el('div',undefined,'thermostat-center');
  const status=el('div',undefined,'thermostat-status');
  const target=el('div',undefined,'thermostat-target');
  const current=el('div',undefined,'thermostat-current');
  const currentText=el('span');current.append(thermostatIcon('thermometer'),currentText);
  center.append(status,target,current);
  const adjust=el('div',undefined,'thermostat-adjust');
  const minus=el('button','−','thermostat-step');minus.type='button';minus.setAttribute('aria-label','Decrease target temperature');
  const plus=el('button','+','thermostat-step');plus.type='button';plus.setAttribute('aria-label','Increase target temperature');adjust.append(minus,plus);
  const range=el('div',undefined,'thermostat-range');
  const modeBar=el('div',undefined,'thermostat-modes');modeBar.setAttribute('role','group');modeBar.setAttribute('aria-label','HVAC mode');
  const fanRow=el('label',undefined,'thermostat-fan');fanRow.append(thermostatIcon('fan_only'));
  const fan=el('select');fan.setAttribute('aria-label','Fan mode');fanRow.append(fan);
  const feedback=el('p',undefined,'thermostat-feedback');feedback.setAttribute('role','status');
  dial.append(svg,center,adjust);node.replaceChildren(heading,dial,range,modeBar,fanRow,feedback);
  let card,attrs={},allowed=false,field='temperature',draft=null,busy=false,timer=null,drag=false,cooldown=0,modeKey='',fanKey='',rangeKey='';
  const values=()=>draft || card?.climate_controls?.values || {};
  const bounds=()=>card?.climate_controls;
  const fields=()=>bounds()?.fields || [];
  const step=()=>finiteTemp(attrs.target_temp_step)&&Number(attrs.target_temp_step)>0?Number(attrs.target_temp_step):(card?.temperature_unit==='°F'?1:.5);
  const canAdjust=()=>!readOnly&&allowed&&!busy&&fields().includes(field)&&finiteTemp(values()[field])&&Number(bounds().max)>Number(bounds().min)&&fields().every(key=>finiteTemp(values()[key]));
  const format=v=>finiteTemp(v)?Number(Number(v).toFixed(3)).toString():'—';
  const fraction=v=>Math.max(0,Math.min(1,(Number(v)-Number(bounds().min))/(Number(bounds().max)-Number(bounds().min))));
  const show=(element,visible)=>{element.hidden=!visible;};
  const labelMode=()=>card?.climate_operations?.hvac_mode;
  function draw(){
    if(!card)return;
    const ops=card.climate_operations || {};const mode=labelMode();const action=attrs.hvac_action;
    node.dataset.hvacMode=mode || 'off';node.dataset.hvacAction=action || '';
    const unit=card.temperature_unit || '';
    status.textContent=!card.available?'Unavailable':mode==='off'?'Off':hvacStatus(action || mode || card.state);
    target.replaceChildren();const display=el('span',format(values()[field]),'thermostat-number');const units=el('span',unit,'thermostat-unit');target.append(display,units);
    currentText.textContent=`${format(card.current_temperature)} ${unit}`;
    const valid=fields().includes(field)&&finiteTemp(values()[field])&&Number(bounds()?.max)>Number(bounds()?.min);
    const dual=fields().length===2&&fields().every(key=>finiteTemp(values()[key]));
    const pos=valid?fraction(values()[field]):0;
    show(fill,valid && (dual || pos>0));show(handle,valid);show(second,valid&&dual);
    if(valid){const p=dialPoint(pos);handle.setAttribute('cx',p[0]);handle.setAttribute('cy',p[1]);fill.setAttribute('d',dual?dialArc(fraction(values().target_temp_low),fraction(values().target_temp_high)):dialArc(0,pos));if(dual){const other=dialPoint(fraction(values()[fields().find(key=>key!==field)]));second.setAttribute('cx',other[0]);second.setAttribute('cy',other[1]);}}
    show(adjust,!readOnly&&fields().length>0);minus.disabled=!canAdjust()||Number(values()[field])<=Number(bounds()?.min);plus.disabled=!canAdjust()||Number(values()[field])>=Number(bounds()?.max);
    hit.classList.toggle('enabled',canAdjust());
    const rk=fields().join(',');if(rk!==rangeKey){rangeKey=rk;range.replaceChildren();if(fields().length===2)for(const key of fields()){const b=el('button');b.type='button';b.dataset.field=key;b.onclick=()=>{field=key;draw();};range.append(b);}}
    show(range,fields().length===2);for(const b of range.children){b.textContent=`${b.dataset.field==='target_temp_low'?'Heat':'Cool'} ${format(values()[b.dataset.field])} ${unit}`;b.setAttribute('aria-pressed',String(b.dataset.field===field));b.disabled=readOnly||!allowed||busy||!!draft;}
    const modes=ops.hvac_modes || [];const mk=JSON.stringify([modes,!!ops.turn_on,!!ops.turn_off]);
    if(mk!==modeKey){modeKey=mk;modeBar.replaceChildren();const choices=[...modes];if(!modes.includes('off')&&ops.turn_off)choices.unshift('@off');if(ops.turn_on&&!modes.some(m=>m!=='off'))choices.push('@on');for(const choice of choices){const m=choice==='@off'||choice==='@on'?'off':choice;const b=el('button');b.type='button';b.dataset.mode=choice;const name=choice==='@on'?'Turn on':choice==='@off'?'Turn off':climateLabel(m);b.title=name;b.setAttribute('aria-label',name);b.append(thermostatIcon(m));b.onclick=()=>send(choice==='@on'?'turn_on':choice==='@off'?'turn_off':'set_hvac_mode',choice.startsWith('@')?{}:{hvac_mode:choice});modeBar.append(b);}}
    show(modeBar,!readOnly&&modeBar.children.length>0);for(const b of modeBar.children){const selected=b.dataset.mode===mode||(b.dataset.mode==='@off'&&mode==='off');b.setAttribute('aria-pressed',String(selected));b.disabled=!allowed||busy||!!draft||selected;}
    const fans=ops.fan_modes || [];const fk=JSON.stringify(fans);if(fk!==fanKey){fanKey=fk;fan.replaceChildren(new Option('Select fan mode','',false,false));fan.options[0].disabled=true;for(const f of fans)fan.append(new Option(climateLabel(f),f));}
    fan.value=fans.includes(ops.fan_mode)?ops.fan_mode:'';fan.disabled=!allowed||busy||!!draft||!fans.length;show(fanRow,!readOnly&&fans.length>0);
  }
  async function send(service,data){
    if(readOnly||!allowed||busy)return;
    busy=true;feedback.textContent='';draw();
    try{
      const remaining=cooldown-Date.now();if(remaining>0)await new Promise(resolve=>setTimeout(resolve,remaining));
      if(!node.isConnected||!allowed)return;
      cooldown=Date.now()+2100;
      await sendService('climate',service,{...data,entity_id:node.dataset.entityId});
      feedback.textContent='Command sent';
      const next=await fetchState('/api/state');applyState(next);
    }catch(error){feedback.textContent='Unable to update thermostat. Try again in a moment.';}
    finally{busy=false;draft=null;draw();}
  }
  function change(value,commit=true){
    if(!canAdjust())return;
    const c=bounds(), s=step();let next=Math.min(Number(c.max),Math.max(Number(c.min),Number((Number(c.min)+Math.round((value-Number(c.min))/s)*s).toFixed(4))));
    if(field==='target_temp_low')next=Math.min(next,Number(values().target_temp_high));if(field==='target_temp_high')next=Math.max(next,Number(values().target_temp_low));
    draft={...values(),[field]:next};draw();clearTimeout(timer);if(commit)timer=setTimeout(commitDraft,500);
  }
  function commitDraft(){clearTimeout(timer);if(draft&&!busy){const data=Object.fromEntries(fields().map(key=>[key,Number(draft[key])]));if(fields().every(key=>finiteTemp(draft[key])))send('set_temperature',data);else{draft=null;draw();}}}
  minus.onclick=()=>change(Number(values()[field])-step());plus.onclick=()=>change(Number(values()[field])+step());fan.onchange=()=>send('set_fan_mode',{fan_mode:fan.value});
  function dragValue(event){const r=svg.getBoundingClientRect();const x=(event.clientX-r.left)*300/r.width-150;const y=(event.clientY-r.top)*240/r.height-139;const angle=(Math.atan2(y,x)*180/Math.PI+360-135)%360;const f=angle<=270?angle/270:angle<315?1:0;return Number(bounds().min)+f*(Number(bounds().max)-Number(bounds().min));}
  hit.addEventListener('pointerdown',event=>{if(!canAdjust()||event.button!==0)return;event.preventDefault();drag=true;hit.setPointerCapture(event.pointerId);change(dragValue(event),false);});
  hit.addEventListener('pointermove',event=>{if(drag&&canAdjust())change(dragValue(event),false);});
  hit.addEventListener('pointerup',()=>{if(drag){drag=false;commitDraft();}});
  hit.addEventListener('pointercancel',()=>{drag=false;clearTimeout(timer);draft=null;draw();});
  return (next,attributes,enabled)=>{
    const oldFields=fields().join(',');card=next;attrs=attributes || {};allowed=enabled&&card.available;
    if(!fields().includes(field))field=fields()[0] || 'temperature';
    if(!allowed||oldFields!==fields().join(',')){clearTimeout(timer);drag=false;draft=null;}
    draw();
  };
}

const grid = document.querySelector('[data-base-path]');
if (grid) start().catch(error => {
  const notice=el('p',`Dashboard layout could not load: ${error.message}`,'notice error');grid.before(notice);
});

async function start(){
  const base=grid.dataset.basePath;
  const sources = new Map([...grid.querySelectorAll('[data-entity-id]')].map(node=>[node.dataset.entityId,node.cloneNode(true)]));
  const get=async suffix=>{const response=await fetch(`${base}${suffix}`,{credentials:'same-origin'});if(!response.ok)throw Error(`Request failed (${response.status})`);return response.json();};
  const doc=await get('/api/design');let payload=await get('/api/state');let selected=0;let custom=[];
  const diagnostics=el('div');diagnostics.setAttribute('role','status');document.querySelector('#design-tools').append(diagnostics);
  const messages=[];
  const report=message=>{
    const text=String(message);if(messages.includes(text))return;
    messages.push(text);if(messages.length>30)messages.shift();
    diagnostics.replaceChildren(...messages.map(item=>el('p',item)));
    diagnostics.className='notice error';
  };
  if(doc.design_warning)report(doc.design_warning);
  for (const type of ['hass-more-info','hass-action']) grid.addEventListener(type,event=>{
    event.preventDefault();report(`Card event ${type} is not supported by this guest host.`);
  });
  const observeRender=node=>{
    if(node.updateComplete && typeof node.updateComplete.catch==='function') node.updateComplete.catch(error=>report(`Custom card render failed: ${error.message}`));
  };
  const sendService=async(domain,service,data)=>{
    const response=await fetch(`${base}/api/card-service`,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':doc.csrf_token},body:JSON.stringify({domain,service,data})});
    if(!response.ok){const message=await response.text();report(message);throw Error(message);}
    return response.json();
  };
  let hass=restrictedHass(payload,sendService,report);
  // Small host primitives for cards that only need ha-card/ha-icon and hass.
  if(!customElements.get('ha-card'))customElements.define('ha-card',class extends HTMLElement{
    connectedCallback(){this.style.cssText+='display:block;border-radius:var(--ha-card-border-radius,14px);background:var(--ha-card-background);padding:16px';}
  });
  if(!customElements.get('ha-icon'))customElements.define('ha-icon',class extends HTMLElement{
    connectedCallback(){this.textContent='◇';this.setAttribute('aria-label',this.getAttribute('icon') || 'Icon');}
  });
  const entityCard=(id, title, display='tile')=>{
    const source=sources.get(id);
    if(!source)return el('p',`Entity is no longer approved: ${id}`,'notice error');
    const node=source.cloneNode(true);
    if(title)node.querySelector('h2').textContent=title;
    if(display==='state'){node.dataset.climateReadOnly='true';for(const form of node.querySelectorAll('form, a.tile-action'))form.remove();}
    node.classList.toggle('compact-card',display==='compact');return node;
  };
  const build=config=>{
    if(!config || typeof config.type!=='string')throw Error('Card configuration needs a type');
    if(config.type.startsWith('custom:')){
      const tag=config.type.slice(7);
      if(!customElements.get(tag))throw Error(`Card element ${tag} is not registered. Check its resource URL.`);
      const node=document.createElement(tag);node.setConfig(structuredClone(config));node.hass=hass;observeRender(node);custom.push(node);return node;
    }
    if(['vertical-stack','horizontal-stack','grid'].includes(config.type)){
      const node=el('div',undefined,'card-stack');
      if(config.type!=='vertical-stack'){node.style.display='grid';node.style.gridTemplateColumns=`repeat(${Math.min(4,Math.max(1,Number(config.columns)||2))},minmax(0,1fr))`;}
      for(const child of config.cards || [])node.append(build(child));return node;
    }
    if(['entities','glance'].includes(config.type)){
      const node=el('div',undefined,'card-stack');if(config.title)node.append(el('h2',config.title));
      for(const item of config.entities || []){const row=typeof item==='string'?{entity:item}:item;node.append(entityCard(row.entity,row.name,'compact'));}return node;
    }
    if(['tile','button','entity','sensor','media-control','thermostat'].includes(config.type))return entityCard(config.entity,config.name);
    if(config.type==='heading')return el('h2',config.heading || config.title || '');
    if(config.type==='markdown'){const node=el('div',undefined,'ha-card');node.textContent=config.content || '';return node;}
    throw Error(`Built-in card type ${config.type} is not implemented in the guest host yet. Try a custom: card resource or an entity card.`);
  };
  window.loadCardHelpers=async()=>({createCardElement:build,createRowElement:config=>entityCard(config.entity,config.name,'compact')});
  // Load concurrently so unreachable modules cost at most one timeout window.
  await Promise.all((doc.resources || []).map(async resource=>{
    let timeout;
    try{await Promise.race([import(resource),new Promise((_,reject)=>{timeout=setTimeout(()=>reject(Error('Module load timed out')),10000);})]);}
    catch(error){report(`Resource ${resource}: ${error.message}`);}
    finally{clearTimeout(timeout);}
  }));
  const render=()=>{
    const d=doc.design;custom=[];
    document.documentElement.dataset.theme=d.theme;
    for(const [key,value] of Object.entries({'--ha-blue':d.accent,'--primary-color':d.accent,'--page-background':d.background,'--ha-card':d.card_background,'--ha-card-background':d.card_background,'--ha-card-active':d.card_background,'--dashboard-columns':String(d.columns)}))document.documentElement.style.setProperty(key,value);
    document.body.classList.toggle('density-compact',d.density==='compact');
    const tabs=document.querySelector('#dashboard-tabs');tabs.replaceChildren();tabs.setAttribute('role','tablist');
    d.views.forEach((view,i)=>{const button=el('button',view.title,'lad-tab');button.type='button';button.setAttribute('role','tab');button.setAttribute('aria-selected',String(i===selected));button.onclick=()=>{selected=i;render();};tabs.append(button);});
    grid.replaceChildren();grid.classList.add('section-layout');
    const view=d.views[selected] || d.views[0];
    if (!view.sections.some(section => section.cards.length)) {
      grid.append(el('p', 'This tab has no cards yet. Add cards or move existing cards here in the dashboard editor.', 'notice empty-tab'));
    }
    for(const section of view.sections){
      const wrapper=el('section',undefined,'dashboard-section');if(section.title)wrapper.append(el('h2',section.title,'section-heading'));
      const cards=el('div',undefined,'section-grid');wrapper.append(cards);
      for(const card of section.cards){
        const holder=el('div',undefined,'card-slot');if(card.wide)holder.classList.add('wide');
        holder.style.setProperty('--ha-blue',card.color || d.accent);holder.style.setProperty('--primary-color',card.color || d.accent);
        try{holder.append(card.type==='entity'?entityCard(card.entity,card.title,card.display):build(card.config));}
        catch(error){holder.append(el('p',error.message,'notice error'));}
        cards.append(holder);
      }
      grid.append(wrapper);
    }
    update(payload);
  };
  const update=next=>{
    if (payload.available === false && next.available) { location.reload(); return; }
    grid.hidden = !next.available;
    payload=next;hass=restrictedHass(payload,sendService,report);
    for(const card of custom){try{card.hass=hass;observeRender(card);}catch(error){report(error.message);}}
    for(const state of payload.cards){for(const node of grid.querySelectorAll(`[data-entity-id="${CSS.escape(state.entity_id)}"]`)){
      node.classList.toggle('active',state.active);node.classList.toggle('unavailable',!state.available);
      if(state.action==='climate'){
        if(!thermostatViews.has(node))thermostatViews.set(node,mountThermostat(node,sendService,get,update));
        thermostatViews.get(node)(state,payload.states?.[state.entity_id]?.attributes,payload.available);
        continue;
      }
      const label=node.querySelector('.state-value');if(label)label.textContent=`${state.state}${state.unit?` ${state.unit}`:''}`;
      const climate = state.climate_controls;
      const current = node.querySelector('.climate-current');
      if (current) current.textContent = `Current: ${state.current_temperature == null ? 'Unavailable' : `${state.current_temperature} ${state.temperature_unit}`}`;
      const target = node.querySelector('.climate-target');
      if (target) target.textContent = climate ? `Target: ${climate.fields.map(field => climate.values[field] ?? '—').join(' / ')} ${state.temperature_unit}` : '';
      const climateForm = node.querySelector('[data-climate-fields]');
      const climateMismatch = climateForm && (!climate || climateForm.dataset.climateFields !== climate.fields.join(','));
      if (climateForm && climate && !climateMismatch) {
        for (const input of climateForm.querySelectorAll('input[type=number]')) {
          input.min = climate.min; input.max = climate.max;
          if (!input.dataset.edited && document.activeElement !== input) input.value = climate.values[input.name] ?? '';
          input.oninput = () => { input.dataset.edited = 'true'; };
        }
      }
      if (climateForm) climateForm.title = climateMismatch ? 'Reload the dashboard after a thermostat mode change.' : '';
      const operations = state.climate_operations;
        for (const form of node.querySelectorAll('[data-climate-operation]')) {
          const service = form.dataset.climateOperation;
          const select = form.querySelector('select');
          if (select) {
            const field = select.name;
            const choices = operations?.[field + 's'] || [];
            const selected = select.value;
            const current = operations?.[field];
            const desired = select.dataset.edited && choices.includes(selected) ? selected : current;
            if (!choices.includes(selected)) delete select.dataset.edited;
            const values = [...select.options].filter(option => option.value !== '').map(option => option.value);
            if (JSON.stringify(values) !== JSON.stringify(choices) || (!choices.includes(desired) && !select.querySelector('option[value=""]'))) {
              select.replaceChildren();
              const placeholder = new Option('Select mode', '', false, false);
              placeholder.disabled = true;
              select.append(placeholder);
              for (const choice of choices) select.append(new Option(choice.replaceAll('_', ' '), choice));
            }
            select.value = choices.includes(desired) ? desired : '';
            select.onchange = () => { select.dataset.edited = 'true'; };
            form.hidden = !choices.length;
          } else {
            form.hidden = !operations?.[service];
          }
        }
        for(const control of node.querySelectorAll('button,.tile-action,input[type=number],select')){
        const unsupported=Boolean(control.dataset.mediaAction)&&!state.media_controls.some(item=>item.action===control.dataset.mediaAction);
        const disabled=!payload.available||!state.available||unsupported||Boolean(climateMismatch && climateForm.contains(control)) || Boolean(control.closest('[data-climate-operation]')?.hidden);control.toggleAttribute('disabled',disabled);control.classList.toggle('disabled',disabled);
      }
    }}
  };
  window.addEventListener('lad-state',event=>update(event.detail));
  if(doc.allow_guest_customization){
    const edit=el('button','Customize appearance','icon-button');edit.type='button';document.querySelector('#design-tools').prepend(edit);
    edit.onclick=()=>{
      if(document.querySelector('#guest-editor'))return;
      const editor=createEditor({document:doc,entities:doc.entities,onClose:()=>editor.remove(),onSave:async document=>{
        const response=await fetch(`${base}/api/design`,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':doc.csrf_token},body:JSON.stringify(document.design)});
        if(!response.ok)throw Error(await response.text());doc.design=document.design;selected=0;render();
      }});editor.id='guest-editor';grid.before(editor);
    };
  }
  render();
}
