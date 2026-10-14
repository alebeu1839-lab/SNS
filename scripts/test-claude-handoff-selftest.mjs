#!/usr/bin/env node
// Credential-free integration tests using a local stub instead of real Claude Code.
import { spawnSync } from "node:child_process";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
const dir=mkdtempSync(join(tmpdir(),"claude-handoff-test-"));
const script=fileURLToPath(new URL("./test-claude-handoff.mjs",import.meta.url));
try {
  const bin=join(dir,"fake-claude.mjs");
  const run=(answer, key) => {
    writeFileSync(bin, 'console.log('+JSON.stringify(answer)+');' + String.fromCharCode(10));
    return spawnSync(process.execPath,[script],{
      encoding:"utf8",env:{...process.env,ANTHROPIC_API_KEY:key||"",CLAUDE_HANDOFF_TEST_BIN:process.execPath,CLAUDE_HANDOFF_TEST_PREFIX_ARGS:JSON.stringify([bin])}
    });
  };
  const pass=run("CLAUDE_HANDOFF_OK","");
  const fail=run("WRONG","");
  const billing=run("CLAUDE_HANDOFF_OK","test-key");
  if(pass.status!==0 || !JSON.parse(pass.stdout).ok) throw new Error("success path failed: "+JSON.stringify({status:pass.status,stdout:pass.stdout,stderr:pass.stderr}));
  if(fail.status===0 || JSON.parse(fail.stdout).ok) throw new Error("invalid response path failed");
  if(billing.status!==2) throw new Error("billing guard failed");
  console.log("PASS: success, unexpected reply, API-key billing guard");
} finally { rmSync(dir,{recursive:true,force:true}); }
