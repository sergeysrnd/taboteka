const BASE = import.meta.env.VITE_API_BASE || 'http://127.0.0.1:8766';
let token = '';
export function setToken(value) { token = value; }
export function endpoint(path) { return BASE + path; }
export async function api(path, body, signal) {
  const request = () => fetch(endpoint(path), {
    signal,
    ...(body === undefined ? {} : {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Taboteka-Token': token}, body: JSON.stringify(body)})
  });
  let response = await request();
  if (response.status === 403 && body !== undefined) {
    const bootstrap = await fetch(endpoint('/api/bootstrap'), {signal});
    if (bootstrap.ok) { token = (await bootstrap.json()).token; response = await request(); }
  }
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Не удалось выполнить запрос');
  return data;
}
const numberFormat = new Intl.NumberFormat('ru-RU');
export const count = n => numberFormat.format(n || 0);
export function plural(n, forms) {
  const mod100 = Math.abs(n) % 100, mod10 = mod100 % 10;
  return mod100 >= 11 && mod100 <= 14 ? forms[2] : mod10 === 1 ? forms[0] : mod10 >= 2 && mod10 <= 4 ? forms[1] : forms[2];
}
