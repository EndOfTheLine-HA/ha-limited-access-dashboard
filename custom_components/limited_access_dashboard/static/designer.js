export const el = (tag, text, className) => {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
};

export const designerCSS = `
  .lad-editor {font: 15px system-ui; color:var(--primary-text-color,#e1e1e6); background:var(--card-background-color,#1c1f2a); padding:24px; border-radius:16px;}
  .lad-editor * {box-sizing:border-box} .lad-editor h2 {font-size:22px;margin:0 0 18px}
  .lad-editor h3 {margin:20px 0 12px} .lad-editor p {line-height:1.5}
  .lad-toolbar,.lad-fields {display:flex;gap:12px;flex-wrap:wrap;align-items:end;margin:12px 0}
  .lad-editor label {display:grid;gap:7px;font-size:14px;flex:1;min-width:150px}
  .lad-editor input,.lad-editor select,.lad-editor textarea {font:inherit;color:inherit;background:var(--secondary-background-color,#12151d);border:1px solid #727782;padding:10px;border-radius:8px;max-width:100%}
  .lad-editor input[type=checkbox] {width:20px;height:20px} .lad-editor input[type=color] {width:100%;height:42px;padding:4px}
  .lad-editor button,.lad-tab {font:inherit;padding:9px 14px;background:#24536b;color:#fff;border:1px solid #53788c;border-radius:9px;cursor:pointer}
  .lad-editor button:disabled {opacity:.4;cursor:default} .lad-editor textarea {min-height:150px;width:100%;font:13px monospace}
  .lad-list {display:grid;gap:10px}.lad-row {padding:14px;border:1px solid #727782;border-radius:12px}
  .lad-row summary {cursor:pointer;font-weight:600}.lad-status {min-height:24px;white-space:pre-wrap}
  .lad-preview {padding:16px;border-radius:12px;margin-top:16px}.lad-preview-grid {display:grid;gap:12px;align-items:start}
  .lad-preview-card {padding:20px;border:1px solid #727782;border-radius:12px;min-width:0;overflow-wrap:anywhere}
  .lad-editor details {margin:10px 0}.lad-editor .lad-danger {background:#652f37}
  @media(max-width:650px){.lad-editor{padding:14px}.lad-preview-grid{grid-template-columns:1fr!important}}
`;

