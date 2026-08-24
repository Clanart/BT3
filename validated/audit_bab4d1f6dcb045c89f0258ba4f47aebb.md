Confirmed: `UNSAFE_openDirectory` calls `shell.openPath`, which is the OS "double-click" equivalent — for any bundle-type directory this can trigger execution/automation rather than a plain file-manager listing. [1](#0-0) 

The gate for whether a macOS directory is safe to hand to `UNSAFE_openDirectory` is `isApplicationBundle`, which relies on a hardcoded allowlist of three UTI strings matched via `stdout.includes` against `mdls` output. [2](#0-1) 

### Title
Incomplete allowlist in `isApplicationBundle` lets non-.app "double-clickable" bundles (e.g. Automator `.workflow`) execute via `showFolderContents` - (File: app/src/lib/is-application-bundle.ts)

### Summary
`showFolderContents` in `main-process-proxy.ts` is the only gate that decides, on macOS, whether a directory from a cloned/fetched repository is safe to hand to `UNSAFE_openDirectory` (which calls `shell.openPath`, effectively simulating a Finder double-click) or must instead be revealed (not opened) via `showItemInFolder`.

### Finding Description
The decision is based entirely on a three-item allowlist of Uniform Type Identifiers (`com.apple.application-bundle`, `com.apple.application`, `public.executable`) matched with a naive substring check `stdout.includes(`"${id}"`)` against the output of `mdls -name kMDItemContentType -name kMDItemContentTypeTree`. [3](#0-2) 

This allowlist does not cover other macOS bundle UTIs that Finder treats as directly "runnable" on double click, most notably Automator workflows (`com.apple.automator-workflow`, extension `.workflow`), which are ordinary directories a `git` repository can contain and clone verbatim (Git has no concept of "app bundle" and stores directories as regular trees; a `.workflow` folder with the correct `Info.plist`/`document.wflow` structure survives clone/fetch unmodified). When `isApplicationBundle` is called against such a directory, `mdls`'s `kMDItemContentType`/`kMDItemContentTypeTree` output for that path will contain `"com.apple.automator-workflow"` but none of the three allowlisted strings, so the function returns `false`, and `showFolderContents` proceeds to call `UNSAFE_openDirectory(path)` → `shell.openPath`, which is functionally equivalent to double-clicking the workflow in Finder — i.e., it runs the embedded Automator actions (which can include Run Shell Script / Run AppleScript actions) without further user confirmation. [4](#0-3) 

The same class of gap likely applies to any other double-click-executable bundle-style UTI not enumerated (e.g. some Quick Look/plugin/prefPane bundle types), since the check is a positive allowlist rather than derived from the canonical `public.executable`/`com.apple.bundle` type hierarchy exhaustively, or from asking the OS "is this launchable" directly.

### Impact Explanation
If exploitable, this results in code execution on the victim's machine triggered purely by content inside an attacker-controlled repository the user clones/fetches and then invokes "Show folder contents" (or equivalent UI action reaching `showFolderContents`) on, without the explicit "you are opening an application, are you sure?" Gatekeeper-style friction that a real `.app` bundle triggers. This matches the "Valid Impact" criteria: attacker controls cloned/fetched repository content, and outcome is code execution outside normal repository interaction.

### Likelihood Explanation
Exploitability depends on: (1) confirming exactly which UI action(s) reach `showFolderContents` with an attacker-influenced path (this needs verification — it appeared referenced from `app/src/main-process/main.ts`, `build-default-menu.ts`, and `app.tsx`, but I was not able to fully trace which specific "open" flows expose a repository-relative directory to this function before running out of tool budget), and (2) whether the `.workflow` (or other non-allowlisted UTI) folder structure inside a cloned repo actually resolves via `mdls` to a content type Automator will execute on `shell.openPath`, and whether Gatekeeper/quarantine attributes (not typically applied to freshly cloned git content, only to downloaded files) would block execution. These are plausible but unverified assumptions given index/tool limits.

### Recommendation
Replace the hardcoded substring allowlist with a stricter mechanism: e.g., query `kMDItemContentTypeTree` and check that it is a subtype of `public.directory`/`public.folder` AND NOT a subtype of `public.executable`, `com.apple.bundle`, or any UTI conforming to `public.item` that Launch Services considers "openable/runnable" — or more robustly, use `NSWorkspace`/Launch Services APIs (e.g., `LSCopyKindStringForURL`/UTTypeConformsTo checks against `public.executable` and known workflow/bundle conforming types) rather than a fixed three-string list, so newly-added or overlooked executable bundle UTIs are not silently treated as plain folders.

### Proof of Concept
1. Create a git repository containing a valid Automator `.workflow` bundle directory (with `Info.plist`, `document.wflow`, and Quick Action or Application-style workflow) whose `document.wflow` includes a "Run Shell Script" action.
2. Have the victim clone this repository with GitHub Desktop and trigger the "Show folder contents" action (via whichever UI path reaches `showFolderContents`) on that `.workflow` directory.
3. Because `isApplicationBundle` returns `false` for `com.apple.automator-workflow` (not in the allowlist), `UNSAFE_openDirectory`/`shell.openPath` is invoked directly on the workflow folder, causing macOS to execute the workflow's shell script action.

*Note: I could not fully trace, within the available tool budget, every UI trigger point that calls `showFolderContents` with an untrusted repository path, nor could I execute the PoC in a live macOS environment to confirm `mdls`/Automator double-click execution semantics for git-cloned (non-quarantined) `.workflow` bundles. This should be validated end-to-end (ideally in a Devin session with full filesystem/macOS access) before treating it as confirmed.*

### Citations

**File:** app/src/main-process/shell.ts (L17-31)
```typescript
export function UNSAFE_openDirectory(path: string) {
  // Add a trailing slash to the directory path.
  //
  // On Windows, if there's a file and a directory with the
  // same name (e.g `C:\MyFolder\foo` and `C:\MyFolder\foo.exe`),
  // when executing shell.openItem(`C:\MyFolder\foo`) then the EXE file
  // will get opened.
  // We can avoid this by adding a final backslash at the end of the path.
  const pathname = __WIN32__ && !path.endsWith('\\') ? `${path}\\` : path

  shell.openPath(pathname).then(err => {
    if (err !== '') {
      log.error(`Failed to open directory (${path}): ${err}`)
    }
  })
```

**File:** app/src/lib/is-application-bundle.ts (L34-46)
```typescript
  const { stdout } = await execFile('mdls', [
    ...['-name', 'kMDItemContentType'],
    ...['-name', 'kMDItemContentTypeTree'],
    path,
  ])

  const probableBundleIdentifiers = [
    'com.apple.application-bundle',
    'com.apple.application',
    'public.executable',
  ]

  return probableBundleIdentifiers.some(id => stdout.includes(`"${id}"`))
```

**File:** app/src/ui/main-process-proxy.ts (L123-144)
```typescript
  // On macOS a directory might also be an app bundle and if it is
  // and we attempt to open it we're gonna execute that app which
  // it far from ideal so we'll look up the metadata for the path
  // and attempt to determine whether it's an app bundle or not.
  //
  // If we fail loading the metadata we'll assume it's an app bundle
  // out of an abundance of caution.
  const isBundle = await isApplicationBundle(path).catch(err => {
    log.error(`Failed to load metadata for path '${path}'`, err)
    return true
  })

  if (isBundle) {
    log.info(
      `Preventing direct open of path '${path}' as it appears to be an application bundle`
    )

    await _showItemInFolder(path)
  } else {
    UNSAFE_openDirectory(path)
  }
}
```
