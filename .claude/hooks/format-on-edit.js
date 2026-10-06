#!/usr/bin/env node
// agent-setup-template: v1.1
// PostToolUse hook (Edit|Write|MultiEdit): 방금 편집한 파일 하나를 프로젝트 포맷터로 포맷한다. "포맷만" 담당한다 (린트·타입체크 없음).
//
// 종료 코드 정책
//   0 (출력 없음) : 포맷 성공 / 지원하지 않는 확장자 / 포맷터 설정·바이너리 없음 / 타임아웃 / 설정 파일 오류 / 기타 실패
//   2 (stderr)    : 포맷터가 "방금 편집한 파일의 문법(파싱) 오류" 를 보고 → "파일:라인 - 메시지 요약(10줄 이내)" 를 Claude 에게 전달. 편집은 되돌리지 않는다.
//
// 문법 오류 판정 = (포맷터별 패턴 일치) AND (출력에 편집한 파일 이름이 포함)
//   두 번째 조건이 없으면 .prettierrc / biome.json / pyproject.toml / .csharpierrc 같은 "설정 파일 오류" 를 코드 문법 오류로 오판해
//   Claude 가 멀쩡한 코드를 고치려 한다. 설정 오류 출력에는 보통 편집 파일 이름이 없다.
//
// 포맷터 선택 (프로젝트 설정 파일이 있을 때만, 설정 파일이 있는 디렉터리를 작업 디렉터리로 사용 → .prettierignore 등 적용)
//   biome.json(c)                          → biome      (js/ts/jsx/tsx/json/jsonc/css/graphql)
//   .prettierrc* / prettier.config.* / package.json "prettier" → prettier (js/ts/css/md/html/vue/yaml 등)
//   ruff.toml / .ruff.toml / pyproject [tool.ruff] → ruff format
//   pyproject [tool.black]                 → black
//   .config/dotnet-tools.json 또는 dotnet-tools.json 에 csharpier → CSharpier (.cs). 없으면 PATH 의 전역 csharpier. dotnet format 은 느려서 쓰지 않는다.
//   go.mod                                 → gofmt      // 미검증: 문서 기준
//   Cargo.toml                             → rustfmt    // 미검증: 문서 기준
//   .razor 는 CSharpier 가 "unsupported file type" 으로 거부하므로 지원하지 않는다 (실측).
// 바이너리: 패키지 JS 진입점(package.json bin) → node_modules/.bin(.cmd) → venv → PATH. npx 는 쓰지 않는다 (느림).
"use strict";

const fs = require("fs");
const path = require("path");
const os = require("os");
const { spawnSync } = require("child_process");

const IS_WIN = process.platform === "win32";
const SPAWN_TIMEOUT_MS = 8000; // hook timeout(10s) 보다 짧게 두어 타임아웃 시에도 exit 0 으로 끝낸다

const SKIP_SEGMENTS = new Set(["node_modules", ".git", "dist", "build", "out", "bin", "obj", ".next", ".nuxt", ".svelte-kit", "coverage", "target", "__pycache__", ".venv", "venv", "vendor", ".cache", "Migrations"]);
const SKIP_NAMES = new Set(["package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb", "poetry.lock", "pdm.lock", "uv.lock", "Cargo.lock", "go.sum", "composer.lock", "Gemfile.lock", "packages.lock.json"]);
const SKIP_NAME_RE = /\.(min|generated|gen|g|designer|Designer|AssemblyInfo|GlobalUsings)\.[a-z]+$/i;

const BIOME_EXTS = new Set([".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts", ".json", ".jsonc", ".css", ".graphql"]);
const PRETTIER_EXTS = new Set([...BIOME_EXTS, ".scss", ".less", ".md", ".mdx", ".html", ".vue", ".svelte", ".yaml", ".yml", ".gql"]);
const SUPPORTED_EXTS = new Set([...PRETTIER_EXTS, ".py", ".cs", ".go", ".rs"]);

// ── 유틸 ──────────────────────────────────────────────────────────────
function readStdin() {
  return new Promise((resolve) => {
    let d = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (c) => (d += c));
    process.stdin.on("end", () => resolve(d));
    process.stdin.on("error", () => resolve(d));
  });
}
const exists = (p) => { try { fs.accessSync(p); return true; } catch { return false; } };
const readText = (p) => { try { return fs.readFileSync(p, "utf8"); } catch { return ""; } };
const readJson = (p) => { try { return JSON.parse(readText(p).replace(/^﻿/, "")); } catch { return null; } };

