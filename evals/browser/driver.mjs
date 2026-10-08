// CDP somente da bancada, via pipe privado. Nunca faz parte da ponte do produto.
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {mkdirSync, openSync} from 'node:fs';
import {resolve, relative} from 'node:path';

const directory = resolve(process.argv[2]);
if (process.env.TASKPILOT_TEST_SESSION !== '1' || process.env.WAYLAND_DISPLAY !== 'wayland-tp') {
  throw new Error('Chrome de teste exige a sessão isolada');
}
const local = relative(resolve('runs'), directory);
if (!local || local.startsWith('..') || local.startsWith('/')) {
  throw new Error('perfil de teste deve ficar em runs/ dentro do repo');
}
mkdirSync(directory, {recursive:true});
const chrome = spawn('google-chrome', [
  ...(process.env.TP_BROWSER_HEADFUL === '1' ? ['--ozone-platform=wayland'] : ['--headless=new']), '--disable-gpu', '--no-first-run', '--no-default-browser-check',
  '--password-store=basic', '--disable-features=Translate', '--disable-background-networking', '--disable-component-update', '--disable-sync',
  '--remote-debugging-pipe', '--enable-unsafe-extension-debugging',
  `--user-data-dir=${directory}/profile`, `--load-extension=${resolve('extension')}`,
  'about:blank',
], {env:{...process.env, HOME:`${directory}/home`, XDG_CONFIG_HOME:`${directory}/home/.config`},
  stdio:['ignore','ignore',openSync(`${directory}/chrome.log`,'w'),'pipe','pipe']});
let sequence = 0, buffer = '', pending = new Map();
function command(method, params = {}, sessionId) {
  return new Promise((resolve, reject) => {
    const id = ++sequence;
    const timer = setTimeout(() => {pending.delete(id); reject(new Error(`timeout: ${method}`));},15000);
    pending.set(id, {resolve, reject, timer});
    chrome.stdio[3].write(JSON.stringify({id,method,params,...(sessionId ? {sessionId} : {})})+'\0');
  });
}
chrome.stdio[4].on('data', data => {
  buffer += data.toString();
  let index;
  while ((index = buffer.indexOf('\0')) >= 0) {
    const response = JSON.parse(buffer.slice(0,index)); buffer = buffer.slice(index+1);
    const item = pending.get(response.id);
    if (!item) continue;
    clearTimeout(item.timer); pending.delete(response.id);
    if (response.error) item.reject(new Error(JSON.stringify(response.error)));
    else item.resolve(response.result);
  }
});
chrome.on('exit', () => {for (const item of pending.values()) {clearTimeout(item.timer); item.reject(new Error('chrome exited'));}});
const version = await command('Browser.getVersion');
let extensionId, loading = 'flag';
const extensions = await command('Extensions.getExtensions');
extensionId = extensions.extensions.find(e => e.name === 'TaskPilot Bridge')?.id;
if (!extensionId) {
  ({id:extensionId} = await command('Extensions.loadUnpacked',{path:resolve('extension')}));
  loading = 'Extensions.loadUnpacked (flag ignorada pelo Chrome instalado)';
}
console.log(JSON.stringify({ready:true,extensionId,version,loading}));
const input = createInterface({input:process.stdin});
for await (const line of input) {
  const request = JSON.parse(line);
  try {
    const result = await command(request.method,request.params,request.sessionId);
    console.log(JSON.stringify({id:request.id,result}));
  } catch (error) {console.log(JSON.stringify({id:request.id,error:error.message}));}
}
chrome.kill('SIGTERM');
