(() => {
  const payload = document.getElementById('graph-data');
  if (!payload) return;
  const graph = JSON.parse(payload.textContent);
  const svg = document.getElementById('relationship-canvas');
  const inspector = document.getElementById('graph-inspector');
  const colors = {study:'#5b73b3', sample:'#368f86', sequence:'#73964d', publication:'#b37c47', organism:'#8c67ac', disease:'#bf687b', repository:'#819091', program:'#bc983b'};
  const anchors = {study:[180,145], sample:[385,205], sequence:[565,350], publication:[155,450], organism:[815,155], disease:[835,475], repository:[510,70], program:[370,555]};
  const nodes = graph.nodes.map((n,i) => ({...n, x:anchors[n.type][0]+Math.cos(i*2.4)*40, y:anchors[n.type][1]+Math.sin(i*2.4)*40, degree:0}));
  const byId = new Map(nodes.map(n => [n.id,n]));
  graph.edges.forEach(e => {byId.get(e.source).degree++; byId.get(e.target).degree++;});
  const enabled = new Set(Object.keys(colors));
  const nodeElements = new Map();
  const edgeElements = [];
  let suppressDragClick = false;
  let selected = null;
  let query = '';
  let allLabels = false;
  let view = {x:0,y:0,w:1000,h:640};
  const ns = 'http://www.w3.org/2000/svg';
  const element = (name,attrs={}) => {
    const el = document.createElementNS(ns,name);
    Object.entries(attrs).forEach(([k,v]) => el.setAttribute(k,String(v)));
    return el;
  };
  const html = (tag,text,className) => {
    const el = document.createElement(tag);
    if (text != null) el.textContent = text;
    if (className) el.className = className;
    return el;
  };

  // A deterministic, bounded layout keeps the view usable offline without a graph service.
  for (let iteration=0; iteration<100; iteration++) {
    const forces = nodes.map(n => ({x:(anchors[n.type][0]-n.x)*.035,y:(anchors[n.type][1]-n.y)*.035}));
    for(let i=0;i<nodes.length;i++) for(let j=i+1;j<nodes.length;j++) {
      let dx=nodes[i].x-nodes[j].x, dy=nodes[i].y-nodes[j].y;
      const distance=Math.max(10,Math.hypot(dx,dy));
      const strength=950/(distance*distance);
      forces[i].x+=dx/distance*strength; forces[i].y+=dy/distance*strength;
      forces[j].x-=dx/distance*strength; forces[j].y-=dy/distance*strength;
    }
    nodes.forEach((n,i) => {n.x+=Math.max(-8,Math.min(8,forces[i].x)); n.y+=Math.max(-8,Math.min(8,forces[i].y));});
  }
  function setView() { if(svg) svg.setAttribute('viewBox',`${view.x} ${view.y} ${view.w} ${view.h}`); }
  function fit() {
    const shown = nodes.filter(n => enabled.has(n.type));
    if(!shown.length) {view={x:0,y:0,w:1000,h:640};setView();return;}
    const xs=shown.map(n=>n.x), ys=shown.map(n=>n.y);
    const width=Math.max(450,Math.max(...xs)-Math.min(...xs)+220);
    const height=Math.max(width*.64,Math.max(...ys)-Math.min(...ys)+180);
    view={x:(Math.min(...xs)+Math.max(...xs)-width)/2,y:(Math.min(...ys)+Math.max(...ys)-height)/2,w:width,h:height};
    setView();
  }
  function zoom(factor) {
    const width=Math.min(4000,Math.max(150,view.w*factor));
    const height=view.h*(width/view.w);
    view={x:view.x+(view.w-width)/2,y:view.y+(view.h-height)/2,w:width,h:height};setView();
  }
  function render() {
    if(!svg) return;
    svg.replaceChildren();
    nodeElements.clear();
    edgeElements.length = 0;
    const adjacent = new Set(selected ? [selected] : []);
    if(selected) graph.edges.forEach(e => {if(e.source===selected||e.target===selected){adjacent.add(e.source);adjacent.add(e.target);}});
    const matches = n => !query || `${n.label} ${n.identifier}`.toLowerCase().includes(query);
    const faded = n => (selected && !adjacent.has(n.id)) || !matches(n);
    const edges = element('g',{'aria-hidden':'true'});
    graph.edges.forEach(edge => {
      const a=byId.get(edge.source), b=byId.get(edge.target);
      if(!enabled.has(a.type)||!enabled.has(b.type)) return;
      const line=element('line',{x1:a.x,y1:a.y,x2:b.x,y2:b.y,class:`graph-edge ${edge.status}${(selected && edge.source!==selected && edge.target!==selected) || (query && !matches(a) && !matches(b)) ? ' faded' : ''}`});
      edgeElements.push({edge, line});
      const title=element('title');title.textContent=`${a.identifier} → ${edge.relation} → ${b.identifier} (${edge.status})`;line.append(title);edges.append(line);
    });
    svg.append(edges);
    nodes.forEach(node => {
      if(!enabled.has(node.type)) return;
      const group=element('g',{transform:`translate(${node.x},${node.y})`,class:`graph-node${faded(node)?' faded':''}${selected===node.id?' selected':''}`,'data-node-id':node.id,tabindex:0,role:'button','aria-label':`${node.type}: ${node.label}. ${node.degree} relationships.`});
      const radius=7+Math.min(9,Math.log2(node.degree+1)*1.6);
      const circle=element('circle',{r:radius,fill:colors[node.type]});group.append(circle);
      const title=element('title');title.textContent=`${node.label}\n${node.identifier}`;group.append(title);
      if(allLabels || adjacent.has(node.id) || (query && matches(node)) || (nodes.length<45) || node.type==='repository') {
        const label=element('text',{x:radius+5,y:4,class:'graph-node-label'});
        const display=node.type==='repository'?node.label:node.identifier.replace(/^name:|^reported:/,'');
        label.textContent=display.length>32?display.slice(0,30)+'…':display;
        group.append(label);
      }
      group.addEventListener('click',event=>{event.stopPropagation();select(node.id);});
      group.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();select(node.id);}});
      nodeElements.set(node.id, group);
      svg.append(group);
    });
  }
  function select(id) {
    const node=byId.get(id);if(!node)return;
    selected=id;enabled.add(node.type);
    const checkbox=document.querySelector(`[data-graph-type="${node.type}"]`);if(checkbox)checkbox.checked=true;
    inspector.replaceChildren(html('span',node.type.toUpperCase(),'eyebrow'),html('h2',node.label),html('code',node.identifier));
    if(node.url){const a=html('a','Open external identifier ↗','text-button');a.href=node.url;a.target='_blank';a.rel='noopener noreferrer';const paragraph=html('p');paragraph.append(a);inspector.append(paragraph);}
    inspector.append(html('h3',`${node.degree} relationships in this view`));
    const relationships=graph.edges.filter(e=>e.source===id||e.target===id);
    relationships.forEach(edge=>{
      const other=byId.get(edge.source===id?edge.target:edge.source);
      const box=html('section',null,'graph-evidence');
      const label=html('button',other.label,'text-button');label.type='button';label.addEventListener('click',()=>select(other.id));
      box.append(html('small',`${edge.source===id?'Outgoing':'Incoming'} · ${edge.relation} · ${edge.status}`),label);
      edge.evidence.forEach(evidence=>{
        const item=html('div',null,'evidence-source');
        item.append(html('code',evidence.path),html('p',evidence.value));
        const link=html('a',`Record ${evidence.record_id} · line ${evidence.line} ↗`);
        link.href=graph.record_urls[String(evidence.record_id)];item.append(link);
        if(edge.status==='derived')item.append(html('small',evidence.basis));
        box.append(item);
      });
      inspector.append(box);
    });
    inspector.append(html('p','Evidence lists show up to five supporting source fields per relationship.','form-note'));
    render();
  }
  document.querySelectorAll('[data-graph-type]').forEach(input=>input.addEventListener('change',()=>{input.checked?enabled.add(input.dataset.graphType):enabled.delete(input.dataset.graphType);render();}));
  document.querySelectorAll('[data-select-node]').forEach(button=>button.addEventListener('click',()=>{select(button.dataset.selectNode);inspector.scrollIntoView({block:'nearest'});}));
  document.getElementById('graph-search')?.addEventListener('input',event=>{query=event.target.value.toLowerCase().trim();render();});
  document.getElementById('graph-labels')?.addEventListener('change',event=>{allLabels=event.target.checked;render();});
  document.getElementById('graph-fit')?.addEventListener('click',fit);
  document.getElementById('graph-zoom-in')?.addEventListener('click',()=>zoom(.8));
  document.getElementById('graph-zoom-out')?.addEventListener('click',()=>zoom(1.25));
  document.getElementById('graph-clear')?.addEventListener('click',()=>{selected=null;inspector.replaceChildren(html('h2','Select an entity'),html('p','Choose a node to inspect its relationships and supporting source fields.'));render();});
  if(svg) {
    let drag = null;
    // Ignore the synthetic click after a drag; ordinary clicks still select nodes.
    svg.addEventListener('click', event => {
      if (!suppressDragClick || event.detail === 0) return;
      suppressDragClick = false;
      event.preventDefault();
      event.stopImmediatePropagation();
    }, true);
    svg.addEventListener('pointerdown', event => {
      if (drag || !event.isPrimary || event.button !== 0) return;
      const matrix = svg.getScreenCTM();
      if (!matrix) return;
      const group = event.target.closest('.graph-node');
      const node = group ? byId.get(group.dataset.nodeId) : null;
      const capture = group || svg;
      suppressDragClick = false;
      drag = {pointerId:event.pointerId, capture, node, inverse:matrix.inverse(),
              x:event.clientX, y:event.clientY, startX:node ? node.x : view.x,
              startY:node ? node.y : view.y, moved:false};
      capture.setPointerCapture(event.pointerId);
    });
    svg.addEventListener('pointermove', event => {
      if (!drag || event.pointerId !== drag.pointerId) return;
      const screenX = event.clientX - drag.x, screenY = event.clientY - drag.y;
      // Small pointer jitter is still a click, not a layout change.
      if (!drag.moved && Math.hypot(screenX, screenY) < 4) return;
      drag.moved = true;
      svg.classList.add('graph-dragging');
      event.preventDefault();
      // Account for zoom, pan, and SVG letterboxing using the initial screen transform.
      const dx = drag.inverse.a * screenX + drag.inverse.c * screenY;
      const dy = drag.inverse.b * screenX + drag.inverse.d * screenY;
      if (drag.node) {
        const node = drag.node;
        node.x = drag.startX + dx;
        node.y = drag.startY + dy;
        nodeElements.get(node.id)?.setAttribute('transform', `translate(${node.x},${node.y})`);
        edgeElements.forEach(({edge, line}) => {
          if (edge.source === node.id) {line.setAttribute('x1', node.x);line.setAttribute('y1', node.y);}
          if (edge.target === node.id) {line.setAttribute('x2', node.x);line.setAttribute('y2', node.y);}
        });
      } else {
        view.x = drag.startX - dx;
        view.y = drag.startY - dy;
        setView();
      }
    });
    function endDrag(event) {
      if (!drag || event.pointerId !== drag.pointerId) return;
      const finished = drag;
      drag = null;
      suppressDragClick = finished.moved;
      svg.classList.remove('graph-dragging');
      if (finished.capture.hasPointerCapture(finished.pointerId)) finished.capture.releasePointerCapture(finished.pointerId);
    }
    svg.addEventListener('pointerup', endDrag);
    svg.addEventListener('pointercancel', endDrag);
    svg.addEventListener('lostpointercapture', endDrag);
    svg.addEventListener('wheel',event=>{event.preventDefault();if(!drag)zoom(event.deltaY>0?1.12:.89);},{passive:false});
    fit();render();
  }
})();
