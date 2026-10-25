import { nodeopsFetch } from './http.js';

const entries = new Map();
let activeView = null;
function entry(build) {
  if (!entries.has(build)) entries.set(build, { data: null, hardware: null, error: '', pending: null });
  return entries.get(build);
}
function notify(build) { activeView?.(build, entry(build)); }
export function watchRuntime(callback) { activeView = callback; }
export function runtimeState(build) { return entry(build); }
export function resetRuntimeNetwork() {
  const previous = [...entries];
  entries.clear();
  for (const [build, old] of previous) {
    entries.set(build, {data: old.data, hardware: old.hardware, error: '', pending: null});
    void loadRuntime(build, true);
  }
}
export function loadRuntime(build, force = false) {
  const state = entry(build);
  if (state.pending) return state.pending;
  if (state.data && !force) return Promise.resolve(state.data);
  state.error = '';
  state.pending = (async () => {
    try {
      const response = await nodeopsFetch('/qbb_nodeops/migration/runtime-catalog?build=' +
        encodeURIComponent(build) + (force ? '&refresh=1' : ''), { signal: AbortSignal.timeout(90000) });
      const data = await response.json();
      if (data.hardware) state.hardware = data.hardware;
      if (!response.ok) throw Error(data.error || response.statusText);
      if (!Array.isArray(data.builds) || !Array.isArray(data.combinations)) throw Error('Invalid runtime catalogue response');
      state.data = data;
      state.error = data.refresh_error || '';
      return data;
    } catch (error) {
      state.error = error.message;
      return state.data;
    } finally {
      state.pending = null;
      if (entries.get(build) === state) notify(build);
    }
  })();
  notify(build);
  return state.pending;
}
