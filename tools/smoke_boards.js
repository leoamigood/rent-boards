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
  try {
    w.eval(data);
    for (const m of html.matchAll(/<script>([\s\S]*?)<\/script>/g)) w.eval(m[1]);
  } catch (e) {
    errors.push(`${e.name}: ${e.message}`);
  }
  const doc = w.document;
  const rows = doc.querySelectorAll("#rows .row").length;
  const tiles = doc.querySelectorAll("#tiles .tile").length;
  const ok = !errors.length && rows > 0 && tiles > 0;
  if (!ok) failed++;
  console.log(`${ok ? "ok  " : "FAIL"} ${dir.padEnd(14)} listings=${String(rows).padStart(3)} `
            + `tiles=${tiles} chips=${doc.querySelectorAll(".chip").length}`);
  errors.forEach((e) => console.log(`       ${e}`));
}
process.exit(failed ? 1 : 0);
