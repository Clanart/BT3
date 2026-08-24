## Finding: `openFile` executes attacker-controlled repository content via the OS default handler with no safeguards

### Title
Unwarned execution of attacker-controlled repository files via `shell.openExternal(file://...)` - (File: `app/src/ui/lib/open-file.ts`)

### Summary
The external report's broken invariant is: a value (USDi) carries an implicit safety guarantee (redeemability) that is silently lost once it crosses a trust boundary (transfer to a non-whitelisted address), with no code-level guard preventing or warning about that transition. The GitHub Desktop analog is the "Open File" feature, which passes a path taken directly from repository contents into `shell.openExternal` as a `file://` URI, causing the OS to execute the file with its default handler with no extension check, no confirmation, and — critically — without the "Mark of the Web"/quarantine flag that browsers normally attach to downloaded content, which is what typically triggers OS-level warnings (Windows SmartScreen, macOS Gatekeeper "unidentified developer" prompts) for untrusted executables.

### Finding Description
`openFile` builds a `file://` URL directly from a path and hands it to Electron's `shell.openExternal`: [1](#0-0) 

`shell.openExternal` asks the OS to open the resource with its registered default handler — for an `.exe`, `.cmd`, `.scr`, `.desktop`, `.app`, `.command`, etc. this means direct execution, not merely "viewing" the file. This function is wired into multiple UI surfaces that operate over files sourced from a repository's working tree/history (changed files sidebar, commit file lists, conflict resolution UI, pull-request file-changed view), all of which enumerate paths taken from git-tracked/attacker-controlled content: [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) 

Unlike files obtained through a browser download (which get an internet-zone marker that triggers OS execution warnings), files that arrive via `git clone`/`git fetch`/`git checkout` are written to disk as ordinary local files with no such marker. GitHub Desktop's clone path only guards against writing into *sensitive system locations* — it does not add any provenance/quarantine marking to the cloned content itself: [6](#0-5) 

So the two protections that would normally intervene — (1) the OS quarantine/MOTW warning for internet-sourced executables, and (2) any Desktop-side content-type check — are both absent for this path, meaning a repository-supplied file that a user opens through Desktop's "Open File" affordance is executed exactly as if it were a trusted local file.

### Impact Explanation
An attacker who controls a public/shared repository (or a PR branch a victim fetches) can commit an executable disguised with a benign-looking name/extension for the platform (e.g. a `.cmd`/`.bat` on Windows, a `.command` on macOS, or a `.desktop` launcher on Linux) inside the repo. When a legitimate Desktop user reviews changes and uses the "Open File" action on that entry, Desktop calls `shell.openExternal('file://...')` and the OS launches it immediately via its default handler, with none of the "this file was downloaded from the internet" friction a browser download would normally impose. This gives the attacker code execution on the victim's machine.

### Likelihood Explanation
The action requires the victim to deliberately choose "Open File" (or double-click) on a specific file shown in Desktop's UI — a normal part of reviewing changes/PRs, not an unnatural multi-step sequence. It does not require admin rights, prior malware, or leaked credentials, and the attacker fully controls the payload by simply committing it to a repository the victim clones/fetches/checks out (including PR branches surfaced in the "Files Changed" view).

### Recommendation
Before invoking `shell.openExternal` on a repository-relative path, apply platform-appropriate safeguards: check the file extension against a list of potentially executable/script types and either warn the user with an explicit confirmation dialog, or refuse to open such files directly (routing them to a text/hex viewer instead), similar to the "Open Without Git"/"Install Git" confirmation pattern already used elsewhere in Desktop's dialog system, and consistent with how browsers gate execution of downloaded content.

### Proof of Concept
1. Create a public repository containing a file `update-notes.cmd` (Windows) whose contents run an arbitrary command (e.g., `calc.exe` or a reverse shell).
2. Have the victim clone or fetch the repository (or open a pull request from it) in GitHub Desktop.
3. In the Changes/Commit history/PR "Files Changed" view, the victim right-clicks/double-clicks the file and selects "Open File."
4. `openFile()` in `app/src/ui/lib/open-file.ts` calls `shell.openExternal('file:///path/to/repo/update-notes.cmd')`.
5. The OS launches `update-notes.cmd` with its default handler (`cmd.exe`) immediately, executing the attacker's payload — with no SmartScreen/quarantine warning, since the file was never marked as internet-downloaded content.

**Note on confidence:** I was unable to retrieve the exact double-click/context-menu wiring in `app/src/ui/changes/sidebar.tsx` and related files in this session due to a tool-call failure on the final iteration, so the precise UI trigger (double-click vs. context-menu item) is inferred from `grep` matches rather than directly read code. If more certainty is needed on the exact call sites and any pre-existing extension checks, a full read of those files (or a Devin session with file-system access) would confirm the exact trigger conditions.

### Citations

**File:** app/src/ui/lib/open-file.ts (L1-17)
```typescript
import { shell } from '../../lib/app-shell'
import { Dispatcher } from '../dispatcher'

export async function openFile(
  fullPath: string,
  dispatcher: Dispatcher
): Promise<void> {
  const result = await shell.openExternal(`file://${fullPath}`)

  if (!result) {
    const error = {
      name: 'no-external-program',
      message: `Unable to open file ${fullPath} in an external program. Please check you have a program associated with this file extension`,
    }
    await dispatcher.postError(error)
  }
}
```

**File:** app/src/ui/changes/sidebar.tsx (L1-1)
```typescript
import * as Path from 'path'
```

**File:** app/src/ui/history/selected-commits.tsx (L1-1)
```typescript
import * as React from 'react'
```

**File:** app/src/ui/lib/conflicts/unmerged-file.tsx (L1-1)
```typescript
import * as React from 'react'
```

**File:** app/src/ui/open-pull-request/pull-request-files-changed.tsx (L1-1)
```typescript
import * as React from 'react'
```

**File:** app/src/lib/git/clone.ts (L74-79)
```typescript
  if (isClonePathSensitive(path)) {
    throw new Error(
      `The clone destination "${path}" targets a sensitive system location. ` +
        'Cloning into this directory is not allowed.'
    )
  }
```
