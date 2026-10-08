import {originOf, patternFor} from './policy.js';
const tabs = new Map();
const status = document.getElementById('status');
for (const tab of await chrome.tabs.query({})) {
  try {
    originOf(tab.url);
    tabs.set(String(tab.id), tab);
    const option = document.createElement('option');
    option.value = String(tab.id); option.textContent = `${tab.title} — ${new URL(tab.url).origin}`;
    document.getElementById('tab').append(option);
    if (tab.active) option.selected = true;
  } catch { /* Abas internas não são compartilháveis. */ }
}
document.getElementById('share').addEventListener('click', async () => {
  try {
    const tab = tabs.get(document.getElementById('tab').value);
    if (!tab) throw new Error('Escolha uma aba.');
    const origin = originOf(tab.url);
    const granted = await chrome.permissions.request({origins: [patternFor(origin)]});
    if (!granted) throw new Error('Permissão negada.');
    const result = await chrome.runtime.sendMessage({type: 'share', tab: tab.id,
      level: document.getElementById('level').value});
    status.textContent = result.error || `Aba compartilhada: ${origin}`;
  } catch (error) { status.textContent = error.message; }
});
for (const type of ['connect', 'stop']) {
  document.getElementById(type).addEventListener('click', async () => {
    const result = await chrome.runtime.sendMessage({type});
    status.textContent = result.error || (type === 'stop' ? 'Sessão revogada.' : 'Ponte solicitada.');
  });
}
