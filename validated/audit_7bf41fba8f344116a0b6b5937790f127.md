## Title
Unsanitized remote/hook terminal output rendered via xterm.js in `HookFailed` dialog can visually spoof git operation status - (File: `app/src/ui/terminal.tsx`)

## Summary
The `terminalChunks`/`pushTerminalChunk` mechanism in `app/src/lib/git/core.ts` captures raw, unmodified stdout/stderr bytes from git (which faithfully relays remote `remote:` messages and hook output) and stores them verbatim. That raw text is surfaced through two different paths with very different rendering behavior: (1) `GitError.message` / `errorMessage.push(terminalOutput.slice(-1024))`, which ends up as plain text/log output, and (2) `onTerminalOutputAvailable`, which for hook failures is piped straight into an `@xterm/xterm` `Terminal` component that interprets ANSI escape sequences.

## Finding Description
`git()` in [1](#0-0)  wires `process.stdout`/`process.stderr` data into `pushTerminalChunk`, which just concatenates chunks with no stripping of control/escape sequences — it only manages buffer capacity: [2](#0-1) .

This same raw buffer is exposed to consumers through `onTerminalOutputAvailable` as a live/replay stream, and the hook-failure UI feeds this directly into an xterm.js terminal that renders ANSI escapes (cursor movement, line erase, etc.) verbatim: [3](#0-2)  and [4](#0-3) . Because `xterm.js` is a real terminal emulator, a hook script (which can be attacker-controlled if it's a repository-provided/managed hook intercepted by Desktop's trampoline, e.g. `git commit`/`git push` pre/post hooks) can emit sequences like `\x1b[2K\r` (erase line, return to column 0) followed by fabricated text such as "✓ Hook succeeded" to visually overwrite what was actually printed, even though the hook failed and the dialog title still says "failed."

For the plain `GitError` path (`errorMessage.push(terminalOutput.slice(-1024))` at [5](#0-4) ), this text goes into `log.error` (the app log) and into `GitError.message`, which is ultimately displayed in dialogs via `DialogError`, a plain React `<div>` at [6](#0-5) . React's default text-node rendering escapes HTML but does **not** strip ANSI escape codes — however, since this is rendered as inert text (not fed into a terminal emulator), the ANSI bytes would just show up as unprintable/garbled characters in a browser DOM context, not be interpreted as cursor-control instructions. This path is not exploitable for visual spoofing beyond minor text clutter.

The exploitable path is specifically the xterm.js-backed `Terminal` component used for `onHookFailure`/`onTerminalOutputAvailable` consumers (hook failed dialog, and potentially any future consumer of `onTerminalOutputAvailable`), since xterm.js is a full ANSI interpreter by design.

## Impact Explanation
This could allow a malicious hook (if such a hook can be attacker-supplied, e.g. via a cloned repository containing a committed hook that Desktop's trampoline executes) to make the "Hook Failed" dialog display fabricated success text or otherwise obscure the real hook output that the user needs to review before deciding to "Ignore and Continue" or "Abort" a git operation. This is a UI-spoofing/social-engineering-adjacent issue rather than something granting code execution, file access, or credential exfiltration by itself — the actual security decision (abort vs. ignore) is still made against the git exit code and hook status tracked in application state, not against the rendered terminal text. The impact is limited to visually misleading text within a dialog whose title (`${hookName} Failed`) and buttons are not spoofable by this text, and no `remote: message` output for `git push` is currently piped into any xterm-based UI component per the available context — only hook failure output was confirmed to use the `Terminal`/xterm.js component.

## Likelihood Explanation
Requires the attacker to control a git hook that Desktop's trampoline will execute and that hook failure dialog is shown; the scope explicitly excludes local/malware-based hook installation, and it's unclear from the code alone whether a cloned/fetched repository can smuggle in a hook that Desktop will actually run without prior local trust setup (Git normally does not execute hooks from a cloned `.git/hooks` — hooks are not part of the transferred repository data in a normal `git clone`/`fetch`). I could not confirm a path where a plain `git push`/`fetch` remote response (e.g., `remote:` lines) is rendered through the xterm `Terminal` component; the `GitError` path (which does carry raw remote text) renders through a plain, non-ANSI-interpreting `<div>`, defeating the visual-spoofing premise in the proof idea for that specific case.

## Recommendation
- If terminal output must be interpreted for legitimate progress-erasure purposes, sanitize/allow-list which ANSI sequences are permitted in `pushTerminalChunk` (e.g., permit `\r` and simple erase-line sequences but strip cursor-repositioning, screen-clearing, and other sequences that can be used to overwrite unrelated lines).
- Confirm whether `onTerminalOutputAvailable`/`Terminal` (xterm.js) is ever fed content originating from untrusted remote text (e.g., `remote:` lines in `push`/`fetch`), and if so, treat that content with the same caution as hook output.
- Consider disabling or restricting escape-sequence interpretation in the xterm.js instance used for these read-only status displays (e.g., only convert `\r`/`\n`, and screen-reader-mode already partially mitigates this for accessibility but not for sighted users).

## Proof of Concept
Not independently verified end-to-end against a running Desktop instance (no test harness available in this session). Conceptually: a git hook script that, on failure, writes to stderr:
```
echo -e "Some real error\n\x1b[2K\rHook succeeded ✓"
```
would, when displayed by `HookFailed`'s `Terminal` component (which uses `@xterm/xterm` and directly `write()`s the raw buffer per [3](#0-2) ), cause xterm.js to erase the current line and print the fabricated "Hook succeeded" text, even though the dialog itself still correctly indicates failure via its title and button labels. This is a partial confirmation of the reported mechanism (no sanitization exists in `pushTerminalChunk`) but does **not** confirm the specific `git push`/`remote:` scenario described in the question, since that path renders through the non-ANSI-interpreting `DialogError` `<div>`, not through xterm.js.

### Citations

**File:** app/src/lib/git/core.ts (L246-271)
```typescript
  const terminalChunks: string[] = []
  const terminalCapacity = 256 * 1024

  // Keep at most 256kb of combined stderr and stdout output. This is used
  // to provide more context in error messages.
  opts.processCallback = process => {
    options?.onTerminalOutputAvailable?.(function (cb) {
      terminalChunks.forEach(chunk => cb(chunk))

      process.stdout?.on('data', cb)
      process.stderr?.on('data', cb)

      return {
        unsubscribe: () => {
          process.stdout?.off('data', cb)
          process.stderr?.off('data', cb)
        },
      }
    })

    const push = (chunk: Buffer | string) => {
      pushTerminalChunk(terminalChunks, terminalCapacity, chunk)
    }

    process.stdout?.on('data', push)
    process.stderr?.on('data', push)
```

**File:** app/src/lib/git/core.ts (L363-368)
```typescript
          const terminalOutput = terminalChunks.join('')

          if (terminalOutput.length > 0) {
            // Leave even less of the combined output in the log
            errorMessage.push(terminalOutput.slice(-1024))
          }
```

**File:** app/src/lib/git/push-terminal-chunk.ts (L21-40)
```typescript
export const pushTerminalChunk = (
  chunks: string[],
  capacity: number,
  chunk: Buffer | string
) => {
  chunks.push(coerceToString(chunk))
  let terminalOutputLength = chunks.reduce((acc, cur) => acc + cur.length, 0)

  while (terminalOutputLength > capacity) {
    const firstChunk = chunks[0]
    const overrun = terminalOutputLength - capacity

    if (overrun >= firstChunk.length) {
      chunks.shift()
      terminalOutputLength -= firstChunk.length
    } else {
      chunks[0] = firstChunk.substring(overrun)
      terminalOutputLength -= overrun
    }
  }
```

**File:** app/src/ui/terminal.tsx (L31-37)
```typescript
  public write(data: TerminalOutput) {
    if (Array.isArray(data)) {
      data.forEach(chunk => this.terminal?.write(chunk))
    } else {
      this.terminal?.write(data)
    }
  }
```

**File:** app/src/ui/hook-failed/hook-failed.tsx (L46-50)
```typescript
          <Terminal
            terminalOutput={this.props.terminalOutput}
            rows={15}
            cols={80}
          />
```

**File:** app/src/ui/dialog/error.tsx (L16-24)
```typescript
export class DialogError extends React.Component {
  public render() {
    return (
      <div className="dialog-banner dialog-error" role="alert">
        <Octicon symbol={octicons.stop} />
        <div>{this.props.children}</div>
      </div>
    )
  }
```
