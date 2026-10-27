import { createEditor, el } from './designer.js';

class LimitedAccessEditorPanel extends HTMLElement {
  constructor(){super();this.attachShadow({mode:'open'});}
  set hass(value){this._hass=value;if(this.isConnected&&!this.started){this.started=true;this.load();}}
  connectedCallback(){if(this._hass&&!this.started){this.started=true;this.load();}}
  async load(){
    this.shadowRoot.replaceChildren();
    const container=el('div');container.style.cssText='font:15px system-ui;padding:24px;max-width:1300px;margin:auto';this.shadowRoot.append(container);
    if(!this._hass.user?.is_owner){container.append(el('p','Only the Home Assistant owner can edit guest dashboards.'));return;}
    try{
      const dashboards=await this._hass.callWS({type:'limited_access_dashboard/editor'});
      container.append(el('h1','Guest dashboard editor'));
      const select=el('select');select.style.cssText='font:inherit;padding:12px;margin:0 0 20px';select.setAttribute('aria-label','Choose dashboard');
      const placeholder=el('option','Choose a dashboard…');placeholder.value='';select.append(placeholder);
      for(const dashboard of dashboards){const option=el('option',dashboard.title);option.value=dashboard.entry_id;select.append(option);}
      if(!dashboards.length)container.append(el('p','Add Limited Access Dashboard under Settings → Devices & services first.'));
      container.append(select);const editor=el('div');container.append(editor);
      select.onchange=async()=>{
        if(!select.value){editor.replaceChildren();return;}
        const entryId=select.value;
        try{
          const doc=await this._hass.callWS({type:'limited_access_dashboard/editor',entry_id:entryId});
          const link=el('a','Open guest dashboard ↗');link.href=doc.url;link.target='_blank';link.rel='noopener';
          const node=createEditor({document:doc,entities:doc.entities,owner:true,onSave:async document=>{
            await this._hass.callWS({type:'limited_access_dashboard/editor',entry_id:entryId,document});
          }});
          const auditButton=el('button','View recent activity');auditButton.type='button';
          const activity=el('div');activity.style.cssText='overflow:auto;max-width:100%';
          auditButton.onclick=async()=>{
            try{
              const rows=await this._hass.callWS({type:'limited_access_dashboard/audit',entry_id:entryId});
              activity.replaceChildren();
              if(!rows.length){activity.textContent='No recorded activity.';return;}
              const table=el('table');table.style.cssText='width:100%;table-layout:fixed;border-collapse:collapse;overflow-wrap:anywhere';
              const headers=el('tr');
              for(const title of ['Time','Username','Action','Result','Details','Address'])headers.append(el('th',title));
              const head=el('thead');head.append(headers);table.append(head);
              const body=el('tbody');table.append(body);
              for(const row of rows){
                const line=el('tr');line.dataset.auditRow='';
                for(const key of ['occurred_at','username','action_id','outcome','detail','remote_ip']){
                  const cell=el('td',String(row[key] ?? '').replace(/[\p{Cc}\p{Cf}\p{Zl}\p{Zp}]/gu,' '));
                  cell.style.cssText='padding:8px;border-bottom:1px solid #727782;vertical-align:top';line.append(cell);
                }
                body.append(line);
              }
              activity.append(table);
            }catch(error){activity.textContent=error.message || String(error);}
          };
          editor.replaceChildren(link,node,auditButton,activity);
          if(doc.design_warning)editor.prepend(el('p',doc.design_warning));
        }catch(error){editor.replaceChildren(el('p',error.message || String(error)));}
      };
      const requested=new URLSearchParams(location.search).get('dashboard');
      const matching=dashboards.find(d=>d.url?.split('/').pop()===requested);
      if(matching || dashboards.length===1){select.value=(matching || dashboards[0]).entry_id;await select.onchange();}
    }catch(error){container.append(el('p',error.message || String(error)));}
  }
}
customElements.define('lad-editor-panel',LimitedAccessEditorPanel);