export function createEditor({ document: initial, entities, owner = false, onSave, onClose }) {
  const doc = structuredClone(initial);
  let viewIndex = 0, sectionIndex = 0;
  const root = el('div', undefined, 'lad-editor');
  const style = el('style'); style.textContent = designerCSS; root.append(style);
  const entityName = id => entities.find(item => item.entity_id === id)?.name || id;
  const button = (label, action, danger = false) => {
    const node = el('button', label, danger ? 'lad-danger' : '');
    node.type = 'button'; node.onclick = action; return node;
  };
  const field = (container, label, value, change, options) => {
    const wrapper = el('label', label);
    let input;
    if (Array.isArray(options)) {
      input = el('select');
      for (const option of options) {
        const [value, title] = Array.isArray(option) ? option : [option, option];
        const item = el('option', title); item.value = value; input.append(item);
      }
      input.value = value;
    } else {
      input = el('input'); input.type = options || 'text';
      if (input.type === 'checkbox') input.checked = value; else input.value = value;
    }
    input.setAttribute('aria-label', label);
    input.onchange = () => { change(input.type === 'checkbox' ? input.checked : input.value); preview(); };
    wrapper.append(input); container.append(wrapper); return input;
  };
  let previewNode;
  const preview = () => {
    if (!previewNode) return;
    previewNode.replaceChildren();
    const d = doc.design;
    previewNode.style.background = d.background;
    previewNode.style.color = d.theme === 'light' ? '#20242a' : '#e1e1e6';
    previewNode.append(el('h3', `${d.views[viewIndex].title} · preview`));
    if (!d.views[viewIndex].sections.some(section => section.cards.length)) {
      previewNode.append(el('p', 'This tab has no cards yet. Add an approved entity card below the card list, or move a card here from another tab.'));
    }
    for (const section of d.views[viewIndex].sections) {
      if (section.title) previewNode.append(el('h3', section.title));
      const grid = el('div', undefined, 'lad-preview-grid');
      grid.style.gridTemplateColumns = `repeat(${d.columns}, minmax(0,1fr))`;
      for (const card of section.cards) {
        const tile = el('div', undefined, 'lad-preview-card');
        tile.style.background = d.card_background;
        tile.style.borderTop = `4px solid ${card.color || d.accent}`;
        if (card.wide) tile.style.gridColumn = '1 / -1';
        tile.append(el('strong', card.title || (card.type === 'entity' ? entityName(card.entity) : card.config.type)));
        tile.append(el('p', card.type === 'entity' ? entities.find(e => e.entity_id === card.entity)?.state || 'State' : 'Experimental card · open the guest page to test'));
        grid.append(tile);
      }
      previewNode.append(grid);
    }
  };
  const render = () => {
    root.replaceChildren(style);
    root.append(el('h2', owner ? 'Edit guest dashboard' : 'Customize appearance'));
    root.append(el('p', owner ? 'Arrange approved entities into tabs and sections. Entity access and schedules stay in the integration configuration.' : 'Change the appearance of this guest dashboard. The homeowner controls which entities and actions are available.'));
    const d = doc.design;
    if (owner) {
      const placed = new Set();
      const scan = value => {
        if (typeof value === 'string') placed.add(value);
        else if (Array.isArray(value)) value.forEach(scan);
        else if (value && typeof value === 'object') Object.values(value).forEach(scan);
      };
      for (const view of d.views) for (const section of view.sections) for (const card of section.cards) scan(card.type === 'entity' ? card.entity : card.config);
      const unplaced = entities.filter(entity => !placed.has(entity.entity_id));
      if (unplaced.length) {
        const notice = el('div', undefined, 'lad-row');
        notice.append(el('h3', 'Approved entities not placed'));
        notice.append(el('p', 'These entities are approved but are not displayed. Add a card to the selected section.'));
        for (const entity of unplaced) notice.append(button(`Add ${entity.name}`, () => {
          d.views[viewIndex].sections[sectionIndex].cards.push({type:'entity',entity:entity.entity_id});render();
        }));
        root.append(notice);
      }
    }
    const fields = el('div', undefined, 'lad-fields'); root.append(fields);
    field(fields, 'Theme', d.theme, v => {
      d.theme = v; d.background = v === 'light' ? '#f3f5f7' : '#11131a';
      d.card_background = v === 'light' ? '#ffffff' : '#1c1f2a'; render();
    }, ['dark', 'light']);
    field(fields, 'Columns', String(d.columns), v => { d.columns = Number(v); }, ['1','2','3','4']);
    field(fields, 'Density', d.density, v => { d.density = v; }, ['comfortable','compact']);
    field(fields, 'Accent color', d.accent, v => { d.accent = v; }, 'color');
    field(fields, 'Page color', d.background, v => { d.background = v; }, 'color');
    field(fields, 'Card color', d.card_background, v => { d.card_background = v; }, 'color');
    if (owner) {
      const permission = el('div', undefined, 'lad-fields'); root.append(permission);
      field(permission, 'Allow guest customization', doc.allow_guest_customization, v => { doc.allow_guest_customization = v; }, 'checkbox');
    }
    const tabs = el('div', undefined, 'lad-toolbar'); root.append(tabs);
    field(tabs, 'Tab', String(viewIndex), v => { viewIndex = Number(v); sectionIndex = 0; render(); }, d.views.map((v,i) => [String(i),v.title]));
    tabs.append(button('+ Tab', () => { d.views.push({title:'New tab',sections:[{title:'',cards:[]}]});viewIndex=d.views.length-1;sectionIndex=0;render(); }));
    if(d.views.length > 1) tabs.append(button('Delete tab', () => { if(confirm('Delete this tab and its card placements?')){d.views.splice(viewIndex,1);viewIndex=0;sectionIndex=0;render();}}, true));
    const view = d.views[viewIndex];
    field(tabs, 'Tab title', view.title, v => { view.title = v; render(); });
    const sections = el('div', undefined, 'lad-toolbar'); root.append(sections);
    field(sections, 'Section', String(sectionIndex), v => {sectionIndex=Number(v);render();}, view.sections.map((s,i) => [String(i), s.title || `Section ${i+1}`]));
    sections.append(button('+ Section', () => {view.sections.push({title:'New section',cards:[]});sectionIndex=view.sections.length-1;render();}));
    const section = view.sections[sectionIndex];
    field(sections, 'Section heading', section.title, v => {section.title=v;render();});
    if(view.sections.length > 1) sections.append(button('Delete section',()=>{if(confirm('Delete this section and its card placements?')){view.sections.splice(sectionIndex,1);sectionIndex=0;render();}},true));
    const list = el('div', undefined, 'lad-list'); root.append(list);
    section.cards.forEach((card, i) => {
      const row=el('details',undefined,'lad-row'); list.append(row);
      row.append(el('summary',card.title || (card.type==='entity'?entityName(card.entity):card.config.type)));
      const f=el('div',undefined,'lad-fields');row.append(f);
      field(f,'Display name',card.title || '',v=>{card.title=v;row.querySelector('summary').textContent=v || entityName(card.entity);});
      field(f,'Accent',card.color || d.accent,v=>{card.color=v;},'color');
      field(f,'Full width',card.wide || false,v=>{card.wide=v;},'checkbox');
      if(card.type==='entity') {
        field(f,'Entity',card.entity,v=>{card.entity=v;},entities.map(e=>[e.entity_id,e.name]));
        field(f,'Style',card.display || 'tile',v=>{card.display=v;},['tile','compact','state']);
      } else if(owner) {
        const label=el('label','Lovelace configuration (JSON)');const json=el('textarea');json.value=JSON.stringify(card.config,null,2);
        json.onchange=()=>{try{card.config=JSON.parse(json.value);json.setCustomValidity('');preview();}catch{json.setCustomValidity('Enter valid JSON');json.reportValidity();}};
        label.append(json);row.append(label);
      }
      const actions=el('div',undefined,'lad-toolbar');row.append(actions);
      for(const [label,delta] of [['Move up',-1],['Move down',1]]) {
        const b=button(label,()=>{[section.cards[i],section.cards[i+delta]]=[section.cards[i+delta],section.cards[i]];render();});
        b.disabled=i+delta<0 || i+delta>=section.cards.length;actions.append(b);
      }
      field(actions,'Move to section','',value=>{if(!value)return;const [vi,si]=value.split(':').map(Number);section.cards.splice(i,1);d.views[vi].sections[si].cards.push(card);render();},
        [['','Choose…'],...d.views.flatMap((v,vi)=>v.sections.map((s,si)=>[`${vi}:${si}`,`${v.title} / ${s.title || `Section ${si+1}`}`]))]);
      actions.append(button('Remove card',()=>{section.cards.splice(i,1);render();},true));
    });
    const add=el('div',undefined,'lad-toolbar');root.append(add);
    let newEntity=entities[0]?.entity_id;
    field(add,'Add approved entity',newEntity,v=>{newEntity=v;},entities.map(e=>[e.entity_id,e.name]));
    add.append(button('+ Entity card',()=>{if(newEntity){section.cards.push({type:'entity',entity:newEntity});render();}}));
    if(owner) add.append(button('+ Lovelace / HACS card',()=>{section.cards.push({type:'lovelace',config:{type:'custom:state-card-example',entity:newEntity}});render();}));
    if(owner) {
      const resources=el('details');resources.append(el('summary','Experimental Lovelace / HACS resources'));
      resources.append(el('p','Add installed /hacsfiles/…js or /local/…js module URLs, one per line. Cards run on the guest page with a restricted hass adapter. Resource code is trusted; only add modules you trust.'));
      const input=el('textarea');input.setAttribute('aria-label','Card module URLs');input.value=(doc.resources || []).join('\n');
      input.onchange=()=>{doc.resources=input.value.split('\n').map(s=>s.trim()).filter(Boolean);};resources.append(input);root.append(resources);
    }
    const advanced=el('details');advanced.append(el('summary','Import / export dashboard layout (JSON)'));
    const json=el('textarea');json.setAttribute('aria-label','Dashboard layout JSON');json.value=JSON.stringify(d,null,2);advanced.append(json);
    advanced.append(button('Apply layout JSON',()=>{try{const parsed=JSON.parse(json.value);if(!Array.isArray(parsed.views)||!parsed.views.length)throw Error('A layout needs tabs');doc.design=parsed;viewIndex=0;sectionIndex=0;render();}catch(e){json.setCustomValidity(e.message);json.reportValidity();}}));root.append(advanced);
    previewNode=el('div',undefined,'lad-preview');root.append(previewNode);preview();
    const footer=el('div',undefined,'lad-toolbar');const status=el('p','', 'lad-status');status.setAttribute('role','status');
    const save=button('Save dashboard',async()=>{save.disabled=true;status.textContent='Saving…';try{await onSave(structuredClone(doc));status.textContent='Saved.';}catch(error){status.textContent=error.message || String(error);}finally{save.disabled=false;}});
    footer.append(save);if(onClose)footer.append(button('Close',onClose));root.append(footer,status);
  };
  render();return root;
}
