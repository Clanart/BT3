### Title
`isSafeFileExtension` extension-safety check bypassed via trailing dot/space in file name, allowing `.exe`/`.cmd`/`.bat`/`.sh` files to be classified "safe" and opened via `openFile` - (File: `app/src/ui/lib/context-menu.ts`)

### Summary
`isSafeFileExtension` in `app/src/ui/lib/context-menu.ts` blocks a hard-coded list of extensions on Windows by lower-casing `Path.extname(path)` and checking membership in `RestrictedFileExtensions`. Casing tricks (e.g. `payload.EXE`) are already neutralized by the `toLowerCase()` call, but a file whose name ends in a trailing `.` or trailing whitespace after the real extension (e.g. `payload.exe.` or `payload.exe `) produces an `extname()` value that does **not** match `.exe`, so the check incorrectly reports the file as "safe".

### Finding Description
`isSafeFileExtension` is defined as: [1](#0-0) 

It is invoked in `pull-request-files-changed.tsx` (and similarly in `filter-changes-list.tsx` / `selected-commits.tsx`) using `Path.extname(file.path)`: [2](#0-1) 

`Path.extname` returns everything from the *last* `.` to the end of the last path segment. For a file literally named `payload.exe.`, `Path.extname` returns `'.'` (per Node's documented behavior, e.g. `extname('index.')` → `'.'`), which does not equal `.exe`, so `isSafeFileExtension('.')` returns `true`. Similarly, for `payload.exe ` (trailing space), `extname` returns `'.exe '` (including the space), which also fails to match `.exe` in the restricted list, again returning `true`.

Because Windows' Win32 file APIs (used by `CreateFile`/`ShellExecute`, and transitively by Electron's `shell.openExternal`) strip trailing dots and spaces from file/path names when resolving them, a file checked out from a malicious repository as `payload.exe.` or `payload.exe ` is effectively treated by the OS as `payload.exe`. When the user selects "Open with Default Program" (enabled because `isSafeExtension` evaluated `true`), `onOpenFile` builds the path and calls `openFile`: [3](#0-2) 

which calls `shell.openExternal` with a `file://` URL: [4](#0-3) 

Windows' shell-execute path resolution normalizes the trailing dot/space away, so the OS launches the underlying `.exe` (or `.cmd`/`.bat`) directly — exactly the outcome `RestrictedFileExtensions` is meant to prevent.

### Impact Explanation
If the check is bypassed, a repository committed/pushed by an attacker (e.g. a malicious pull request the victim reviews via GitHub Desktop's "Files Changed" view) can contain an executable disguised with a trailing dot/space. The victim, seeing the app itself label the action "Open with Default Program" as enabled (i.e., presented as safe), double-clicks/opens it, resulting in direct execution of attacker-controlled native code on the victim's machine outside of any git-repo confinement. This is unprivileged remote code execution triggered purely by attacker-controlled repository content plus a single, intended UI action (not an "unnatural" step) — the app's own safety gate is what fails.

### Likelihood Explanation
Requires the victim to open a malicious PR/repo in Desktop and manually invoke "Open with Default Program" on the file. This is a normal, expected user interaction that the app explicitly offers as safe (`OpenWithDefaultProgramLabel`, `enabled: isSafeExtension && fileExistsOnDisk`) — the user is not doing anything unusual; the app is mis-labeling the action as safe. This is Windows-only (`__WIN32__` guard), since on other platforms `isSafeFileExtension` always returns `true` by design (that path is intentional, not a bug for macOS/Linux).

### Recommendation
- Normalize/validate the extension before comparison: reject filenames whose trailing characters (after trimming legitimate suffixes) are `.` or whitespace, or better, resolve the extension against the name Windows would actually create on disk (trim trailing dots/spaces first, then re-derive `extname`).
- Consider checking the *actual* file on disk (e.g., via `fs.stat`/canonical name resolution) rather than trusting the git-tracked path string when deciding whether to allow "Open with Default Program".
- Alternatively, block opening any file whose extname (after trimming trailing `.`/whitespace-only remainders) still resolves to a restricted extension, rather than doing a strict string equality after only `toLowerCase()`.

### Proof of Concept
1. Attacker creates a repository/branch containing a file named `payload.exe.` (trailing dot) with executable/malicious PE content, and opens a PR against the victim's fork/repo, or the victim simply fetches/clones the branch.
2. Victim opens the PR in GitHub Desktop's "Files Changed" view (`pull-request-files-changed.tsx`) and right-clicks the file.
3. `Path.extname('payload.exe.')` evaluates to `'.'`; `isSafeFileExtension('.')` returns `true` on Windows, so "Open with Default Program" is enabled.
4. Victim clicks "Open with Default Program"; `onOpenFile` → `openFile` → `shell.openExternal('file://.../payload.exe.')`.
5. Windows' path-normalization strips the trailing dot when resolving/launching the file, executing it as `payload.exe` via the OS default handler for executables — i.e., directly running attacker-controlled code.
6. The same bypass applies to a trailing-space variant, `payload.exe ` (extname `'.exe '` ≠ `.exe`).

Note: I was unable to directly inspect `app/src/lib/app-shell.ts`'s `openExternal` implementation (grep found no `openExternal` match in that file, so it might come from an alias/wrapper or Electron directly) or run an actual Windows checkout test to empirically confirm the OS-level trailing-dot/space stripping behavior in this exact codebase; this is based on Node's documented `path.extname` semantics (confirmed by reading `isSafeFileExtension`) combined with the well-documented Win32 filesystem-API behavior of stripping trailing dots/spaces from filenames. A background Devin session with local Windows access would be needed to fully verify the on-disk execution step.

### Citations

**File:** app/src/ui/lib/context-menu.ts (L34-39)
```typescript
export function isSafeFileExtension(extension: string): boolean {
  if (__WIN32__) {
    return RestrictedFileExtensions.indexOf(extension.toLowerCase()) === -1
  }
  return true
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

**File:** app/src/ui/open-pull-request/pull-request-files-changed.tsx (L178-200)
```typescript
    const extension = Path.extname(file.path)
    const isSafeExtension = isSafeFileExtension(extension)
    const openInExternalEditor =
      externalEditorLabel !== undefined
        ? `Open in ${externalEditorLabel}`
        : DefaultEditorLabel

    const items: IMenuItem[] = [
      {
        label: RevealInFileManagerLabel,
        action: () => revealInFileManager(repository, file.path),
        enabled: fileExistsOnDisk,
      },
      {
        label: openInExternalEditor,
        action: () => dispatcher.openInExternalEditor(fullPath),
        enabled: fileExistsOnDisk,
      },
      {
        label: OpenWithDefaultProgramLabel,
        action: () => this.onOpenFile(file.path),
        enabled: isSafeExtension && fileExistsOnDisk,
      },
```

**File:** app/src/ui/lib/open-file.ts (L4-8)
```typescript
export async function openFile(
  fullPath: string,
  dispatcher: Dispatcher
): Promise<void> {
  const result = await shell.openExternal(`file://${fullPath}`)
```
