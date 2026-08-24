### Title
Inconsistent path-containment enforcement lets a malicious repository's status/diff paths escape the working tree when opened via context-menu actions - (File: `app/src/ui/lib/open-file.ts`, `app/src/lib/app-shell.ts`)

### Summary
The external report's broken invariant is that two functions performing the *same conceptual operation* (converting a user-controlled quantity into a value compared against a security check) enforce that check inconsistently, so an attacker simply uses the unguarded path. In GitHub Desktop, the analogous invariant is "a repository-relative file path must resolve to a location inside the repository working directory before Desktop opens/reveals it." Desktop enforces this consistently in some code paths and not in others.

### Finding Description
Desktop has a purpose-built guard, `resolveWithin()`, specifically documented as a defense against "path traversal attacks where a crafted URL tricks the UI into deriving a clone path outside the intended base directory" [1](#0-0) . It is correctly applied to attacker-influenced paths in at least two places:
- The custom-protocol deep-link `filepath` handler, which explicitly rejects absolute paths and calls `resolveWithin(repository.path, filepath)` before calling `shell.showItemInFolder` [2](#0-1) .
- The Copilot conflict-resolution file writer, which calls `resolveWithin(workingDirectory, file.path)` before reading/writing conflict files [3](#0-2)  and again before `writeFile` [4](#0-3) .

However, the exact same class of value — a `path` string taken from `WorkingDirectoryFileChange`/`CommittedFileChange`, which is populated verbatim from parsed `git status`/`git log` output (`entry.path` in `status-parser.ts`, unescaped and untraversed) — is joined onto the repository root with plain `Path.join()` and no containment check in numerous "reveal/open" actions:
- `revealInFileManager()`: `Path.join(repository.path, path)` then `shell.showItemInFolder(fullyQualifiedFilePath)`, with an explicit warning in the same file that this API "should never be used with non-validated paths" [5](#0-4) [6](#0-5) .
- `onOpenItem` in the Changes sidebar: `Path.join(this.props.repository.path, path)` then `openFile()` [7](#0-6) .
- The equivalent `onOpenItem` in history/commit file view [8](#0-7) .
- `onOpenFile`/`onOpenBinaryFile` in the pull-request diff viewer, which handles paths reported for a PR's changed files (an even more directly remote-controlled input — the file list comes from the GitHub API/diff for a fetched PR) [9](#0-8) .
- `openFile()` itself, which takes the already-joined `fullPath` and hands it straight to `shell.openExternal('file://' + fullPath)` with no re-validation [10](#0-9) .

`Path.join()` normalizes but does not prevent `..` segments from escaping the base directory (e.g., `Path.join('/repo', '../../etc/passwd')` legitimately resolves outside `/repo`), which is exactly why `resolveWithin` exists and is used elsewhere. None of the "reveal/open" call sites re-derive the path through `resolveWithin`, so the safety property that dispatcher.ts and the Copilot flow deliberately enforce is silently absent here.

### Impact Explanation
If a crafted/malicious commit, working-tree entry, or PR diff can cause `git status`/`git log`/the PR files API to surface a path string containing `..` traversal components (e.g., via a maliciously crafted rename/copy entry, a crafted tree/index that a vulnerable git version fails to reject, or a PR-provided file list from a compromised/malicious fork), the context-menu "Reveal in Finder" or "Open with default program" actions would resolve and open a file *outside* the repository directory chosen by the attacker's path. This is a file-read/open action outside the repo boundary — the same "boundary bypass via the less-guarded of two equivalent code paths" pattern as the audited finding, just instantiated as a filesystem containment check instead of a monetary minimum check.

### Likelihood Explanation
This requires attacker-controlled repository content (a cloned/fetched repo, or a PR file list) to actually produce a traversal-bearing path string that survives Desktop's `git status --porcelain=2 -z` / `git log --name-status -z` parsing and, on the git side, is not itself blocked by git's own path-safety checks (git has historically had to add and fix such protections, e.g. CVE-2014-9390-class issues). I could not verify from the indexed code whether current git guarantees make it impossible for `entry.path` to ever contain `..`/absolute segments in all supported git versions/platforms, nor could I find a git-version floor enforced by Desktop. Given that Desktop's own authors clearly consider this attack surface real enough to add `resolveWithin` gates for the deep-link and Copilot flows, but did not apply the same gate to the older `revealInFileManager`/`onOpenItem`/`openFile` call sites, this reads as a plausible inconsistency rather than a proven, actively exploitable primitive today. Confidence is moderate, not certain — full confirmation would require testing against current git internals/version pinned by dugite, which is outside what the indexed sources let me verify.

### Recommendation
Route every path derived from `WorkingDirectoryFileChange.path`, `CommittedFileChange.path`, or PR-supplied file paths through `resolveWithin(repository.path, path)` (mirroring `dispatcher.ts`'s `openRepositoryFromUrl` and `app-store.ts`'s Copilot resolution flow) before calling `revealInFileManager`, `onOpenItem`/`openFile`, or `shell.showItemInFolder`/`shell.openExternal`, and refuse the action (as the two already-hardened call sites do) if the resolved path escapes the repository root.

### Proof of Concept
Not independently reproducible from the indexed code alone: it would require constructing a git repository/tree or PR file-list response whose reported change path contains `../` segments and confirming it survives `parsePorcelainStatus`/git's own tree-path validation to reach `buildStatusMap`'s `entry.path` unmodified — I was not able to verify this precondition with the available tools. The concrete Desktop-side gap, however, is directly evidenced by the file/line citations above: `resolveWithin` is used in `dispatcher.ts` and `app-store.ts`/`copilot-conflict-context.ts`, but is absent from `app-shell.ts`'s `revealInFileManager`, `sidebar.tsx`/`selected-commits.tsx`'s `onOpenItem`, `pull-request-files-changed.tsx`'s `onOpenFile`, and `open-file.ts`'s `openFile`, all of which perform `Path.join(repository.path, path)` on the same class of input.

### Citations

**File:** app/src/lib/path.ts (L13-24)
```typescript
/**
 * Resolve one or more path sequences into an absolute path underneath
 * or at the given root path.
 *
 * The path segments are expected to be relative paths although
 * providing an absolute path is also supported. In the case of an
 * absolute path segment this method will essentially only verify
 * that the absolute path is equal to or deeper in the directory
 * tree than the root path.
 *
 * If the fully resolved path does not reside underneath the root path
 * this method will return null.
```

**File:** app/src/ui/dispatcher/dispatcher.ts (L1957-1972)
```typescript
    if (filepath !== null) {
      if (isAbsolute(filepath)) {
        log.error(`Refusing to open absolute path: ${filepath}`)
        return
      }

      const resolved = await resolveWithin(repository.path, filepath)

      if (resolved !== null) {
        shell.showItemInFolder(resolved)
      } else {
        log.error(
          `Prevented attempt to open path outside of the repository root: ${filepath}`
        )
      }
    }
```

**File:** app/src/lib/copilot-conflict-context.ts (L390-407)
```typescript
      // Guard against path traversal and symlink escapes (cross-platform)
      let absolutePath: string | null
      try {
        absolutePath = await resolveWithin(workingDirectory, file.path)
      } catch {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'File path could not be resolved safely',
        }
      }
      if (absolutePath === null) {
        return {
          path: file.path,
          hunks: [],
          skippedReason: 'File path is outside the repository',
        }
      }
```

**File:** app/src/lib/stores/app-store.ts (L7233-7239)
```typescript
      const absolutePath = await resolveWithin(repository.path, resolution.path)
      if (absolutePath === null) {
        log.warn(
          `Copilot resolution skipped: path outside repository: ${resolution.path}`
        )
        continue
      }
```

**File:** app/src/lib/app-shell.ts (L17-20)
```typescript
   * Reveals the specified file using the operating
   * system default application.
   * Do not use this method with non-validated paths.
   *
```

**File:** app/src/lib/app-shell.ts (L61-63)
```typescript
export function revealInFileManager(repository: Repository, path: string) {
  const fullyQualifiedFilePath = Path.join(repository.path, path)
  return shell.showItemInFolder(fullyQualifiedFilePath)
```

**File:** app/src/ui/changes/sidebar.tsx (L282-285)
```typescript
  private onOpenItem = (path: string) => {
    const fullPath = Path.join(this.props.repository.path, path)
    openFile(fullPath, this.props.dispatcher)
  }
```

**File:** app/src/ui/history/selected-commits.tsx (L292-295)
```typescript
  private onOpenItem = (path: string) => {
    const fullPath = Path.join(this.props.repository.path, path)
    openFile(fullPath, this.props.dispatcher)
  }
```

**File:** app/src/ui/open-pull-request/pull-request-files-changed.tsx (L86-97)
```typescript
  private onOpenFile = (path: string) => {
    const fullPath = Path.join(this.props.repository.path, path)
    this.onOpenBinaryFile(fullPath)
  }

  /**
   * Opens a binary file in an the system-assigned application for
   * said file type.
   */
  private onOpenBinaryFile = (fullPath: string) => {
    openFile(fullPath, this.props.dispatcher)
  }
```

**File:** app/src/ui/lib/open-file.ts (L4-9)
```typescript
export async function openFile(
  fullPath: string,
  dispatcher: Dispatcher
): Promise<void> {
  const result = await shell.openExternal(`file://${fullPath}`)

```
