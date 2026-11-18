import test from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { App, api, findingLabel } from "../.test-build/main.js";

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
