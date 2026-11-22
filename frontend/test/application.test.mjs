import test from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { App, GraphVisualization, api, findingLabel } from "../.test-build/main.js";

test("initial view identifies the real evaluation workflow", () => {
  const html = renderToStaticMarkup(React.createElement(App));
  assert.match(html, /Start an evaluation/);
  assert.match(html, /Select an evaluation to inspect its progress and evidence/);
  assert.match(html, /Connections and classic view/);
  assert.match(html, /At least two completed evaluations are needed/);
});

test("comparison labels distinguish missing evidence and severity changes", () => {
  assert.equal(findingLabel("not_comparable"), "not comparable");
  assert.equal(findingLabel("severity_changed"), "severity changed");
});

test("graph displays recorded direction and uncertainty without inventing edges", () => {
  const graph = { status: "incomplete", limitations: [], nodes: [
    { id: "a", kind: "function", entity_id: "module.a" },
    { id: "b", kind: "function", entity_id: "module.b" },
  ], edges: [
    { id: "one", src: "a", dst: "b", kind: "CALLS", confident: false },
    { id: "missing", src: "a", dst: "unknown", kind: "CALLS", confident: true },
  ] };
  const html = renderToStaticMarkup(React.createElement(GraphVisualization, { graph }));
  assert.match(html, /Showing 2 of 2 nodes and 1 of 2 edges/);
  assert.match(html, /class="graph-edge uncertain"/);
  assert.match(html, /CALLS: a to b/);
  assert.match(html, /module\.a/);
  assert.doesNotMatch(html, /a to unknown/);
});

test("API errors expose the server's safe message", async () => {
  const previous = globalThis.fetch;
  globalThis.fetch = async () => new Response(
    JSON.stringify({ error: { message: { message: "Reconnect GitHub." } } }),
    { status: 409, headers: { "Content-Type": "application/json" } },
  );
  try {
    await assert.rejects(api("/api/evaluations"), /Reconnect GitHub/);
  } finally {
    globalThis.fetch = previous;
  }
});
