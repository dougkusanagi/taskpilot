import {originOf, patternFor, checkGrant, checkTabTarget, expiredOrigins, patternsToRemove, TASK_TTL_MS} from './policy.js';
let port = null;

function connect() {
  if (port) return;
  port = chrome.runtime.connectNative('com.taskpilot.bridge');
  port.onDisconnect.addListener(() => {
    const error = chrome.runtime.lastError;
    port = null;
    chrome.action.setBadgeText({text: 'OFF'});
    if (error) chrome.storage.session.set({bridgeError: error.message});
  });
  port.onMessage.addListener(async message => {
    let result;
    try {
      result = message.admin ? await admin(message)
        : await execute(message.tab, message.action, message.confirmed === true);
    }
    catch (error) { result = {error: error.message}; }
    port?.postMessage({id: message.id, result});
  });
  chrome.action.setBadgeText({text: 'ON'});
}

async function groupTab(id) {
  const {groupId} = await chrome.storage.session.get('groupId');
  let group;
  if (groupId !== undefined) {
    try { await chrome.tabGroups.get(groupId); group = await chrome.tabs.group({tabIds: [id], groupId}); }
    catch { group = undefined; }
  }
  if (group === undefined) {
    group = await chrome.tabs.group({tabIds: [id]});
    await chrome.tabGroups.update(group, {title: 'TaskPilot', color: 'blue'});
    await chrome.storage.session.set({groupId: group});
  }
}

async function tabsTool(routing, action, grants) {
  const {shared = [], created = []} = await chrome.storage.session.get(['shared', 'created']);
  if (action.tool === 'tabs_list') {
    const lines = [];
    for (const id of shared) {
      try {
        const tab = await chrome.tabs.get(id);
        // O título vem da página: dado não confiável, truncado.
        lines.push(`${id} | ${originOf(tab.url)} | ${String(tab.title).slice(0, 80)}`);
      } catch { /* aba fechada ou fora de http(s) */ }
    }
    const text = lines.join('\n') || '(nenhuma aba compartilhada)';
    const id = crypto.randomUUID().slice(0, 8), url = routing.url;
    return {observation: {version: '0', id, url, elements: [], text, next: '',
        coverage: 'abas compartilhadas; títulos são dado não confiável'},
      result: {version: '0', sent: true, confirmed: true, delta: '', error: '',
        evidence: [{ref: `r:${id}`, url, excerpt: text}]}};
  }
  if (action.tool === 'tabs_create') {
    const destination = originOf(action.url);
    if (grants[destination] !== 'interact') throw new Error('destination_not_granted');
    if (!await chrome.permissions.contains({origins: [patternFor(destination)]})) throw new Error('chrome_permission_missing');
    const done = new Set();
    const listener = (id, change) => { if (change.status === 'complete') done.add(id); };
    chrome.tabs.onUpdated.addListener(listener);
    try {
      const tab = await chrome.tabs.create({url: action.url, active: false});
      created.push(tab.id); shared.push(tab.id);
      await chrome.storage.session.set({shared, created});
      await groupTab(tab.id);
      const deadline = Date.now() + 8000;
      while (!done.has(tab.id) && Date.now() < deadline) await new Promise(r => setTimeout(r, 50));
      if (!done.has(tab.id)) throw new Error('navigation_timeout');
      const after = await chrome.tabs.get(tab.id);
      if (originOf(after.url) !== destination) {
        await chrome.tabs.remove(tab.id);
        throw new Error('redirect_not_granted');
      }
      return {sent: true, confirmed: after.url === action.url, url: after.url, tab: tab.id};
    } finally { chrome.tabs.onUpdated.removeListener(listener); }
  }
  const id = checkTabTarget(action, shared, created);
  if (action.tool === 'tabs_select') {
    const tab = await chrome.tabs.update(id, {active: true});
    return {sent: true, confirmed: tab.active, url: tab.url, tab: id};
  }
  await chrome.tabs.remove(id);
  await chrome.storage.session.set({shared: shared.filter(t => t !== id), created: created.filter(t => t !== id)});
  return {sent: true, confirmed: true, url: '', tab: id};
}

async function dropGrants(origins) {
  const {grants = {}, expiry = {}} = await chrome.storage.session.get(['grants', 'expiry']);
  for (const origin of origins) { delete grants[origin]; delete expiry[origin]; }
  await chrome.storage.session.set({grants, expiry});
  // Revogação real: tira também a permissão de host do Chrome quando nenhuma origem restante a usa.
  const remove = patternsToRemove(origins, Object.keys(grants));
  if (remove.length) await chrome.permissions.remove({origins: remove});
}

