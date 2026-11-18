import { useCallback, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./style.css";

type Project = { jira_project: string; github_repository: string };
type Setup = { project: Project | null; source_mode: string };
type JiraProject = { key: string; name: string };
type Repository = { full_name: string };
type PullRequest = { number: number; title: string; author: string };
type Job = { id: string; repository: string; pull_request_number: number; status: string; stage: string; created_at: string; verdict: string | null; error: string | null; events: { id: number; message: string; at: string }[] };
type Check = { case: string; outcome: string; severity: string; message: string; evidence_ids: string[] };
type Result = { bundle_hash: string; verdict: { value: string; results: Check[] }; bundle: { policy_source: string | null }; collection_notes: string[] };
type Graph = { status: string; limitations: string[]; nodes: unknown[]; edges: { id: string; src: string; dst: string; kind: string; confident: boolean | null }[] };
type Semantic = { criterion_id: string; status: string; reason: string; candidates: { entity_id: string; rank: number; accepted: boolean | null }[] };
type Replay = { match: boolean; refused: boolean; message: string; limitations: string[] };
type ComparisonSide = { evaluation_id: string; repo: string; pr_number: number; verdict: string; policy_source: string | null; policy_hash: string; base_sha: string; head_sha: string; cutoff: string; ruleset_version: string; graph: { entities: number; edges: number }; semantic: { criteria: number; candidates: number } };
type Comparison = { before: ComparisonSide; after: ComparisonSide; directly_comparable: boolean; limitations: string[]; graph_note: string; semantic_note: string; findings: { case: string; change: string; before: Check | null; after: Check | null }[] };

export function findingLabel(change: string): string {
  return change.replaceAll("_", " ");
}

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, { ...options, headers: { "Content-Type": "application/json", ...options?.headers } });
  if (!response.ok) {
    const data: unknown = await response.json().catch(() => null);
    let detail: unknown = data && typeof data === "object" && "detail" in data ? data.detail : null;
    if (data && typeof data === "object" && "error" in data && data.error && typeof data.error === "object" && "message" in data.error) detail = data.error.message;
    const message = typeof detail === "string" ? detail : detail && typeof detail === "object" && "message" in detail ? String(detail.message) : `Request failed (${response.status}).`;
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

export function App() {
  const [setup, setSetup] = useState<Setup | null>(null);
  const [projects, setProjects] = useState<JiraProject[]>([]);
  const [repositories, setRepositories] = useState<Repository[]>([]);
  const [project, setProject] = useState("");
  const [repository, setRepository] = useState("");
  const [saved, setSaved] = useState(false);
  const [prs, setPrs] = useState<PullRequest[]>([]);
  const [pr, setPr] = useState("");
  const [jobs, setJobs] = useState<Job[]>([]);
  const [selected, setSelected] = useState("");
  const [result, setResult] = useState<Result | null>(null);
  const [graph, setGraph] = useState<Graph | null>(null);
  const [semantic, setSemantic] = useState<Semantic[] | null>(null);
  const [replay, setReplay] = useState<Replay | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [before, setBefore] = useState("");
  const [after, setAfter] = useState("");
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [comparing, setComparing] = useState(false);

  const loadHistory = useCallback(async () => setJobs(await api<Job[]>("/api/evaluations")), []);
  useEffect(() => {
    async function load() {
      try {
        const state = await api<Setup>("/api/setup");
        setSetup(state);
        if (state.project) { setProject(state.project.jira_project); setRepository(state.project.github_repository); setSaved(true); }
        await loadHistory();
        const [jira, repos] = await Promise.all([api<JiraProject[]>("/api/jira/projects"), api<Repository[]>("/api/github/repositories")]);
        setProjects(jira); setRepositories(repos);
      } catch (e) { setError(e instanceof Error ? e.message : "Could not load setup."); }
    }
    void load();
  }, [loadHistory]);
  useEffect(() => {
    if (!saved || !repository.includes("/")) { setPrs([]); return; }
    const [owner, name] = repository.split("/");
    api<PullRequest[]>(`/api/github/repositories/${encodeURIComponent(owner)}/${encodeURIComponent(name)}/pull-requests`)
      .then(setPrs).catch((e: Error) => setError(e.message));
  }, [repository, saved]);
  useEffect(() => {
    if (!selected) return;
    let stopped = false;
    async function poll() {
      try {
        const job = await api<Job>(`/api/evaluations/${encodeURIComponent(selected)}`);
        await loadHistory();
        if (job.status === "succeeded") {
          const data = await api<Result>(`/api/evaluations/${encodeURIComponent(selected)}/case`);
          const hash = encodeURIComponent(data.bundle_hash);
          if (!stopped) setResult(data);
          const [g, s, r] = await Promise.allSettled([api<Graph>(`/api/cases/${hash}/graph`), api<Semantic[]>(`/api/cases/${hash}/semantic-runs`), api<Replay>(`/api/cases/${hash}/replay`)]);
          if (!stopped) {
            if (g.status === "fulfilled") setGraph(g.value);
            if (s.status === "fulfilled") setSemantic(s.value);
            if (r.status === "fulfilled") setReplay(r.value);
            if ([g, s, r].some((item) => item.status === "rejected")) setError("Some evidence views could not be loaded. The verdict remains available.");
          }
        } else if (job.status !== "failed" && !stopped) window.setTimeout(() => { if (!stopped) void poll(); }, 1800);
      } catch (e) { if (!stopped) setError(e instanceof Error ? e.message : "Could not load evaluation."); }
    }
    setResult(null); setGraph(null); setSemantic(null); setReplay(null); void poll();
    return () => { stopped = true; };
  }, [selected, loadHistory]);

  async function saveProject() {
    setBusy(true); setError("");
    try { await api<Project>("/api/project-config", { method: "PUT", body: JSON.stringify({ jira_project: project, github_repository: repository }) }); setSaved(true); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not save project."); }
    finally { setBusy(false); }
  }
  async function submit() {
    setBusy(true); setError("");
    try { const job = await api<{ id: string }>("/api/evaluations", { method: "POST", body: JSON.stringify({ pull_request_number: Number(pr) }) }); setSelected(job.id); await loadHistory(); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not submit evaluation."); }
    finally { setBusy(false); }
  }
  async function compare() {
    setComparing(true); setComparison(null); setError("");
    try { setComparison(await api<Comparison>(`/api/evaluations/compare?before=${encodeURIComponent(before)}&after=${encodeURIComponent(after)}`)); }
    catch (e) { setError(e instanceof Error ? e.message : "Could not compare evaluations."); }
    finally { setComparing(false); }
  }
  const active = jobs.find((job) => job.id === selected);
  const completed = jobs.filter((job) => job.status === "succeeded");
  return <main className="app">
    <header><div><p className="eyebrow">CODEATLAS / WORKSPACE</p><h1>Evaluations</h1><p>Review changes against frozen evidence and the effective policy.</p></div><a href="/setup">Connections and classic view ↗</a></header>
    {error && <p className="notice error" role="alert">{error}</p>}
    <section className="card"><div className="section-head"><div><h2>Start an evaluation</h2><p>Select a configured Jira project and GitHub repository.</p></div><span className="tag">{setup ? `${setup.source_mode} mode` : "Loading"}</span></div>
      <div className="form-grid"><label>Jira project<select value={project} onChange={(e) => { setProject(e.target.value); setSaved(false); }}><option value="">Select project</option>{projects.map((p) => <option key={p.key} value={p.key}>{p.name} ({p.key})</option>)}</select></label>
      <label>Repository<select value={repository} onChange={(e) => { setRepository(e.target.value); setSaved(false); setPr(""); }}><option value="">Select repository</option>{repositories.map((r) => <option key={r.full_name}>{r.full_name}</option>)}</select></label>
      <button disabled={busy || !project || !repository || saved} onClick={() => void saveProject()}>Save selection</button></div>
      <div className="form-grid"><label>Pull request<select value={pr} disabled={!saved} onChange={(e) => setPr(e.target.value)}><option value="">{saved ? "Select pull request" : "Save selection first"}</option>{prs.map((p) => <option key={p.number} value={p.number}>#{p.number} · {p.title} · {p.author}</option>)}</select></label><button disabled={busy || !saved || !pr} onClick={() => void submit()}>Evaluate</button></div>
    </section>
    <div className="columns"><section className="card"><div className="section-head"><h2>History</h2><button className="secondary" onClick={() => void loadHistory().catch((e: Error) => setError(e.message))}>Refresh</button></div>{jobs.length === 0 ? <p>No evaluations yet.</p> : <ul className="history">{jobs.map((job) => <li key={job.id}><button className="history-button" onClick={() => setSelected(job.id)}><strong>{job.repository} · #{job.pull_request_number}</strong><span>{job.status} · {job.verdict || job.stage}</span></button></li>)}</ul>}</section>
      <section className="card" aria-live="polite"><h2>Evaluation detail</h2>{!selected ? <p>Select an evaluation to inspect its progress and evidence.</p> : <><p><strong>{active?.status || "Loading"}</strong> · {active?.stage}</p>{active?.error && <p className="error">{active.error}</p>}{!!active?.events?.length && <details><summary>Stage history ({active.events.length})</summary><ol>{active.events.map((event) => <li key={event.id}>{event.message} <small>{event.at}</small></li>)}</ol></details>}</>}</section></div>
    <section className="card"><h2>Compare evaluations</h2><p>Choose an earlier and a later completed evaluation. Missing evidence is not treated as a resolution.</p>
      {completed.length < 2 ? <p>At least two completed evaluations are needed.</p> : <div className="form-grid">
        <label>Before<select value={before} onChange={(e) => { setBefore(e.target.value); setComparison(null); }}><option value="">Select evaluation</option>{completed.map((job) => <option key={job.id} value={job.id}>{job.repository} · #{job.pull_request_number} · {job.verdict} · {job.created_at} · {job.id.slice(0, 8)}</option>)}</select></label>
        <label>After<select value={after} onChange={(e) => { setAfter(e.target.value); setComparison(null); }}><option value="">Select evaluation</option>{completed.map((job) => <option key={job.id} value={job.id}>{job.repository} · #{job.pull_request_number} · {job.verdict} · {job.created_at} · {job.id.slice(0, 8)}</option>)}</select></label>
        <button disabled={!before || !after || before === after || comparing} onClick={() => void compare()}>{comparing ? "Comparing…" : "Compare"}</button></div>}
      {comparison && <div aria-live="polite"><p className="notice">{comparison.directly_comparable ? "Matching recorded context." : "Not directly equivalent: " + comparison.limitations.join(" ")}</p>
        <div className="columns">{[comparison.before, comparison.after].map((side, index) => <div key={index}><h3>{index ? "After" : "Before"}: {side.verdict}</h3><p>{side.repo} · PR #{side.pr_number}</p><p>Policy: {side.policy_source || "source not recorded"} · {side.policy_hash.slice(0, 12)}</p><p>Base {side.base_sha.slice(0, 12)} · head {side.head_sha.slice(0, 12)}</p><p>Cutoff {side.cutoff} · {side.ruleset_version}</p><p>Graph: {side.graph.entities} recorded entities, {side.graph.edges} edges. Semantic: {side.semantic.criteria} criteria, {side.semantic.candidates} candidates.</p><button className="secondary" onClick={() => setSelected(side.evaluation_id)}>Open {index ? "after" : "before"} evaluation</button></div>)}</div>
        <h3>Findings</h3><ul className="checks">{comparison.findings.map((finding) => <li key={finding.case}><strong>{finding.case} · {findingLabel(finding.change)}</strong><p>Before: {finding.before ? `${finding.before.outcome} · ${finding.before.severity} (${finding.before.message})` : "not recorded"}</p><p>After: {finding.after ? `${finding.after.outcome} · ${finding.after.severity} (${finding.after.message})` : "not recorded"}</p></li>)}</ul><p>{comparison.graph_note}</p><p>{comparison.semantic_note}</p></div>}
    </section>
    {result && <section className="card"><div className="section-head"><div><p className="eyebrow">FROZEN BUNDLE</p><h2>Verdict: {result.verdict.value}</h2></div><a href={`/cases/${result.bundle_hash}`}>Open detailed evidence ↗</a></div><p>Policy source: {result.bundle.policy_source || "Not recorded"}</p><p>Replay: {replay ? (replay.refused ? "Refused" : replay.match ? "Match" : "Mismatch") : "Not loaded"}</p>{replay?.limitations.map((note) => <p key={note}>{note}</p>)}
      <ul className="checks">{result.verdict.results.map((check) => <li key={check.case}><strong>{check.case} · {check.outcome}</strong><p>{check.message}</p><small>{check.evidence_ids.join(", ") || "No evidence references"}</small></li>)}</ul>
      {result.collection_notes.map((note) => <p key={note} className="notice">{note}</p>)}
      <h3>Graph evidence</h3><p>{graph ? `${graph.status} · ${graph.nodes.length} entities · ${graph.edges.length} edges` : "Not loaded"}</p>{graph?.limitations.map((note) => <p key={note}>{note}</p>)}<ul>{graph?.edges.slice(0, 25).map((edge) => <li key={edge.id}>{edge.kind}: {edge.src} → {edge.dst} · {edge.confident === null ? "confidence unavailable" : edge.confident ? "confident static resolution" : "uncertain static resolution"}</li>)}</ul>{graph && graph.edges.length > 25 && <p>Showing 25 of {graph.edges.length} edges.</p>}
      <h3>Semantic suggestions</h3>{semantic === null && <p>Not loaded.</p>}{semantic?.length === 0 && <p>No criteria are available.</p>}{semantic?.map((run) => <div key={run.criterion_id}><strong>{run.criterion_id} · {run.status}</strong><p>{run.reason}</p><ul>{run.candidates.map((candidate) => <li key={`${candidate.rank}-${candidate.entity_id}`}>#{candidate.rank} {candidate.entity_id} · {candidate.accepted === null ? "acceptance not recorded" : candidate.accepted ? "accepted" : "not accepted"}</li>)}</ul></div>)}
    </section>}
  </main>;
}

if (typeof document !== "undefined") {
  const root = document.getElementById("root");
  if (root) createRoot(root).render(<App />);
}
