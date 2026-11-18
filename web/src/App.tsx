import { WORKFLOW_URL } from './api';

// Deliberately bare. The review screens, draft editor, cost panel and
// keyboard-driven triage are M9 (AGENTS.md §11); building them now would be
// working ahead of the current milestone.
export default function App() {
  return (
    <main>
      <h1>Job Searcher</h1>
      <p>Foundations are up. The dashboard arrives in M9.</p>
      <dl>
        <dt>workflow API</dt>
        <dd>{WORKFLOW_URL}</dd>
      </dl>
    </main>
  );
}
