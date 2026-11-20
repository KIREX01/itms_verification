// Compiles every inline on*="..." handler found in JS template strings, the way the browser would.
// Usage: node check_handlers.js <file.js> [...]
const fs = require("fs");
let totalBad = 0;
for (const f of process.argv.slice(2)) {
    const src = fs.readFileSync(f, "utf8");
    let ok = 0, bad = 0;
    for (const m of src.matchAll(/on(?:click|error|load|change|input|keydown|submit)="([^"]*)"/g)) {
        let body = m[1]
            .replace(/\$\{[^}]*\}/g, "1")   // interpolated values -> a harmless literal
            .replace(/\\'/g, "'")           // inside a template literal, \' becomes a bare '
            .replace(/&quot;/g, '"').replace(/&#0?39;|&apos;/g, "'").replace(/&amp;/g, "&");
        try { new Function("event", body); ok++; }
        catch (e) { bad++; console.log(`${f}: BROKEN (${e.message}): ${body.slice(0, 110)}`); }
    }
    console.log(`${f}: ${ok} ok, ${bad} broken`);
    totalBad += bad;
}
process.exit(totalBad ? 1 : 0);
