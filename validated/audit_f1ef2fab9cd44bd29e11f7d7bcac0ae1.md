## Title
Unvalidated `clone_url` scheme allows RCE via `ext::`/`fd::` git transport helpers - (File: `app/src/lib/git/clone.ts`)

## Summary
`IAPIRepository.clone_url` returned by the GitHub/GHE API (or a MITM'd response) is passed unmodified from the repository list all the way to a `git clone` invocation, with no validation of the URL scheme. Git's `ext::`/`fd::` remote helpers execute an arbitrary shell command as part of establishing the "clone" transport, so a malicious `clone_url` value results in command execution on the user's machine when they select/clone the repo.

## Finding Description
`toListItems` in `app/src/ui/clone-repository/group-repositories.ts` copies the API-provided value straight into the list item that is later matched against/clicked: [1](#0-0) 

When a user clicks (or presses Enter on) a repo item, `onItemClicked` invokes `this.clone()`: [2](#0-1) 

`clone()` calls `resolveCloneInfo()`, which — for URLs that don't end in `.wiki.git` and where there's no matched account/owner+name pair to re-resolve via the API — returns the **original, unmodified URL**: [3](#0-2) 

That `url` is then handed to `cloneImpl`/`dispatcher.clone`, which ultimately reaches `app/src/lib/git/clone.ts`. There, the destination *path* is validated against a sensitive-location denylist (`isClonePathSensitive`), but the *url* itself is never validated for scheme — it is simply appended as a positional argument after `--`: [4](#0-3) 

Using `--` prevents the URL from being parsed as a git CLI flag, but it does nothing to stop the git **transport helper** mechanism: `ext::<command>` and `fd::<fd>` are legitimate git protocols documented in `gitremote-helpers(1)` that spawn a shell command (`ext::`) to serve as the transport. Since this URL is provided as the literal command-line argument to `git clone` (i.e., it looks to Git exactly like a user-typed URL), Git's `protocol.allow`/allowed-protocols mechanism treats it as user-supplied and permits it by default — that protection layer is designed to stop protocols encountered *indirectly* (submodule URLs, redirects, `.netrc`), not URLs the invoking process passes directly on the command line.

No code in the traced path (`toListItems` → `onItemClicked` → `clone` → `resolveCloneInfo` → `clone.ts`) filters the URL scheme to an allowlist such as `https://`/`ssh://`/`git://`. I was unable to fully inspect `app/src/lib/git/environment.ts` (`envForRemoteOperation`) before running out of tool calls, so I cannot rule out that it sets `GIT_ALLOW_PROTOCOL` there; the code I traced only shows `envForRemoteOperation(url)` being merged with `GIT_CLONE_PROTECTION_ACTIVE: 'false'`, a variable name that is not a real Git environment variable and appears to be a custom/no-op flag rather than an actual protocol allowlist mechanism.

## Impact Explanation
If exploitable, this results in **arbitrary command execution** on the victim's machine the moment they click on a repository entry sourced from a GitHub Enterprise instance (or any MITM'd GitHub API response), well within "Valid Impact" (attacker-controlled API object leading to code execution).

## Likelihood Explanation
Requires the attacker to control an `IAPIRepository` object returned to the client — realistic for a malicious/compromised GitHub Enterprise server, or a MITM of the GitHub API (relevant if TLS pinning/verification is weak or for `.git.enterprise` deployments with custom CAs). The user only needs to click a repo entry in the clone dialog; no unusual user action is required.

## Recommendation
- Validate `clone_url` (and any other API-provided repo URL fields) against an allowlist of expected schemes (`https:`, `http:`, `ssh:`, `git:`) before ever passing it to `git`/`dugite` in `app/src/ui/clone-repository/group-repositories.ts` and `app/src/lib/git/clone.ts`.
- Explicitly set `GIT_ALLOW_PROTOCOL=http:https:ssh:git` (or the equivalent `protocol.*.allow=never` config for `ext`, `fd`, `file`) in the environment used for all `git clone`/`fetch`/`push`/`pull` invocations, not just rely on Git's default trust-the-command-line behavior.
- Reject/sanitize URLs before they ever reach `args.push('--', url, path)` in `app/src/lib/git/clone.ts`.

## Proof of Concept
1. Mock an API response (or MITM a GHE server) so `IAPIRepository.clone_url = "ext::sh -c touch$IFS/tmp/pwned"`.
2. `groupRepositories` → `toListItems` produces a list item with `url: "ext::sh -c touch$IFS/tmp/pwned"` [5](#0-4) .
3. User clicks the item → `onItemClicked` → `clone()` → `resolveCloneInfo()` returns `{ url: "ext::sh -c touch$IFS/tmp/pwned" }` unmodified [6](#0-5) .
4. `dispatcher.clone` eventually calls `clone(url, path, options)` in `app/src/lib/git/clone.ts`, which runs `git -c init.defaultBranch=... clone --recursive -- ext::sh -c touch$IFS/tmp/pwned <path>` [4](#0-3) , causing `sh -c touch /tmp/pwned` to execute on the victim's machine.

**Note on uncertainty**: I could not confirm the full contents of `app/src/lib/git/environment.ts` (`envForRemoteOperation`) due to reaching the tool-call limit; if that function already sets `GIT_ALLOW_PROTOCOL` to an allowlist excluding `ext`/`fd`, this finding would be mitigated. Based on all code actually inspected in the traced path, no such protection was found — the only related-looking variable, `GIT_CLONE_PROTECTION_ACTIVE: 'false'`, is not a real Git protocol-restriction mechanism.

### Citations

**File:** app/src/ui/clone-repository/group-repositories.ts (L43-53)
```typescript
const toListItems = (repositories: ReadonlyArray<IAPIRepository>) =>
  repositories
    .map<ICloneableRepositoryListItem>(repo => ({
      id: repo.html_url,
      text: [`${repo.owner.login}/${repo.name}`],
      url: repo.clone_url,
      name: repo.name,
      icon: getIcon(repo),
      archived: repo.archived,
    }))
    .sort((x, y) => compare(x.name, y.name))
```

**File:** app/src/ui/clone-repository/clone-repository.tsx (L732-753)
```typescript
  private async resolveCloneInfo(): Promise<IAPIRepositoryCloneInfo | null> {
    const { url, lastParsedIdentifier } = this.getSelectedTabState()

    if (url.endsWith('.wiki.git')) {
      return { url }
    }

    const account = await findAccountForRemoteURL(url, this.props.accounts)
    if (lastParsedIdentifier !== null && account !== null) {
      const api = API.fromAccount(account)
      const { owner, name } = lastParsedIdentifier
      // Respect the user's preference if they provided an SSH URL
      const protocol = parseRemote(url)?.protocol

      return api.fetchRepositoryCloneInfo(owner, name, protocol).catch(err => {
        log.error(`Failed to look up repository clone info for '${url}'`, err)
        return { url }
      })
    }

    return { url }
  }
```

**File:** app/src/ui/clone-repository/clone-repository.tsx (L755-761)
```typescript
  private onItemClicked = (repository: IAPIRepository, source: ClickSource) => {
    if (source.kind === 'keyboard' && source.event.key === 'Enter') {
      if (this.checkIfCloningDisabled() === false) {
        this.clone()
      }
    }
  }
```

**File:** app/src/lib/git/clone.ts (L119-126)
```typescript
  if (options.branch) {
    args.push('-b', options.branch)
  }

  args.push('--', url, path)

  await git(args, __dirname, 'clone', opts)
}
```
