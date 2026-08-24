## #Confirmed Vulnerability

### Title
Recursive `protocol.file.allow=always` config leaks trust to nested/transitive submodules - (File: `app/src/lib/git/submodule.ts`)

### Summary
`updateSubmodulesAfterOperation` builds the submodule-update command as a single top-level invocation of `git -c protocol.file.allow=always submodule update --init --recursive` [1](#0-0) . Git's `-c` values are propagated to every child process the top-level invocation spawns for the lifetime of that invocation (via `GIT_CONFIG_PARAMETERS`/`GIT_CONFIG_COUNT`), and `--recursive` makes `git submodule update` itself recurse into every submodule's own `.gitmodules` and invoke `git submodule update --init --recursive` inside it as a child of the same top-level command. There is no re-application of the flag scoped to only the outer repository's own `.gitmodules` entries — the config is inherited unconditionally at every recursion depth.

### Finding Description
The intended semantics implied by the parameter name and doc comment ("Whether to allow file:// protocol for submodules") is that the caller is vouching for the *top-level* repository's own submodule URLs (e.g. a user has explicitly acknowledged a local/test `file://` submodule reference in the repo they opened) [2](#0-1) . Because the flag is implemented as a single `-c` option on the outer `git ... --recursive` call rather than being re-evaluated per submodule, the actual effect is that `protocol.file.allow=always` applies to *every* submodule URL encountered anywhere in the recursive submodule tree, including `.gitmodules` files that ship inside submodules the top-level repository owner never authored and has no visibility into.

This means an attacker who controls only a leaf/nested submodule (e.g. a public dependency that a trusted top-level repo — or an intermediate submodule — references) can add their own `.gitmodules` file inside that leaf submodule pointing a further nested submodule at a `file://` URL. When `--recursive` dereferences it, git will happily clone from that local path using the file transport, because the `protocol.file.allow=always` config from the outermost invocation is still in effect for the child `git submodule update` process spawned for the attacker's submodule.

### Impact Explanation
`git`'s file transport requires the target path to be a valid git repository (it reads objects/refs), so this is not an arbitrary-file read primitive, but it is an arbitrary-git-repository-disclosure primitive: an attacker-controlled leaf submodule can force git to clone any local git repository the desktop process can read (e.g. `file:///Users/victim/Projects/private-repo/.git`) into the victim's working tree as a new submodule directory. The disclosed repository's full history/contents become part of the user's working directory, where they can be inspected, or accidentally staged/committed/pushed by the user, resulting in disclosure of private repository content that is not reachable via the top-level repository's own declared submodules.

### Likelihood Explanation
Exploitability is entirely gated on `allowFileProtocol` being `true` for the operation. Within the code I was able to inspect, the call sites in `checkout.ts` (`checkoutBranch`/`checkoutCommit`) default the parameter to `false`, and the caller path I traced through `app-store.ts` (`checkoutIgnoringChanges` → `checkoutBranch`) does not pass `true` [3](#0-2) [4](#0-3) . I was not able to locate, within the indexed portions of the codebase, any call site that actually sets `allowFileProtocol = true` in production code — it may be wired up in a UI flow (e.g., a "trust this local file:// submodule" confirmation) that isn't present in the indexed files I retrieved. Because of this, I cannot confirm whether the vulnerable path (`allowFileProtocol === true`) is currently reachable from an unprivileged attacker-controlled repository without deeper investigation of the caller graph (e.g., `pull.ts`, `fetch.ts`, clone flows, or dialog components that decide this flag). If a code path exists where this flag becomes `true` based on any attacker-influenced signal (not a per-operation user confirmation tied specifically to the top-level repo's own submodules), the recursive leak described above is real and will trigger automatically, with no additional confirmation for nested submodules.

### Recommendation
- Do not apply `protocol.file.allow=always` as a single global `-c` flag on a `--recursive` invocation. Instead, drop `--recursive` from the top-level call and manually walk/init submodules one level at a time, re-deciding whether `file://` should be trusted based on each submodule's own declared URL/origin before recursing further.
- Alternatively, if `--recursive` must be kept, use `git config -c protocol.file.allow=user` (the git default) at nested levels and only elevate to `always` for the specific relative submodule path(s) that were already vetted at the top level, e.g. via `submodule.<name>.update` per-path config rather than a blanket process-wide override.
- Ensure any UI-driven "trust this file:// submodule" confirmation is scoped to the specific path/URL the user approved and does not implicitly cover submodules discovered recursively afterward.

### Proof of Concept
1. Create `outer` repo with a submodule `dep` pointing at a normal `https://` (or trusted) URL.
2. Inside `dep`, add a `.gitmodules` entry for a nested submodule `evil` with URL `file:///home/victim/private-repo/.git` (or any absolute path reachable on the victim's machine).
3. Trigger a Desktop code path that calls `updateSubmodulesAfterOperation(..., allowFileProtocol=true)` on `outer` (e.g. checkout of a branch that references `dep`).
4. Observe that `git -c protocol.file.allow=always submodule update --init --recursive` clones `evil` from the local `file://` path with no further prompt, because the config value set on the outer invocation is inherited by the child `git submodule update` process spawned while initializing `dep`'s own submodules — confirming the recursive trust leak. [1](#0-0)

### Citations

**File:** app/src/lib/git/submodule.ts (L27-27)
```typescript
 * @param allowFileProtocol - Whether to allow file:// protocol for submodules
```

**File:** app/src/lib/git/submodule.ts (L45-51)
```typescript
  const args = [
    ...(allowFileProtocol ? ['-c', 'protocol.file.allow=always'] : []),
    'submodule',
    'update',
    '--init',
    '--recursive',
  ]
```

**File:** app/src/lib/git/checkout.ts (L102-108)
```typescript
export async function checkoutBranch(
  repository: Repository,
  branch: Branch,
  currentRemote: IRemote | null,
  progressCallback?: ProgressCallback,
  allowFileProtocol: boolean = false
): Promise<true> {
```

**File:** app/src/lib/stores/app-store.ts (L4663-4671)
```typescript
  private async checkoutIgnoringChanges(
    repository: Repository,
    branch: Branch,
    currentRemote: IRemote | null
  ) {
    await checkoutBranch(repository, branch, currentRemote, progress => {
      this.updateCheckoutProgress(repository, progress)
    })
  }
```
