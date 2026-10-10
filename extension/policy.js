// Política independente da escolha do modelo. Sem permissão por origem, negar.
export function originOf(url) {
  const parsed = new URL(url);
  if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password) {
    throw new Error('origin_not_allowed');
  }
  return parsed.origin;
}
export function patternFor(origin) {
  const parsed = new URL(origin);
  return `${parsed.protocol}//${parsed.hostname}/*`;
}
export function isWrite(tool) {
  return ['click', 'form_input', 'navigate', 'tabs_create', 'tabs_close'].includes(tool);
}
export function checkGrant(tab, shared, grants, action) {
  if (!shared.includes(tab.id)) throw new Error('tab_not_shared');
  const origin = originOf(tab.url);
  const level = grants[origin];
  if (!level) throw new Error('origin_not_granted');
  if (isWrite(action.tool) && level !== 'interact') throw new Error('read_only');
  if (['navigate', 'tabs_create'].includes(action.tool) && !grants[originOf(action.url)]) {
    throw new Error('destination_not_granted');
  }
  return origin;
}
// Só abas compartilhadas existem para o agente; só as que ele criou podem ser fechadas por ele.
export function checkTabTarget(action, shared, created) {
  const id = Number(action.tab);
  if (!Number.isInteger(id) || !shared.includes(id)) throw new Error('tab_not_shared');
  if (action.tool === 'tabs_close' && !created.includes(id)) throw new Error('not_agent_tab');
  return id;
}
// Validade: `expiry[origem]` é um instante em ms; ausente = vale até parar/fechar o Chrome (sessão).
export const TASK_TTL_MS = 30 * 60 * 1000;
export function expiredOrigins(expiry, now) {
  return Object.keys(expiry).filter(origin => expiry[origin] <= now);
}
// A permissão do Chrome é por host (sem porta): só some quando nenhuma origem restante a usa.
export function patternsToRemove(dropped, remaining) {
  const keep = new Set(remaining.map(patternFor));
  return [...new Set(dropped.map(patternFor))].filter(pattern => !keep.has(pattern));
}
