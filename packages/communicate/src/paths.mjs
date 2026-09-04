import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

export const pkgDir = fileURLToPath(new URL("..", import.meta.url));
export const packageVersion = JSON.parse(readFileSync(path.join(pkgDir, "package.json"), "utf8")).version;
// Checkout MCP must execute checkout code, even when a stale vendor/ or a
// frozen npm installation exists. Published packages only have vendor/.
const checkout = path.resolve(pkgDir, "..", "..");
export const communicateCli = existsSync(path.join(checkout, "lib", "common.sh")) &&
  existsSync(path.join(checkout, "packages", "communicate", "package.json"))
  ? path.join(checkout, "bin", "communicate")
  : path.join(pkgDir, "vendor", "bin", "communicate");
