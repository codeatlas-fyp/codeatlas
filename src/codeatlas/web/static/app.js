// CodeAtlas pages: small progressive enhancements. Every page works without this file;
// it only shows, hides or highlights content the server already rendered. It never fetches.
"use strict";

document.addEventListener("DOMContentLoaded", () => {
  // Copy buttons: <button data-copy="text">.
  document.querySelectorAll("[data-copy]").forEach((button) => {
    button.addEventListener("click", () => {
      const text = button.getAttribute("data-copy") || "";
      const done = () => {
        const label = button.textContent;
        button.textContent = "Copied";
        setTimeout(() => { button.textContent = label; }, 1500);
      };
      if (navigator.clipboard) navigator.clipboard.writeText(text).then(done, () => {});
    });
  });

  // Check filter tabs on the case page: buttons [data-filter] inside [data-filter-group=x]
  // show only children of [data-filter-target=x] whose data-group matches.
  document.querySelectorAll("[data-filter-group]").forEach((group) => {
    const name = group.getAttribute("data-filter-group");
    const target = document.querySelector(`[data-filter-target="${name}"]`);
    if (!target) return;
    group.querySelectorAll("[data-filter]").forEach((button) => {
      button.addEventListener("click", () => {
        const wanted = button.getAttribute("data-filter");
        group.querySelectorAll("[data-filter]").forEach((b) => {
          const on = b === button;
          b.classList.toggle("is-active", on);
          b.setAttribute("aria-pressed", on ? "true" : "false");
        });
        target.querySelectorAll("[data-group]").forEach((item) => {
          item.hidden = wanted !== "all" && item.getAttribute("data-group") !== wanted;
        });
      });
    });
  });

  // Expand / collapse every <details> in a list.
  document.querySelectorAll("[data-toggle-all]").forEach((button) => {
    const name = button.getAttribute("data-toggle-all");
    const target = document.querySelector(`[data-filter-target="${name}"]`);
    if (!target) return;
    const sync = () => {
      const all = target.querySelectorAll("details");
      const anyClosed = Array.from(all).some((d) => !d.open);
      button.textContent = anyClosed ? "Expand all" : "Collapse all";
      return anyClosed;
    };
    sync();
    target.addEventListener("toggle", sync, true);
    button.addEventListener("click", () => {
      const open = sync();
      target.querySelectorAll("details").forEach((d) => { d.open = open; });
      sync();
    });
  });

  // Timeline: selecting a point highlights its row in the event table, and back.
  document.querySelectorAll("[data-timeline]").forEach((timeline) => {
    const name = timeline.getAttribute("data-timeline");
    const table = document.querySelector(`[data-event-table="${name}"]`);
    const select = (index, scroll) => {
      timeline.querySelectorAll("[data-event]").forEach((col) => {
        const on = col.getAttribute("data-event") === index;
        col.classList.toggle("is-selected", on);
        const mark = col.querySelector("[data-select]");
        if (mark) mark.setAttribute("aria-pressed", on ? "true" : "false");
      });
      if (!table) return;
      table.querySelectorAll("[data-row]").forEach((row) => {
        const on = row.getAttribute("data-row") === index;
        row.classList.toggle("is-selected", on);
        if (on && scroll) row.scrollIntoView({ block: "nearest", behavior: "smooth" });
      });
    };
    timeline.querySelectorAll("[data-select]").forEach((mark) => {
      mark.addEventListener("click", () => select(mark.getAttribute("data-select"), true));
    });
    if (table) {
      table.querySelectorAll("[data-row]").forEach((row) => {
        row.addEventListener("click", () => select(row.getAttribute("data-row"), false));
      });
    }
    const problem = timeline.querySelector(".is-problem [data-select]");
    if (problem) select(problem.getAttribute("data-select"), false);

    const toggle = document.querySelector(`[data-problems-toggle="${name}"]`);
    if (toggle) {
      toggle.addEventListener("change", () => {
        timeline.classList.toggle("is-focus", toggle.checked);
        if (!table) return;
        table.querySelectorAll("[data-row]").forEach((row) => {
          row.hidden = toggle.checked && !row.classList.contains("is-problem");
        });
      });
    }
  });
});

