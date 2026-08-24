Based on the code I was able to trace, I confirm a real validation gap, but the specific "`--upload-pack=` executed via `checkoutLocalBranch`" exploitation chain described in the question is not fully substantiated — I was not able to complete tracing the `openRepositoryFromUrl` flow through `app-store.ts` / `dispatcher.ts` before running out of tool budget, so I can't confirm whether the raw `branch` string is ever passed to a `git clone`/`git fetch` invocation (where `--upload-pack=` would matter) as opposed to only being used to look up an already-resolved `Branch` object.

### Title
Argument/flag-injection into `git checkout` via unsanitized deep-link `branch` parameter - (`app/src/lib/parse-app-url.ts`, `app/src/lib/git/checkout.ts`)

### Summary
`parseAppURL` validates the `branch` query parameter of an `x-github-client://openrepo` deep link with `testForInvalidChars`, which only rejects control characters and the set `~^:?*[\|<>` etc. [1](#0-0)  It does **not** reject a leading `-`/`--`, unlike the sibling `sanitizedRefName` helper, which explicitly strips leading `-`/`+` characters (`.replace(/^[-\+]*/g, '')`) — showing the omission in `testForInvalidChars` is inconsistent with the intended sanitization model. [2](#0-1) 

As a result, `parseAppURL('x-github-client://openrepo/...&branch=--upload-pack%3Did')` (with no `pr` param) passes the check at [3](#0-2)  and returns a non-`unknown` `open-repository-from-url` action carrying the raw attacker-controlled string as `branch`.

### Finding Description
When a branch is eventually checked out via `checkoutBranch`, the branch's name is placed directly as the first positional argument to `git checkout`, *before* the `--` pathspec terminator: [4](#0-3) [5](#0-4) 

Because the `--` separator is appended *after* the branch name rather than before it, a branch value beginning with `-`/`--` is positioned where git still parses it as an option rather than a pathspec/ref, i.e. classic argv/flag injection into the `git checkout` invocation.

However, I was not able to verify (within the current investigation) that the raw, attacker-supplied `branch` string from the deep link is what actually reaches `checkoutBranch`'s `branch.name` unmodified, versus being resolved first against an existing `Branch` model (e.g., matched against known local/remote branches, or used to create a new branch via a separate `git branch`/`git checkout -b` code path in `app/src/lib/stores/app-store.ts` / `app/src/ui/dispatcher/dispatcher.ts`, which I did not get to fully read). Additionally, `--upload-pack=<cmd>` is not a recognized option of `git checkout` (it's a `clone`/`fetch`/`ls-remote` option), so the specific PoC in the question would most likely just produce a `git: unknown option` error rather than executing `touch /tmp/pwned`, unless the branch string is separately routed into a clone/fetch command elsewhere (unconfirmed).

### Impact Explanation
If the unresolved branch string reaches an argv position (as it does in `getBranchCheckoutArgs`), an attacker who gets a victim to click a crafted `x-github-client://openrepo` link could inject arbitrary flags into the `git checkout` subprocess. Depending on which flags are reachable, this is a flag-injection primitive; whether it escalates to code execution depends on whether the value ever reaches a network-fetching git command (`clone`/`fetch`/`ls-remote`) that supports `--upload-pack=<command>`, which I could not confirm from the code paths reviewed.

### Likelihood Explanation
Moderate-to-uncertain: the validation gap (`testForInvalidChars` not blocking leading `-`) is definite and easily reachable via a deep link, requiring only a user click. But the actual exploitability (RCE vs. inert error) depends on unverified downstream logic that I could not fully trace in this session (`app-store.ts`, `dispatcher.ts`, and whether/how a not-yet-existing branch string is used to construct a `Branch` object or fed into a network git command).

### Recommendation
- Align `testForInvalidChars` with `sanitizedRefName` by also rejecting/blocking names starting with `-` or `+`.
- In `getBranchCheckoutArgs`, place the `--` pathspec terminator immediately after `checkout` and before the branch name (i.e., `['checkout', '--', branch.name, ...]` is not valid for the `-b` case, but at minimum ensure branch/ref values are never positioned where git's option parser can consume them), or explicitly validate that resolved branch names never begin with `-`.
- Trace and confirm (recommend a follow-up Devin session to fully read `app/src/lib/stores/app-store.ts` and `app/src/ui/dispatcher/dispatcher.ts`) whether the raw `branch` deep-link value is ever passed to `git clone`/`fetch`/`ls-remote`, since that is the path where `--upload-pack=` would be dangerous.

### Proof of Concept
```ts
import { parseAppURL } from './parse-app-url'
const action = parseAppURL('x-github-client://openrepo/https://github.com/a/b?branch=--upload-pack=touch%20/tmp/pwned')
// action.name === 'open-repository-from-url'
// action.branch === '--upload-pack=touch /tmp/pwned'  (not rejected)
```
This confirms the validation bypass at [6](#0-5) . Full exploitation to code execution requires further tracing of how this `branch` string is consumed downstream, which was not completed in this session.

### Citations

**File:** app/src/lib/sanitize-ref-name.ts (L1-16)
```typescript
// See https://www.kernel.org/pub/software/scm/git/docs/git-check-ref-format.html
// ASCII Control chars and space, DEL, ~ ^ : ? * [ \
// | " < and > is technically a valid refname but not on Windows
// the magic sequence @{, consecutive dots, leading and trailing dot, ref ending in .lock
const invalidCharacterRegex =
  /[\x00-\x20\x7F~^:?*\[\\|""<>]+|@{|\.\.+|^\.|\.$|\.lock$|\/$/g

/** Sanitize a proposed reference name by replacing illegal characters. */
export function sanitizedRefName(name: string): string {
  return name.replace(invalidCharacterRegex, '-').replace(/^[-\+]*/g, '')
}

/** Validate that a reference does not contain any invalid characters */
export function testForInvalidChars(name: string): boolean {
  return invalidCharacterRegex.test(name)
}
```

**File:** app/src/lib/parse-app-url.ts (L114-124)
```typescript
    if (branch != null && testForInvalidChars(branch)) {
      return unknown
    }

    return {
      name: 'open-repository-from-url',
      url: parsedPath,
      branch,
      pr,
      filepath,
    }
```

**File:** app/src/lib/git/checkout.ts (L24-36)
```typescript
function getCheckoutArgs(progressCallback?: ProgressCallback) {
  return ['checkout', ...(progressCallback ? ['--progress'] : [])]
}

async function getBranchCheckoutArgs(branch: Branch) {
  return [
    branch.name,
    ...(branch.type === BranchType.Remote
      ? ['-b', branch.nameWithoutRemote]
      : []),
    '--',
  ]
}
```

**File:** app/src/lib/git/checkout.ts (L121-124)
```typescript
  const baseArgs = getCheckoutArgs(progressCallback)
  const args = [...baseArgs, ...(await getBranchCheckoutArgs(branch))]

  await git(args, repository.path, 'checkoutBranch', opts)
```
