// PaperBridge 扩展构建：共享源码 → dist/chrome 与 dist/edge 两个侧载包
// 用法：node build.mjs [--suffix=名称后缀]
// 差异点（方案 §10.4）：商店元数据、扩展 key、更新地址等由各目标 manifest 覆盖

import { cpSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));
const base = JSON.parse(readFileSync(join(root, "manifest.base.json"), "utf-8"));
const suffix = process.argv.find((a) => a.startsWith("--suffix="))?.split("=")[1] || "";

const targets = ["chrome", "edge"];

for (const target of targets) {
  const manifest = { ...base };
  const overridesPath = join(root, `manifest.${target}.json`);
  try {
    Object.assign(manifest, JSON.parse(readFileSync(overridesPath, "utf-8")));
  } catch (e) {
    if (e.code !== "ENOENT") throw e;
  }
  if (suffix) manifest.name = manifest.name + " " + suffix;

  const out = join(root, "dist", target);
  rmSync(out, { recursive: true, force: true });
  mkdirSync(out, { recursive: true });
  cpSync(join(root, "src"), out, { recursive: true });
  writeFileSync(join(out, "manifest.json"), JSON.stringify(manifest, null, 2) + "\n");
  console.log(`built dist/${target} (name: ${manifest.name})`);
}

// 校验
for (const target of targets) {
  const m = JSON.parse(readFileSync(join(root, "dist", target, "manifest.json"), "utf-8"));
  if (m.manifest_version !== 3) throw new Error(`dist/${target} 不是 MV3`);
  if (m.permissions?.includes("cookies") || m.permissions?.includes("<all_urls>")) {
    throw new Error(`dist/${target} 违反最小权限原则`);
  }
}
console.log("manifest 校验通过");
