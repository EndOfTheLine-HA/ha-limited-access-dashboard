import { createEditor, el } from './designer.js';

export function restrictedHass(payload, sendService, report) {
  const blocked = async message => { report(message); throw Error(message); };
  return {
    states: payload.states || {}, entities: {}, devices: {}, areas: {}, floors: {},
    language: 'en', selectedLanguage: 'en', locale: {language:'en',number_format:'language',time_format:'24',first_weekday:'monday'},
    themes: {darkMode: document.documentElement.dataset.theme !== 'light', themes:{}, default_theme:'default'},
    config: {unit_system:{temperature:'°C',length:'km',mass:'kg',volume:'L'},time_zone:Intl.DateTimeFormat().resolvedOptions().timeZone},
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
  const report=message=>{diagnostics.textContent=String(message);diagnostics.className='notice error';};
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
    if(display==='state')for(const form of node.querySelectorAll('form, a.tile-action'))form.remove();
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
    if(['tile','button','entity','sensor','media-control'].includes(config.type))return entityCard(config.entity,config.name);
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
      const label=node.querySelector('.state-value');if(label)label.textContent=`${state.state}${state.unit?` ${state.unit}`:''}`;
      for(const control of node.querySelectorAll('button,.tile-action,input[type=number]')){
        const unsupported=Boolean(control.dataset.mediaAction)&&!state.media_controls.some(item=>item.action===control.dataset.mediaAction);
        const disabled=!payload.available||!state.available||unsupported;control.toggleAttribute('disabled',disabled);control.classList.toggle('disabled',disabled);
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
