// web talks only to workflow (AGENTS.md §6). Never to agents, and never
// directly to a model provider: anything prefixed VITE_ is compiled into the
// bundle and is therefore public.
export const WORKFLOW_URL: string = import.meta.env.VITE_WORKFLOW_URL ?? 'http://localhost:8080';
