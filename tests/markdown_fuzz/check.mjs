// Renders each case with marked (the Markdown parser JupyterLab uses) and looks in the DOM for anything
// that could run script. Usage: node check.mjs cases.json   (with marked and jsdom installed, e.g.
// npm install marked@15 jsdom@24, and NODE_PATH pointing at that node_modules folder)
import { createRequire } from "module";
import fs from "fs";
const require = createRequire(process.env.NODE_PATH ? process.env.NODE_PATH.split(":")[0] + "/" : import.meta.url);
const { marked } = require("marked");
const { JSDOM } = require("jsdom");
const cases = JSON.parse(fs.readFileSync(process.argv[2] || "cases.json"));
marked.setOptions({ gfm: true, breaks: false });
const BAD_TAGS = new Set(["SCRIPT", "IFRAME", "OBJECT", "EMBED", "STYLE", "LINK", "META", "FORM", "INPUT", "SVG", "MATH", "BASE", "FRAME", "IMG", "VIDEO", "AUDIO", "SOURCE"]);
let bad = 0, rawBad = 0;
for (const c of cases) {
  for (const which of ["raw", "safe"]) {
    const html = marked.parse(c[which]);
    const dom = new JSDOM("<body>" + html + "</body>");
    const problems = [];
    for (const el of dom.window.document.body.querySelectorAll("*")) {
      if (BAD_TAGS.has(el.tagName.toUpperCase())) {
        // images with an http(s) src are fine
        // an image is harmless unless it has handlers or a src with a scheme other than http(s)/mailto
        if (el.tagName === "IMG" && [...el.attributes].every(a => !/^on/i.test(a.name))) { const m = (el.getAttribute("src") || "").replace(/[\u0000-\u0020]/g, "").match(/^([a-z][a-z0-9+.\-]*):/i); if (!m || ["http", "https", "mailto"].includes(m[1].toLowerCase())) continue; }
        problems.push("tag " + el.tagName);
      }
      for (const a of el.attributes) {
        if (/^on/i.test(a.name)) problems.push("attr " + a.name);
        if (["href", "src", "xlink:href", "data", "action", "formaction"].includes(a.name.toLowerCase())) {
          const v = a.value.replace(/[\u0000- ]/g, "").toLowerCase();
          const m = v.match(/^([a-z][a-z0-9+.\-]*):/);
          if (m && !["http", "https", "mailto"].includes(m[1])) problems.push(a.name + "=" + a.value);
        }
        if (a.name.toLowerCase() === "style") problems.push("style attr");
      }
    }
    if (problems.length) {
      if (which === "safe") { bad++; if (bad < 15) console.log("UNSAFE:", JSON.stringify(c.raw), "\n  safe:", JSON.stringify(c.safe), "\n  html:", html.slice(0, 300), "\n  ", problems.join(", ")); }
      else rawBad++;
    }
  }
}
console.log(`cases ${cases.length}: raw unsafe ${rawBad}, sanitised unsafe ${bad}`);
process.exit(bad ? 1 : 0);