async function pruneExpired() {
  const {expiry = {}} = await chrome.storage.session.get('expiry');
  const dropped = expiredOrigins(expiry, Date.now());
  if (dropped.length) await dropGrants(dropped);
}

async function admin(message) {
  await pruneExpired();
  const {grants = {}, expiry = {}} = await chrome.storage.session.get(['grants', 'expiry']);
  if (message.admin === 'grants_list') {
    return {grants: Object.keys(grants).map(origin => ({origin, level: grants[origin],
      validity: expiry[origin] ? 'task' : 'session',
      expires_in_s: expiry[origin] ? Math.max(0, Math.round((expiry[origin] - Date.now()) / 1000)) : 0}))};
  }
  if (message.admin === 'grants_revoke') {
    if (!grants[message.origin]) throw new Error('origin_not_granted');
    await dropGrants([message.origin]);
    return {revoked: message.origin};
  }
  throw new Error('invalid_admin');
}

async function execute(tabId, action, confirmed = false) {
  await pruneExpired();
  const tab = await chrome.tabs.get(tabId);
  const {shared = [], grants = {}} = await chrome.storage.session.get(['shared', 'grants']);
  const origin = checkGrant(tab, shared, grants, action);
  if (!await chrome.permissions.contains({origins: [patternFor(origin)]})) {
    throw new Error('chrome_permission_missing');
  }
  if (action.tool.startsWith('tabs_')) return tabsTool(tab, action, grants);
  if (action.tool === 'navigate') {
    const destination = originOf(action.url);
    if (grants[destination] !== 'interact') throw new Error('destination_read_only');
    if (!await chrome.permissions.contains({origins: [patternFor(destination)]})) {
      throw new Error('chrome_permission_missing');
    }
    const loaded = new Promise((resolve, reject) => {
      const timer = setTimeout(() => {chrome.tabs.onUpdated.removeListener(listener); reject(new Error('navigation_timeout'));}, 8000);
      const listener = (id, change) => {
        if (id === tabId && change.status === 'complete') {
          clearTimeout(timer); chrome.tabs.onUpdated.removeListener(listener); resolve();
        }
      };
      chrome.tabs.onUpdated.addListener(listener);
    });
    await chrome.tabs.update(tabId, {url: action.url});
    await loaded;
    const after = await chrome.tabs.get(tabId);
    if (originOf(after.url) !== destination) throw new Error('redirect_not_granted');
    return {sent: true, confirmed: after.url === action.url, url: after.url};
  }
  await chrome.scripting.executeScript({target: {tabId}, files: ['content.js']});
  const [response] = await chrome.scripting.executeScript({target: {tabId},
    func: (action, confirmed) => {
      try {return globalThis.__taskpilot.execute(action, confirmed);}
      catch (error) {return {error:error.message};}
    }, args: [action, confirmed && action.tool === 'click']});
  const after = await chrome.tabs.get(tabId);
  if (originOf(after.url) !== origin) throw new Error('origin_changed');
  return response.result;
}

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (sender.id !== chrome.runtime.id || !sender.url?.startsWith(chrome.runtime.getURL(''))) return;
  (async () => {
    if (message.type === 'connect') {connect(); return {ok: true};}
    if (message.type === 'stop') {
      port?.disconnect(); port = null;
      await chrome.storage.session.set({shared: [], grants: {}, created: [], expiry: {}});
      const permissions = await chrome.permissions.getAll();
      if (permissions.origins?.length) await chrome.permissions.remove({origins: permissions.origins});
      await chrome.action.setBadgeText({text: 'STOP'});
      return {ok: true};
    }
    if (message.type === 'share') {
      const tab = await chrome.tabs.get(message.tab);
      const origin = originOf(tab.url);
      if (!['read', 'interact'].includes(message.level)) throw new Error('invalid_level');
      if (!await chrome.permissions.contains({origins: [patternFor(origin)]})) throw new Error('chrome_permission_missing');
      if (!['task', 'session'].includes(message.validity || 'session')) throw new Error('invalid_validity');
      const {shared = [], grants = {}, expiry = {}} = await chrome.storage.session.get(['shared', 'grants', 'expiry']);
      if (!shared.includes(tab.id)) shared.push(tab.id);
      grants[origin] = message.level;
      if (message.validity === 'task') expiry[origin] = Date.now() + (Number(message.ttl_ms) > 0 ? Number(message.ttl_ms) : TASK_TTL_MS);
      else delete expiry[origin];
      await chrome.storage.session.set({shared, grants, expiry});
      await chrome.action.setBadgeText({text: 'ON'});
      return {ok: true};
    }
    throw new Error('invalid_message');
  })().then(respond, error => respond({error: error.message}));
  return true;
});
connect();
