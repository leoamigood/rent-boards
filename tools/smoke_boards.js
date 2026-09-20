/*
 * Load each board the way a browser would and fail if anything throws or the
 * listing table comes up empty.
 *
 * `node --check` only parses; it cannot see a ReferenceError thrown at run
 * time. One of those aborted render() midway and shipped a board with charts,
 * a map and no listings at all — this catches that class of bug.
 *
 *   npm install jsdom            (once)
 *   node tools/smoke_boards.js dashboard dashboard-vn dashboard-dn
 */
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");
const attachLeaflet = require("./leaflet_stub.js");

const boards = process.argv.slice(2);
if (!boards.length) {
  console.error("usage: node tools/smoke_boards.js <board-dir> [...]");
  process.exit(2);
}

let failed = 0;
for (const dir of boards) {
  const html = fs.readFileSync(path.join(dir, "index.html"), "utf8");
  const data = fs.readFileSync(path.join(dir, "data.js"), "utf8");
  const errors = [];
  const dom = new JSDOM(`<!doctype html><html><head></head><body>${html}</body></html>`,
                        { runScripts: "outside-only", pretendToBeVisual: true });
  const w = dom.window;
  w.onerror = (m) => errors.push(String(m));
  let scrolls = 0;
  w.Element.prototype.scrollIntoView = function () { scrolls++; };
  w.matchMedia = () => ({ matches: false, addEventListener() {}, addListener() {} });
  // The hosted build runs Leaflet; jsdom has none, so without a stand-in the
  // whole map path goes untested — and it is where the last two bugs lived.
  const lcalls = attachLeaflet(w);
  try {
    w.eval(data);
    for (const m of html.matchAll(/<script>([\s\S]*?)<\/script>/g)) w.eval(m[1]);
  } catch (e) {
    errors.push(`${e.name}: ${e.message}`);
  }
  const doc = w.document;

  // Moving a filter must not drag the page around. render() runs on every
  // slider step and keystroke, and a stale "scroll to this row" in there once
  // yanked the view down to the listings on every tick of a drag.
  const dot = doc.querySelector("#map circle[style]");
  if (dot) dot.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  const settled = scrolls;
  const slider = doc.getElementById("maxp");
  if (slider) {
    for (let i = 0; i < 10; i++) {
      slider.value = String(Number(slider.value) - Number(slider.step || 1));
      slider.dispatchEvent(new w.Event("input", { bubbles: true }));
    }
  }
  const q = doc.getElementById("q");
  if (q) { q.value = "a"; q.dispatchEvent(new w.Event("input", { bubbles: true })); }
  if (scrolls > settled) {
    errors.push(`page scrolled ${scrolls - settled}x while filtering`);
  }

  // Selecting a listing must visibly mark its marker. Radius is the trap:
  // Leaflet only redraws it via setRadius, so setStyle({radius}) is silent.
  let marked = null;
  if (lcalls.markers.length) {
    const listings = w.LISTINGS || [];
    const row = [...doc.querySelectorAll("#rows .row")].find((b) => {
      const r = listings.find((x) => String(x.id) === String(b.dataset.id));
      return r && r.lat && r.lon;
    });
    if (row) {
      row.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
      marked = lcalls.markers.filter((m) => m.drawnRadius > 5).length;
      if (marked !== 1) errors.push(`selecting a listing marked ${marked} markers, want 1`);
      row.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
      const left = lcalls.markers.filter((m) => m.drawnRadius > 5).length;
      if (left !== 0) errors.push(`${left} markers stayed marked after deselect`);
    }
  }

  const rows = doc.querySelectorAll("#rows .row").length;
  const tiles = doc.querySelectorAll("#tiles .tile").length;
  const ok = !errors.length && rows > 0 && tiles > 0;
  if (!ok) failed++;
  console.log(`${ok ? "ok  " : "FAIL"} ${dir.padEnd(14)} listings=${String(rows).padStart(3)} `
            + `tiles=${tiles} chips=${String(doc.querySelectorAll(".chip").length).padStart(2)} `
            + `scrolls-while-filtering=${scrolls - settled}`
            + (marked === null ? "" : ` map-highlight=ok`));
  errors.forEach((e) => console.log(`       ${e}`));
}
process.exit(failed ? 1 : 0);
