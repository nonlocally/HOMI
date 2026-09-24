// Copy the installed dependency graph, including npm/npx-hoisted packages.
// Each real package is copied once; private symlinks preserve its own resolved
// dependency versions, including cycles, without copying unrelated packages.
import { cpSync, existsSync, mkdirSync, readFileSync, realpathSync, symlinkSync } from "node:fs";
import { createHash } from "node:crypto";
import { createRequire } from "node:module";
import path from "node:path";

const manifest = (root) => JSON.parse(readFileSync(path.join(root, "package.json"), "utf8"));
const resolvePackage = (from, name) => {
  const dirs = createRequire(path.join(from, "package.json")).resolve.paths(name) || [];
  for (const dir of dirs) {
    const candidate = path.join(dir, name);
    if (existsSync(path.join(candidate, "package.json"))) return realpathSync(candidate);
  }
  return null;
};

export const hasRuntimeDependencies = (root, { localOnly = false } = {}) => {
  const modules = path.join(root, "node_modules");
  if (localOnly && !existsSync(modules)) return false;
  const boundary = localOnly ? realpathSync(modules) + path.sep : null;
  return Object.keys(manifest(root).dependencies || {}).every((name) => {
    const resolved = resolvePackage(root, name);
    return resolved && (!localOnly || resolved.startsWith(boundary));
  });
};

export function copyRuntimeDependencies(root, destination) {
  const store = path.join(destination, "node_modules", ".communicate-deps");
  const copied = new Map();
  function linkDependencies(source, dest) {
    const pkg = manifest(source);
    const required = pkg.dependencies || {};
    const optional = { ...(pkg.peerDependencies || {}), ...(pkg.optionalDependencies || {}) };
    for (const name of Object.keys({ ...optional, ...required })) {
      const resolved = resolvePackage(source, name);
      if (!resolved) {
        if (name in required && !(name in (pkg.optionalDependencies || {})))
          throw new Error(`MCP dependency ${name} is missing; reinstall the package before setup`);
        continue;
      }
      let target = copied.get(resolved);
      if (!target) {
        target = path.join(store, createHash("sha256").update(resolved).digest("hex").slice(0, 20));
        copied.set(resolved, target);
        cpSync(resolved, target, { recursive: true, dereference: true,
          filter: (entry) => entry !== path.join(resolved, "node_modules") });
        linkDependencies(resolved, target);
      }
      const alias = path.join(dest, "node_modules", name);
      mkdirSync(path.dirname(alias), { recursive: true });
      symlinkSync(path.relative(path.dirname(alias), target), alias, "dir");
    }
  }
  linkDependencies(root, destination);
}
