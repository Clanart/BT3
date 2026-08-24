### Title
Push target/credential mismatch when branch upstream remote differs from resolved default remote - (File: `app/src/lib/stores/app-store.ts`)

### Summary
`AppStore.performPush` builds a `safeRemote` object whose `name` and `url` fields can be sourced from two different `IRemote` records — the branch's own upstream remote name and the store's currently cached default `remote.url` — and then feeds that inconsistent object into `push()`, which resolves authentication/proxy environment for one URL while telling `git push` to target a remote name that may point to a different URL entirely.

### Finding Description
In `performPush`, the remote name actually passed to `git push` is computed as: [1](#0-0) 

```
const branch = this.getBranchToPush(repository, options)
...
const remoteName = branch.upstreamRemoteName || remote.name
```

`remote` here comes from `repositoryStateCache` (the app's cached "default" remote, refreshed asynchronously by `gitStore.loadRemotes()`), while `branch.upstreamRemoteName` is derived purely from the branch's tracking ref (`refs/remotes/<name>/...`), which is read directly from the on-disk git config/refs of whatever repository is currently open: [2](#0-1) 

The code then explicitly builds a `safeRemote` that mixes the two: [3](#0-2) 

```
const safeRemote: IRemote = { name: remoteName, url: remote.url }

if (safeRemote.name !== remote.name) {
  sendNonFatalException('remoteNameMismatch', ...)
}
```

The comment block above this code acknowledges the two values "could... be out of sync" but only logs a non-fatal telemetry event — it does not abort or re-resolve. `safeRemote` is then passed to `pushRepo`/`push()`: [4](#0-3) 

```
const args = ['push', remote.name, ...]
...
let opts: IGitStringExecutionOptions = {
  env: await envForRemoteOperation(remote.url),
  ...
}
```

Here `remote.name` (the potential mismatched upstream-derived name, e.g. `upstream`) is the literal argument passed to `git push <name> <refspec>`, which git resolves against whatever URL is configured for that name in the repository's `.git/config` — a value **entirely independent of** `safeRemote.url`, which is only used to compute `envForRemoteOperation` (credential helper env vars and the resolved HTTP(S) proxy): [5](#0-4) 

So the credential/proxy environment is prepared for one host, while the actual network destination git connects to is determined by a second, independently-sourced remote name/URL pair that Desktop never validates for consistency before invoking git.

### Impact Explanation
Because a cloned/fetched repository fully controls its own `.git/config` remotes and its branches' upstream tracking refs, an attacker distributing a repository (or a branch within it, e.g. via a pull request the victim checks out with Desktop) can register a remote such as `upstream` pointing at an attacker-controlled server while leaving Desktop's cached "default" `remote` referencing the legitimate `origin`. If the branch's tracking ref points to `refs/remotes/upstream/...`, `performPush` will execute `git push upstream ...` while resolving auth/proxy environment for `origin`'s URL. Depending on the credential helper and proxy configuration in effect, this can result in the victim's push (and any credential material picked up along the way, e.g. via HTTP Basic auth headers formed for the wrong host, or via a proxy the attacker controls) being sent to the attacker's server instead of the intended one — i.e., silent corruption of the destination of a push and potential credential exfiltration. This exactly parallels the audited bug class: a downstream operation assumes internal consistency between two related values that are not always kept in sync, and the existing guard (`sendNonFatalException`) only reports the anomaly without preventing the unsafe operation from proceeding.

### Likelihood Explanation
The mismatch requires the cached default `remote` and the current branch's `upstreamRemoteName` to genuinely differ, which the code's own comment states is a "theoretical possibility" it does not fully rule out (remote list and branch state are refreshed on different schedules via `gitStore.loadRemotes()`/`loadBranches()`, and a freshly cloned/checked-out repository can arrive with multiple remotes already configured). This makes the attacker-controlled precondition (crafting extra remotes / tracking branches) directly reachable from checking out or opening a repository, without requiring local machine access, admin rights, or pre-existing malware.

### Recommendation
Do not silently combine `name` from one remote record with `url` from another. When `branch.upstreamRemoteName` differs from the cached default `remote.name`, resolve the actual `IRemote` object registered under that name (or re-run `gitStore.loadRemotes()`) and use that remote's own `name`+`url` pair consistently for both the `git push` target and the `envForRemoteOperation` call, or abort/prompt the user instead of merely emitting a non-fatal exception.

### Proof of Concept
Exact reproduction steps could not be fully verified without running Desktop end-to-end (e.g. confirming which credential helper/proxy environment variable actually leaks in a live push), so this should be validated experimentally:
1. Create a repository with two remotes: `origin` → legitimate GitHub URL, `upstream` → attacker-controlled server.
2. Set up a local branch whose upstream tracking ref is `refs/remotes/upstream/<branch>` (so `Branch.upstreamRemoteName` returns `"upstream"`), while Desktop's `repositoryStateCache` still reports `remote` as `origin` (e.g. immediately after switching remotes/branches, before/without a full remote-state refresh).
3. Trigger `_push` from the UI.
4. Observe that `performPush` computes `remoteName = "upstream"`, builds `safeRemote = { name: "upstream", url: origin.url }`, and calls `push()`, which runs `git push upstream ...` with an environment resolved for `origin.url` — confirm via process arguments/env inspection that the actual network destination (`upstream`'s configured URL) differs from the URL used to prepare `envForRemoteOperation`, and check whether this results in the git push connecting to the attacker's server using credential/proxy configuration intended for `origin`.

### Citations

**File:** app/src/lib/stores/app-store.ts (L5206-5213)
```typescript
    return this.withPushPullFetch(repository, async () => {
      const branch = this.getBranchToPush(repository, options)

      if (branch === undefined) {
        return
      }

      const remoteName = branch.upstreamRemoteName || remote.name
```

**File:** app/src/lib/stores/app-store.ts (L5251-5282)
```typescript
      //
      // Prior to this we relied primarily on the `branch.remote`
      // property and used the `remote.name` as a fallback in case the
      // branch object didn't have a remote name (i.e. if it's not
      // published yet).
      //
      // The remote.name is derived from the current tip first and falls
      // back to using the defaultRemote if the current tip isn't valid
      // or if the current branch isn't published. There's however no
      // guarantee that they'll be refreshed at the exact same time so
      // there's a theoretical possibility that `branch.remote` and
      // `remote.name` could be out of sync. I have no reason to suspect
      // that's the case and if it is then we already have problems as
      // the `fetchRemotes` call after the push already relies on the
      // `remote` and not the `branch.remote`. All that said this is
      // a critical path in the app and somehow breaking pushing would
      // be near unforgivable so I'm introducing this `safeRemote`
      // temporarily to ensure that there's no risk of us using an
      // out of sync remote name while still providing envForRemoteOperation
      // with an url to use when resolving proxies.
      //
      // I'm also adding a non fatal exception if this ever happens
      // so that we can confidently remove this safeguard in a future
      // release.
      const safeRemote: IRemote = { name: remoteName, url: remote.url }

      if (safeRemote.name !== remote.name) {
        sendNonFatalException(
          'remoteNameMismatch',
          new Error('The current remote name differs from the branch remote')
        )
      }
```

**File:** app/src/models/branch.ts (L64-77)
```typescript
  /** The name of the upstream's remote. */
  public get upstreamRemoteName(): string | null {
    const upstream = this.upstream
    if (!upstream) {
      return null
    }

    const pieces = upstream.match(/(.*?)\/.*/)
    if (!pieces || pieces.length < 2) {
      return null
    }

    return pieces[1]
  }
```

**File:** app/src/lib/git/push.ts (L48-61)
```typescript
export async function push(
  repository: Repository,
  remote: IRemote,
  localBranch: string,
  remoteBranch: string | null,
  tagsToPush: ReadonlyArray<string> | null,
  options?: PushOptions,
  progressCallback?: (progress: IPushProgress) => void
): Promise<void> {
  const args = [
    'push',
    remote.name,
    remoteBranch ? `${localBranch}:${remoteBranch}` : localBranch,
  ]
```

**File:** app/src/lib/git/environment.ts (L76-81)
```typescript
export async function envForRemoteOperation(remoteUrl: string) {
  return {
    ...envForAuthentication(),
    ...(await envForProxy(remoteUrl)),
  }
}
```
