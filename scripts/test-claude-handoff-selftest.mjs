#!/usr/bin/env node
// Credential-free integration tests using a local stub instead of real Claude Code.
import { spawnSync } from "node:child_process";
import { mkdtempSync, writeFileSync, chmodSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
const dir=mkdtempSync(join(tmpdir(),"claude-handoff-test-"));
const script=new URL("./test-claude-handoff.mjs",import.meta.url).pathname;
try {
  const bin=join(dir,"fake-claude");
  const run=(answer, key) => {
    writeFileSync(bin, '#!/bin/sh\nprintf "'+answer+'\\n"\n');
    chmodSync(bin,0o755);
    return spawnSync(process.execPath,[script],{
      encoding:"utf8",env:{...process.env,ANTHROPIC_API_KEY:key||"",CLAUDE_HANDOFF_TEST_BIN:bin}
    });
  };
  const pass=run("CLAUDE_HANDOFF_OK","");
  const fail=run("WRONG","");
  const billing=run("CLAUDE_HANDOFF_OK","test-key");
  if(pass.status!==0 || !JSON.parse(pass.stdout).ok) throw new Error("success path failed");
  if(fail.status===0 || JSON.parse(fail.stdout).ok) throw new Error("invalid response path failed");
  if(billing.status!==2) throw new Error("billing guard failed");
  console.log("PASS: success, unexpected reply, API-key billing guard");
} finally { rmSync(dir,{recursive:true,force:true}); }
