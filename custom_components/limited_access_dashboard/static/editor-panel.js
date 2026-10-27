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
          editor.replaceChildren(link,node);
        }catch(error){editor.replaceChildren(el('p',error.message || String(error)));}
      };
      const requested=new URLSearchParams(location.search).get('dashboard');
      const matching=dashboards.find(d=>d.url?.split('/').pop()===requested);
      if(matching || dashboards.length===1){select.value=(matching || dashboards[0]).entry_id;await select.onchange();}
    }catch(error){container.append(el('p',error.message || String(error)));}
  }
}
customElements.define('lad-editor-panel',LimitedAccessEditorPanel);
