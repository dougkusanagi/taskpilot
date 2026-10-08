import {originOf, patternFor, checkGrant} from './policy.js';
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
    try { result = await execute(message.tab, message.action); }
    catch (error) { result = {error: error.message}; }
    port?.postMessage({id: message.id, result});
  });
  chrome.action.setBadgeText({text: 'ON'});
}

async function execute(tabId, action) {
  const tab = await chrome.tabs.get(tabId);
  const {shared = [], grants = {}} = await chrome.storage.session.get(['shared', 'grants']);
  const origin = checkGrant(tab, shared, grants, action);
  if (!await chrome.permissions.contains({origins: [patternFor(origin)]})) {
    throw new Error('chrome_permission_missing');
  }
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
    func: action => {
      try {return globalThis.__taskpilot.execute(action);}
      catch (error) {return {error:error.message};}
    }, args: [action]});
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
      await chrome.storage.session.set({shared: [], grants: {}});
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
      const {shared = [], grants = {}} = await chrome.storage.session.get(['shared', 'grants']);
      if (!shared.includes(tab.id)) shared.push(tab.id);
      grants[origin] = message.level;
      await chrome.storage.session.set({shared, grants});
      await chrome.action.setBadgeText({text: 'ON'});
      return {ok: true};
    }
    throw new Error('invalid_message');
  })().then(respond, error => respond({error: error.message}));
  return true;
});
connect();