document.addEventListener("DOMContentLoaded", () => {
  const dashboard = document.querySelector("[data-dashboard]");
  if (!dashboard) return;

  const mode = dashboard.dataset.sourceMode || "live";
  const jiraSelect = dashboard.querySelector('[name="jira_project"]');
  const repoSelect = dashboard.querySelector('[name="github_repository"]');
  const prSelect = dashboard.querySelector('[name="pull_request"]');
  const evaluateButton = dashboard.querySelector("[data-evaluate]");
  const history = dashboard.querySelector("[data-history]");
  const progress = dashboard.querySelector("[data-progress]");
  let projectSaved = false;
  let activePoll = null;

  const node = (tag, text, className) => {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  };
  const request = async (path, options = {}) => {
    const response = await fetch(path, {
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const detail = data.detail;
      throw new Error(typeof detail === "object" ? detail.message : (detail || `Request failed (${response.status})`));
    }
    return data;
  };
  const setOptions = (select, items, valueOf, labelOf, selected = "") => {
    select.replaceChildren(new Option("Select…", ""));
    items.forEach((item) => select.add(new Option(labelOf(item), valueOf(item), false, valueOf(item) === selected)));
  };
  const message = (element, text, bad = false) => {
    element.textContent = text;
    element.classList.toggle("fail-text", bad);
    element.classList.toggle("pass-text", !bad && Boolean(text));
  };

  const loadCatalogs = async (savedProject = null) => {
    try {
      projectSaved = Boolean(savedProject);
      const [jira, repos] = await Promise.all([
        request("/api/jira/projects"),
        request("/api/github/repositories"),
      ]);
      setOptions(jiraSelect, jira, (item) => item.key, (item) => `${item.name} (${item.key})`, savedProject?.jira_project || "");
      setOptions(repoSelect, repos, (item) => item.full_name, (item) => item.full_name, savedProject?.github_repository || "");
      if (savedProject) await loadPullRequests();
    } catch (error) {
      message(dashboard.querySelector("[data-project-message]"), error.message, true);
    }
  };

  const loadSetup = async () => {
    try {
      const setup = await request("/api/setup");
      ["jira", "github"].forEach((provider) => {
        const state = dashboard.querySelector(`[data-connection-state="${provider}"]`);
        if (state) state.textContent = setup[provider].connected
          ? `${setup[provider].mode === "fixture" ? "Fixture mode" : "Connected"}: ${setup[provider].display_name || provider}`
          : "Not connected";
      });
      await loadCatalogs(setup.project);
      const jobs = await request("/api/evaluations");
      renderHistory(jobs);
      const pending = jobs.find((job) => ["queued", "running"].includes(job.status));
      if (pending) poll(pending.id);
    } catch (error) {
      message(dashboard.querySelector("[data-project-message]"), error.message, true);
    }
  };

  dashboard.querySelectorAll("form[data-connection]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const provider = form.dataset.connection;
      const fields = Object.fromEntries(new FormData(form).entries());
      const tokenField = provider === "jira" ? "api_token" : "personal_access_token";
      const feedback = form.querySelector("[data-message]");
      const button = form.querySelector("button[type=submit]");
      button.disabled = true;
      message(feedback, "Validating credentials…");
      try {
        await request(`/api/connections/${provider}/validate`, { method: "POST", body: JSON.stringify(fields) });
        await request(`/api/connections/${provider}`, { method: "POST", body: JSON.stringify(fields) });
        form.reset();
        message(feedback, "Connection validated and encrypted for storage.");
        await loadSetup();
      } catch (error) {
        message(feedback, error.message, true);
      } finally {
        button.disabled = false;
      }
    });
  });

  dashboard.querySelector("[data-save-project]").addEventListener("click", async () => {
    const feedback = dashboard.querySelector("[data-project-message]");
    try {
      const saved = await request("/api/project-config", {
        method: "PUT",
        body: JSON.stringify({ jira_project: jiraSelect.value, github_repository: repoSelect.value }),
      });
      projectSaved = true;
      message(feedback, `Saved ${saved.jira_project} and ${saved.github_repository}.`);
      await loadPullRequests();
    } catch (error) {
      message(feedback, error.message, true);
    }
  });

  const loadPullRequests = async () => {
    const repository = repoSelect.value;
    if (!repository || (!projectSaved && !repoSelect.selectedOptions[0]?.defaultSelected)) return;
    try {
      const [owner, name] = repository.split("/");
      const prs = await request(`/api/github/repositories/${encodeURIComponent(owner)}/${encodeURIComponent(name)}/pull-requests`);
      setOptions(prSelect, prs, (item) => String(item.number), (item) => `#${item.number} · ${item.title} · ${item.author}`);
      prSelect._items = prs;
      evaluateButton.disabled = true;
    } catch (error) {
      message(dashboard.querySelector("[data-project-message]"), error.message, true);
    }
  };

  repoSelect.addEventListener("change", () => { projectSaved = false; evaluateButton.disabled = true; });
  jiraSelect.addEventListener("change", () => { projectSaved = false; evaluateButton.disabled = true; });
  prSelect.addEventListener("change", () => {
    const selected = (prSelect._items || []).find((item) => String(item.number) === prSelect.value);
    evaluateButton.disabled = !projectSaved || !selected;
    const details = dashboard.querySelector("[data-pr-details]");
    if (selected) details.textContent = `#${selected.number} by ${selected.author} · ${selected.base_ref} ${selected.base_sha.slice(0, 12)} → ${selected.head_ref} ${selected.head_sha.slice(0, 12)}`;
    else details.textContent = "";
  });

  evaluateButton.addEventListener("click", async () => {
    evaluateButton.disabled = true;
    try {
      const created = await request("/api/evaluations", {
        method: "POST",
        body: JSON.stringify({ pull_request_number: Number(prSelect.value) }),
      });
      message(progress, "Evaluation queued. Progress is saved and can be refreshed.");
      await poll(created.id);
    } catch (error) {
      message(progress, error.message, true);
      evaluateButton.disabled = false;
    }
  });

  async function poll(id) {
    if (activePoll === id) return;
    activePoll = id;
    let terminal = false;
    while (!terminal) {
      try {
        const job = await request(`/api/evaluations/${encodeURIComponent(id)}`);
        renderProgress(job);
        terminal = ["succeeded", "failed"].includes(job.status);
        if (terminal && job.status === "succeeded") await showResult(id);
        const jobs = await request("/api/evaluations");
        renderHistory(jobs);
      } catch (error) {
        message(progress, error.message, true);
        terminal = true;
      }
      if (!terminal) await new Promise((resolve) => setTimeout(resolve, 1500));
    }
    activePoll = null;
    evaluateButton.disabled = !projectSaved || !prSelect.value;
  }

  function renderProgress(job) {
    const box = node("div", undefined, "job-progress");
    box.append(node("strong", `${job.status.toUpperCase()} · ${job.stage}`));
    (job.events || []).forEach((event) => {
      box.append(node("p", `${event.at} · ${event.message}`, "small muted"));
    });
    if (job.error) box.append(node("p", job.error, "fail-text"));
    progress.replaceChildren(box);
  }

  function renderHistory(jobs) {
    history.replaceChildren();
    if (!jobs.length) {
      history.append(node("p", "No evaluations yet.", "small muted"));
      return;
    }
    jobs.forEach((job) => {
      const row = node("div", undefined, "history-row");
      const title = node("strong", `${job.repository} · PR #${job.pull_request_number} · ${job.status.toUpperCase()}`);
      row.append(title);
      row.append(node("span", job.verdict || job.error || job.stage, "small muted"));
      row.append(node("time", new Date(job.created_at).toLocaleString(), "small muted"));
      if (job.status === "succeeded") {
        const open = node("a", "Open result");
        open.href = `/setup?evaluation=${encodeURIComponent(job.id)}`;
        open.addEventListener("click", (event) => { event.preventDefault(); showResult(job.id); });
        row.append(open);
      }
      history.append(row);
    });
  }

  async function showResult(id) {
    try {
      const result = await request(`/api/evaluations/${encodeURIComponent(id)}/case`);
      const body = dashboard.querySelector("[data-result-body]");
      body.replaceChildren();
      body.append(node("h3", `Verdict: ${result.verdict.value}`, `verdict-${result.verdict.value.toLowerCase()}`));
      body.append(node("p", `PR #${result.change.pr_number} · ${result.change.repo} · bundle ${result.bundle_hash}`));
      const overview = node("ul");
      result.verdict.results.forEach((check) => {
        const line = node("li", `${check.case} · ${check.outcome} · ${check.message}`);
        if (check.evidence_ids?.length) line.append(node("small", ` Evidence: ${check.evidence_ids.join(", ")}`));
        overview.append(line);
      });
      body.append(overview);
      if (result.collection_notes.length) {
        body.append(node("h3", "Collection notes"));
        result.collection_notes.forEach((note) => body.append(node("p", note, "small muted")));
      }
      const links = node("p");
      const caseLink = node("a", "Inspect evidence and provenance");
      caseLink.href = `/cases/${result.bundle_hash}`;
      const replayLink = node("a", "Replay this evaluation");
      replayLink.href = `/cases/${result.bundle_hash}/replay`;
      links.append(caseLink, document.createTextNode(" · "), replayLink);
      body.append(links);
      dashboard.querySelector("[data-result]").hidden = false;
    } catch (error) {
      message(progress, error.message, true);
    }
  }

  const queryId = new URLSearchParams(window.location.search).get("evaluation");
  loadSetup().then(() => { if (queryId) showResult(queryId); });
});
