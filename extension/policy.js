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
  return ['click', 'form_input', 'navigate'].includes(tool);
}
export function checkGrant(tab, shared, grants, action) {
  if (!shared.includes(tab.id)) throw new Error('tab_not_shared');
  const origin = originOf(tab.url);
  const level = grants[origin];
  if (!level) throw new Error('origin_not_granted');
  if (isWrite(action.tool) && level !== 'interact') throw new Error('read_only');
  if (action.tool === 'navigate' && !grants[originOf(action.url)]) {
    throw new Error('destination_not_granted');
  }
  return origin;
}
