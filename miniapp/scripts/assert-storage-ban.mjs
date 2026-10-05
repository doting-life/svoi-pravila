import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const fixture = path.join(root, "eslint-fixtures", "storage-ban.js");

const result = spawnSync("pnpm", ["exec", "eslint", "--no-ignore", fixture], {
    cwd: root,
    encoding: "utf8",
});

if (result.status === 0) {
    console.error("Expected ESLint to fail on storage-ban fixture, but it passed.");
    process.exit(1);
}

const output = `${result.stdout}\n${result.stderr}`;
if (!output.includes("no-restricted-globals") && !output.includes("no-restricted-properties")) {
    console.error("ESLint failed, but not with the storage ban rules:");
    console.error(output);
    process.exit(1);
}

console.log("storage-ban fixture correctly fails ESLint");
