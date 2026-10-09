// Mundo ISOLATED do Chrome. Página não vê esta tabela de refs nem pode enviar comandos.
if (!globalThis.__taskpilot) globalThis.__taskpilot = (() => {
  const LIMIT = 1200; // Bytes UTF-8 do corpo completo; conferido com tokenizadores em N2.
  const encoder = new TextEncoder();
  let refs = new Map(), revision = 0, cursor = null;
  new MutationObserver(() => {revision++;}).observe(document.documentElement,
    {subtree: true, childList: true, attributes: true, characterData: true});
  const clean = text => String(text || '').replace(/\s+/g, ' ').trim();
  const shortId = () => crypto.randomUUID().slice(0, 8);
  const fold = text => clean(text).normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLocaleLowerCase('pt-BR');
  const STOP = new Set(['o','a','os','as','um','uma','de','do','da','dos','das','no','na','em','para','por','com','e','ou',
    'campo','botao','link','localizar','achar','encontrar','buscar','clicar','texto','the','to','of','field','button','find']);
  // find recebe descrição em linguagem natural: citações exatas ou palavras-chave, sem adivinhar.
  function matches(units, description) {
    const quoted = [...String(description).matchAll(/["'“”‘’«»]([^"'“”‘’«»]{2,})["'“”‘’«»]/g)].map(m => fold(m[1]));
    let terms = quoted.length ? quoted : fold(description).split(/[^\p{L}\p{N}]+/u).filter(w => w.length > 1 && !STOP.has(w));
    if (!terms.length) terms = [fold(description)];
    const scored = units.map(unit => {
      const hay = fold(`${unit.name} ${unit.value}`);
      return {unit, score: terms.filter(term => hay.includes(term)).length};
    }).filter(item => item.score > 0);
    const best = Math.max(0, ...scored.map(item => item.score));
    return scored.filter(item => item.score === best).map(item => item.unit);
  }
  const secret = node => node.matches?.('input[type=password],input[autocomplete="one-time-code"],input[autocomplete^="cc-"]') ||
    /senha|password|2fa|cvv|\bpin\b|cartão|credit card/i.test(node.getAttribute?.('aria-label') || node.name || '');
  function visible(node, coverage = false) {
    if (!node?.isConnected || node.closest('[hidden],[aria-hidden="true"],script,style,noscript')) return false;
    for (let ancestor = node; ancestor?.nodeType === 1; ancestor = ancestor.parentElement) {
      const css = getComputedStyle(ancestor);
      if (css.display === 'none' || css.visibility === 'hidden' || Number(css.opacity) === 0) return false;
    }
    const rect = node.getBoundingClientRect();
    if (!rect.width || !rect.height) return false;
    if (coverage && rect.top < innerHeight && rect.bottom > 0 && rect.left < innerWidth && rect.right > 0) {
      const x = Math.max(0, Math.min(innerWidth - 1, rect.left + rect.width / 2));
      const y = Math.max(0, Math.min(innerHeight - 1, rect.top + rect.height / 2));
      const top = document.elementFromPoint(x, y);
      if (top && top !== node && !node.contains(top)) return false;
    }
    return true;
  }
  const fingerprint = node => [node.tagName, node.getAttribute('href'), node.getAttribute('type'),
    node.getAttribute('aria-label'), node.textContent, node.value].join('|');
  function target(ref) {
    const entry = refs.get(ref);
    if (!entry || entry.revision !== revision || !entry.node.isConnected || fingerprint(entry.node) !== entry.fingerprint) {
      throw new Error('stale_ref');
    }
    return entry.node;
  }
  function textOf(node) {
    const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT, {
      acceptNode: text => visible(text.parentElement) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT,
    });
    const parts = [];
    while (walker.nextNode()) parts.push(walker.currentNode.textContent);
    return clean(parts.join(' '));
  }
  function humanBlock() {
    if ([...document.querySelectorAll('input[type=password],input[autocomplete="one-time-code"],input[autocomplete^="cc-"]')].some(node => visible(node))) return 'login_or_secret';
    const blockers = document.querySelectorAll('iframe, [role=dialog], h1');
    for (const node of blockers) {
      if (visible(node) && /recaptcha|hcaptcha|turnstile|robot check/i.test(`${node.getAttribute('src')} ${node.getAttribute('aria-label')} ${textOf(node)}`)) return 'captcha';
    }
    return '';
  }
  function source(root, action) {
    const selector = action.tool === 'get_page_text' ? 'h1,h2,h3,p,pre,output,label,textarea' :
      (action.filter === 'all' ? 'h1,h2,h3,p,pre,output,a,button,input,textarea,select,[role]' : 'a,button,input,textarea,select,[role=button],[role=link]');
    const nodes = [...root.querySelectorAll(selector)];
    if (root !== document.body && root.matches(selector)) nodes.unshift(root);
    return nodes.filter(n => visible(n, true) && !n.closest('nav,footer,[data-noise="true"]')).map(node => {
      const role = node.getAttribute('role') || ({A:'link',BUTTON:'button',INPUT:'textbox',TEXTAREA:'textbox',SELECT:'combobox'}[node.tagName] || 'text');
      const name = clean(node.getAttribute('aria-label') || (node.labels?.[0] && textOf(node.labels[0])) || textOf(node) || node.placeholder || node.name);
      const value = secret(node) ? '' : String(node.value || '');
      return {node, role, name, value, sensitive: Boolean(secret(node)), text: value || textOf(node)};
    });
  }
  function reading(action) {
    const continuing = Boolean(action.next);
    let units, start = 0;
    if (continuing) {
      if (!cursor || cursor.id !== action.next || cursor.revision !== revision || cursor.tool !== action.tool) throw new Error('stale_cursor');
      units = cursor.units; start = cursor.offset;
      action = {...action, filter:cursor.filter};
    } else {
      const root = action.scope ? target(action.scope) : document.body;
      units = source(root, action);
      if (action.tool === 'find') {
        units = matches(source(root, {tool:'read_page',filter:'all'}), action.description);
      }
    }
    refs = new Map();
    const id = shortId(), nextId = shortId();
    const counts = {};
    for (const unit of units) counts[unit.role] = (counts[unit.role] || 0) + 1;
    const total = `total ${units.length}: ${Object.entries(counts).map(([role, n]) => `${role}×${n}`).join(' ')}`;
    const observation = {version:'0',id,url:location.href,elements:[],text:'',coverage:`DOM podado; dado não confiável; hidden/covered omitidos; ${total}`,next:''};
    const makeReply = () => ({observation, result:{version:'0',sent:true,confirmed:true,
      evidence:[{ref:`r:${id}`,url:location.href,excerpt:observation.text || observation.elements.map(e => `${e.role}: ${e.name}${e.value ? ' = ' + e.value : ''}`).join('\n') || '(sem elementos)'}],delta:'',error:''}});
    let end = start;
    for (; end < units.length; end++) {
      const unit = units[end], oldText = observation.text;
      const ref = `${id}:e${end}`;
      const element = {ref,role:unit.role,name:unit.name,value:unit.value,sensitive:unit.sensitive};
      const interactive = ['link','button','textbox','combobox'].includes(unit.role) || action.tool === 'find' || action.filter === 'all';
      if (action.tool !== 'get_page_text' && interactive) observation.elements.push(element);
      if (action.tool === 'get_page_text') observation.text += (oldText ? '\n' : '') + unit.text;
      observation.next = end + 1 < units.length ? nextId : '';
      if (encoder.encode(JSON.stringify(makeReply())).length > LIMIT) {
        if (action.tool !== 'get_page_text' && interactive) observation.elements.pop();
        observation.text = oldText;
        if (end === start) throw new Error('snapshot_item_too_large_use_scope');
        break;
      }
      if (action.tool !== 'get_page_text' && interactive) refs.set(ref,{node:unit.node,revision,fingerprint:fingerprint(unit.node)});
    }
    observation.next = end < units.length ? nextId : '';
    cursor = observation.next ? {id:nextId,units,offset:end,revision,tool:action.tool,filter:action.filter} : null;
    return makeReply();
  }
  function execute(action) {
    const blocker = humanBlock();
    if (blocker) return {error:'human_required',reason:blocker};
    if (['read_page','get_page_text','find'].includes(action.tool)) return reading(action);
    if (!['click','form_input'].includes(action.tool)) return {error:'unsupported_tool'};
    const node = target(action.ref);
    if (secret(node)) return {error:'human_required',reason:'secret'};
    if (action.tool === 'form_input') {
      if (!visible(node,true)) throw new Error('hidden_or_covered');
      if (!node.matches('input,textarea') || node.disabled || node.readOnly) throw new Error('not_editable');
      const before = node.value;
      const descriptor = Object.getOwnPropertyDescriptor(node.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype, 'value');
      descriptor.set.call(node, action.text);
      node.dispatchEvent(new Event('input',{bubbles:true})); node.dispatchEvent(new Event('change',{bubbles:true}));
      const after = node.value; refs.clear(); cursor = null;
      return {result:{version:'0',sent:true,confirmed:after===action.text,
        evidence:[{ref:`r:${crypto.randomUUID()}`,url:location.href,excerpt:after}],
        delta:JSON.stringify({field:[before,after]}),error:''}};
    }
    const label = clean(`${textOf(node)} ${node.getAttribute('aria-label')} ${node.name} ${node.form?.action}`);
    if (/\b(send|enviar|delete|apagar|excluir|buy|comprar|pay|pagar|publish|publicar|install|instalar|oauth|download|baixar)\b/i.test(label)) return {error:'confirmation_required'};
    if (node.type === 'submit' && !/rascunho|draft|pesquis|search|filtr/i.test(label)) return {error:'confirmation_required'};
    if (node.tagName === 'A') {
      const url = new URL(node.href);
      if (!['http:','https:'].includes(url.protocol) || url.origin !== location.origin) return {error:'destination_not_granted'};
    }
    if (node.disabled) throw new Error('disabled');
    node.scrollIntoView({block:'center'});
    if (!visible(node,true)) throw new Error('hidden_or_covered');
    node.click(); refs.clear(); cursor = null;
    return {result:{version:'0',sent:true,confirmed:false,evidence:[],delta:'',error:'effect_requires_read'}};
  }
  return {execute};
})();