function findUp(startDir, predicate, maxDepth = 12) {
  let dir = startDir;
  for (let i = 0; i < maxDepth; i++) {
    if (predicate(dir)) return dir;
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  return null;
}
const hasAny = (dir, names) => names.some((n) => exists(path.join(dir, n)));
const hasPrettierConfig = (dir) =>
  hasAny(dir, [".prettierrc", ".prettierrc.json", ".prettierrc.yaml", ".prettierrc.yml", ".prettierrc.json5", ".prettierrc.js", ".prettierrc.cjs", ".prettierrc.mjs", ".prettierrc.toml", "prettier.config.js", "prettier.config.cjs", "prettier.config.mjs", "prettier.config.ts"]) ||
  (exists(path.join(dir, "package.json")) && /"prettier"\s*:/.test(readText(path.join(dir, "package.json"))));
const hasBiomeConfig = (dir) => hasAny(dir, ["biome.json", "biome.jsonc"]);
const hasRuffConfig = (dir) => hasAny(dir, ["ruff.toml", ".ruff.toml"]) || /\[tool\.ruff/.test(readText(path.join(dir, "pyproject.toml")));
const hasBlackConfig = (dir) => /\[tool\.black\]/.test(readText(path.join(dir, "pyproject.toml")));
const toolManifestPath = (dir) => [path.join(dir, ".config", "dotnet-tools.json"), path.join(dir, "dotnet-tools.json")].find(exists) || null;
const hasCsharpierManifest = (dir) => { const m = toolManifestPath(dir); const j = m && readJson(m); return !!(j && j.tools && Object.keys(j.tools).some((k) => k.toLowerCase() === "csharpier")); };

function findOnPath(name) {
  const exts = IS_WIN ? (process.env.PATHEXT || ".EXE;.CMD;.BAT").split(";") : [""];
  for (const p of (process.env.PATH || "").split(path.delimiter)) {
    if (!p) continue;
    for (const e of exts) { for (const cand of [path.join(p, name + e.toLowerCase()), path.join(p, name + e)]) if (exists(cand)) return cand; }
  }
  return null;
}

// node 도구: package.json 의 bin 필드로 JS 진입점을 찾아 현재 node 로 직접 실행(.cmd 셔임 경유보다 빠름). 못 찾으면 .bin 셔임 → PATH.
function nodeTool(startDir, pkgName, binName) {
  const pkgDir = findUp(startDir, (d) => exists(path.join(d, "node_modules", pkgName, "package.json")));
  if (pkgDir) {
    const pkgRoot = path.join(pkgDir, "node_modules", pkgName);
    const pkg = readJson(path.join(pkgRoot, "package.json"));
    let rel = pkg && pkg.bin;
    if (rel && typeof rel === "object") rel = rel[binName] || rel[pkgName] || Object.values(rel)[0];
    if (typeof rel === "string" && exists(path.join(pkgRoot, rel))) return { bin: process.execPath, pre: [path.join(pkgRoot, rel)] };
  }
  const shimDir = findUp(startDir, (d) => exists(path.join(d, "node_modules", ".bin", binName + (IS_WIN ? ".cmd" : ""))));
  if (shimDir) return { bin: path.join(shimDir, "node_modules", ".bin", binName + (IS_WIN ? ".cmd" : "")), pre: [] };
  const onPath = findOnPath(binName);
  return onPath ? { bin: onPath, pre: [] } : null;
}
function pyTool(startDir, name) {
  const cands = IS_WIN ? [`.venv\\Scripts\\${name}.exe`, `venv\\Scripts\\${name}.exe`] : [`.venv/bin/${name}`, `venv/bin/${name}`];
  const dir = findUp(startDir, (d) => cands.some((c) => exists(path.join(d, c))));
  if (dir) return { bin: path.join(dir, cands.find((c) => exists(path.join(dir, c)))), pre: [] };
  const onPath = findOnPath(name);
  return onPath ? { bin: onPath, pre: [] } : null;
}
// CSharpier: 매니페스트 버전의 NuGet 캐시 dll 을 dotnet 으로 직접 실행(실측 ~0.45s) → 안 되면 `dotnet csharpier`(~0.85s) → PATH 의 전역 csharpier
function csharpierTool(manifestDir) {
  const dotnet = findOnPath("dotnet");
  if (manifestDir && dotnet) {
    const j = readJson(toolManifestPath(manifestDir));
    const key = j && j.tools && Object.keys(j.tools).find((k) => k.toLowerCase() === "csharpier");
    const ver = key && j.tools[key].version;
    const nugetRoot = process.env.NUGET_PACKAGES || path.join(os.homedir(), ".nuget", "packages");
    const toolsDir = ver && path.join(nugetRoot, "csharpier", ver, "tools");
    if (toolsDir && exists(toolsDir)) {
      // 설치된 런타임(shared/Microsoft.NETCore.App) 메이저 중 가장 높은 것 이하의 TFM 선택
      let installedMajor = 0;
      try { for (const v of fs.readdirSync(path.join(path.dirname(fs.realpathSync(dotnet)), "shared", "Microsoft.NETCore.App"))) installedMajor = Math.max(installedMajor, parseInt(v, 10) || 0); } catch { /* 무시 */ }
      const tfms = fs.readdirSync(toolsDir).map((d) => ({ d, major: parseInt(d.replace(/^net/, ""), 10) || 0 })).filter((t) => t.major && t.major <= installedMajor).sort((a, b) => b.major - a.major);
      for (const t of tfms) { const dll = path.join(toolsDir, t.d, "any", "CSharpier.dll"); if (exists(dll)) return { bin: dotnet, pre: [dll], cwd: manifestDir }; }
    }
    return { bin: dotnet, pre: ["csharpier"], cwd: manifestDir };
  }
  const global = findOnPath("csharpier");
  return global ? { bin: global, pre: [] } : null;
}

// ── 포맷터 선택: { name, bin, args, cwd, isSyntaxError(status, output) } ─────────────────
function pickFormatter(file, ext) {
  const dir = path.dirname(file);
  if (BIOME_EXTS.has(ext)) {
    const cfgDir = findUp(dir, hasBiomeConfig);
    const t = cfgDir && nodeTool(cfgDir, "@biomejs/biome", "biome");
    // biome: 파싱 오류 시 "file.ts:1:5 parse ━━" 블록, 종료 코드 1 (실측 2.x)
    if (t) return { name: "biome", bin: t.bin, args: [...t.pre, "format", "--write", file], cwd: cfgDir, isSyntaxError: (s, o) => s !== 0 && /\bparse\b|syntax|expected/i.test(o) };
  }
  if (PRETTIER_EXTS.has(ext)) {
    const cfgDir = findUp(dir, hasPrettierConfig);
    const t = cfgDir && nodeTool(cfgDir, "prettier", "prettier");
    // prettier: 0 정상, 2 오류(문법 오류 포함). "[error] src/x.ts: SyntaxError: ... (2:10)" (실측 3.x)
    if (t) return { name: "prettier", bin: t.bin, args: [...t.pre, "--write", "--log-level", "warn", file], cwd: cfgDir, isSyntaxError: (s, o) => s === 2 && /SyntaxError|Unexpected token|Unterminated|Expected/i.test(o) };
  }
  if (ext === ".py") {
    const ruffDir = findUp(dir, hasRuffConfig);
    const rt = ruffDir && pyTool(ruffDir, "ruff");
    // ruff format: "error: Failed to parse x.py:1:7: ..." 종료 코드 2 (실측 0.16). --quiet 를 주면 이 메시지가 사라지므로 쓰지 않는다.
    //              깨진 pyproject.toml 도 "Failed to parse ...pyproject.toml" 로 같은 패턴 → 파일명 조건으로 걸러짐 (실측)
    if (rt) return { name: "ruff", bin: rt.bin, args: ["format", file], cwd: ruffDir, isSyntaxError: (s, o) => s !== 0 && /Failed to parse|SyntaxError|invalid syntax/i.test(o) };
    const blackDir = findUp(dir, hasBlackConfig);
    const bt = blackDir && pyTool(blackDir, "black");
    // black: "error: cannot format x.py: Cannot parse: 1:7: ..." 종료 코드 123 (실측 2x.x)
    if (bt) return { name: "black", bin: bt.bin, args: ["--quiet", file], cwd: blackDir, isSyntaxError: (s, o) => s === 123 && /Cannot parse|cannot format/i.test(o) };
  }
  if (ext === ".cs") {
    const manifestDir = findUp(dir, hasCsharpierManifest);
    const t = csharpierTool(manifestDir);
    // CSharpier: "Error <path> - Was not formatted due to syntax errors." + "(2,31): error CS1026: ..." 종료 코드 1 (실측 1.3)
    //            설정 파일 오류는 "Unhandled exception: System.Text.Json.JsonException" (파일 경로 없음) → 파일명 조건으로 걸러짐
    if (t) return { name: "csharpier", bin: t.bin, args: [...t.pre, "format", file], cwd: t.cwd || dir, isSyntaxError: (s, o) => s !== 0 && /syntax errors|error CS\d{4}/i.test(o) };
  }
  if (ext === ".go") {
    const modDir = findUp(dir, (d) => exists(path.join(d, "go.mod")));
    const bin = modDir && findOnPath("gofmt");
    // gofmt: "x.go:3:1: expected declaration, found ..." 종료 코드 2   // 미검증: 문서 기준
    if (bin) return { name: "gofmt", bin, args: ["-w", file], cwd: modDir, isSyntaxError: (s, o) => s === 2 && /:\d+:\d+: /.test(o) };
  }
  if (ext === ".rs") {
    const cargoDir = findUp(dir, (d) => exists(path.join(d, "Cargo.toml")));
    const bin = cargoDir && findOnPath("rustfmt");
    // rustfmt: "error: expected ..." + "--> x.rs:1:5" 종료 코드 1   // 미검증: 문서 기준
    if (bin) return { name: "rustfmt", bin, args: ["--edition", "2021", file], cwd: cargoDir, isSyntaxError: (s, o) => s !== 0 && /^error(\[E\d+\])?:/m.test(o) && /-->/.test(o) };
  }
  return null;
}

// 출력에 편집한 파일이 언급되는가 (파일명 기준, 대소문자·경로 구분자 무시)
function mentionsFile(output, file) {
  const base = path.basename(file).toLowerCase();
  return output.toLowerCase().includes(base);
}

// "파일:라인 - 메시지" 요약 (10줄 이내, 스택 트레이스 제외)
function summarize(file, output) {
  const lines = output.split(/\r?\n/).map((l) => l.trim()).filter((l) => l && !/^\s*at\s/.test(l));
  const m = output.match(/[:(]\s*(\d+)[:,](\d+)/);   // "x.ts:2:10", "(2,31)", "Cannot parse: 1:6" 모두 대응
  const loc = m ? `${path.basename(file)}:${m[1]}` : path.basename(file);
  return `${loc} - 포맷터가 문법 오류를 보고했습니다:\n  ${lines.slice(0, 9).join("\n  ")}`;
}

// ── main ──────────────────────────────────────────────────────────────
(async () => {
  let input;
  try { input = JSON.parse(((await readStdin()) || "{}").replace(/^﻿/, "")); } catch { process.exit(0); }
  const file = (input.tool_input || {}).file_path;
  if (!file) process.exit(0);

  const ext = path.extname(file).toLowerCase();
  if (!SUPPORTED_EXTS.has(ext)) process.exit(0);                       // 지원 확장자가 아니면 즉시 종료 (.razor 포함)
  const base = path.basename(file);
  if (SKIP_NAMES.has(base) || SKIP_NAME_RE.test(base)) process.exit(0);
  if (file.replace(/\\/g, "/").split("/").some((s) => SKIP_SEGMENTS.has(s))) process.exit(0);
  if (!exists(file)) process.exit(0);

  const abs = path.resolve(file);
  const f = pickFormatter(abs, ext);
  if (!f) process.exit(0);                                              // 설정 파일 또는 바이너리 없음 → 조용히 통과

  const useShell = IS_WIN && /\.(cmd|bat)$/i.test(f.bin);               // .cmd 셔임은 shell 로만 실행 가능 (인자는 따옴표로 감싼다)
  const r = spawnSync(useShell ? `"${f.bin}"` : f.bin, f.args.map((a) => (useShell ? `"${a}"` : a)), {
    cwd: f.cwd, shell: useShell, encoding: "utf8", timeout: SPAWN_TIMEOUT_MS, windowsHide: true,
  });

  if (r.error) process.exit(0);                                         // 타임아웃(ETIMEDOUT)·실행 실패 → 조용히 통과
  if (r.status === 0) process.exit(0);                                  // 포맷 성공
  const output = (r.stderr || "") + "\n" + (r.stdout || "");
  if (f.isSyntaxError(r.status, output) && mentionsFile(output, abs)) {
    process.stderr.write(`[format-on-edit] ${summarize(abs, output)}\n`);
    process.exit(2);
  }
  process.exit(0);                                                      // 설정 오류·구분 불확실한 실패는 조용히 통과
})();
