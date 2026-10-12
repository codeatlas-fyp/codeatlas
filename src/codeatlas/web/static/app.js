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
