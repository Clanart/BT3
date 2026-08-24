## Finding: Deep-link `branch` parameter accepts leading-dash values that can be interpreted as `git` command-line options

### File
`app/src/lib/parse-app-url.ts` (validation gap) and `app/src/lib/git/checkout.ts` (unsafe argv construction)

### Summary

`parseAppURL` validates the `branch` query parameter using `testForInvalidChars(branch)` from `sanitize-ref-name.ts`, but that function only tests a character-class regex and does **not** reject a leading `-`: [1](#0-0) 

Note that the *sibling* function in the same file, `sanitizedRefName`, explicitly strips leading `-`/`+` via `.replace(/^[-\+]*/g, '')`, and this stripping behavior is asserted by a dedicated unit test ("does not allow name to start with minus"): [2](#0-1) 

However, `parseAppURL` calls only `testForInvalidChars`, never `sanitizedRefName`, so the leading-dash stripping is never applied to deep-link branch input: [3](#0-2) 

Confirmed by tracing: `parseAppURL(url)` with `branch=--upload-pack=id` would not match any character in `invalidCharacterRegex` (`[\x00-\x20\x7F~^:?*\[\\|""<>]+|@{|\.\.+|^\.|\.$|\.lock$|\/$`), so `testForInvalidChars` returns `false`, and the function returns a non-`unknown` `IOpenRepositoryFromURLAction` with `branch: '--upload-pack=id'`.

Downstream, this value flows through `Dispatcher.openRepositoryFromUrl` → `openBranchNameFromUrl` → `checkoutLocalBranch`: [4](#0-3) 

Separately, the actual `git checkout` argv builder in `checkout.ts` places the `--` pathspec terminator **after** the branch name, not before it: [5](#0-4) [6](#0-5) 

Because `--` only protects arguments that follow it, a `branch.name` beginning with `-` is still parsed by `git` as an option rather than as a positional ref argument. This is a genuine argv-construction defect independent of the deep-link path.

### What I could NOT fully verify
I was unable to retrieve the full body of `Dispatcher.checkoutLocalBranch` (only 2 references were found in `dispatcher.ts`, and the implementation itself wasn't returned by my searches before the iteration budget ran out). Whether that method:
- looks up an *existing* branch by exact name match (in which case the attacker would need to control repository content — creating a real ref literally named `--upload-pack=id` in a malicious remote — which is in-scope per the "attacker controls a cloned/fetched repository" rule), or
- creates a brand-new local branch directly from the untrusted string (in which case the deep link alone, with no malicious repo content, suffices),

could not be confirmed from available context. This materially affects the likelihood/severity of the deep-link-only variant described in the question, and should be verified directly in a full checkout of the repo.

### Impact Explanation
If a branch string starting with `-` reaches `getBranchCheckoutArgs`/`checkoutBranch`, it is placed as a bare argv token before the `--` terminator, so `git checkout` (or whatever underlying `git branch`/`git checkout -b` command consumes it) will parse it as an option rather than a ref name. `--upload-pack=<cmd>` specifically is not a recognized option for `git checkout`, so the exact PoC in the question would just produce an "unknown option" error for `checkout` — it is not itself an RCE against `checkout`. However, the underlying defect (unsanitized leading-dash pass-through + `--` placed after rather than before the variable argument) is a real flaw that could enable different flag injections depending on which git subcommand ultimately consumes the string (e.g. `git branch`, `git checkout -b`), and is a pattern git's own security guidance (`git-check-ref-format`, `--` conventions) is designed to prevent.

### Likelihood Explanation
Low-to-moderate. Exploitability depends on the unverified internals of `checkoutLocalBranch` (see above) and on which specific git subcommand flags could be smuggled to achieve code execution rather than just an error. The specific `--upload-pack` PoC as stated does not work against `git checkout`, since that option isn't valid there.

### Recommendation
- In `parse-app-url.ts`, explicitly reject branch/ref values starting with `-` (or `+`) in addition to the existing `testForInvalidChars` check, matching the stricter behavior already implemented (but unused here) in `sanitizedRefName`.
- In `checkout.ts`'s `getBranchCheckoutArgs`, move the `--` terminator to precede the branch name/ref arguments, e.g. `['--', branch.name, ...]`, so git argument parsing cannot treat a ref beginning with `-` as an option.
- Verify and, if needed, harden `Dispatcher.checkoutLocalBranch` (and any other code path that turns a raw string into a `Branch`/`createBranch` call) to reject or sanitize ref names beginning with `-` before they reach any `git()` invocation.

### Proof of Concept
```ts
import { parseAppURL } from './app/src/lib/parse-app-url'

const result = parseAppURL(
  'github-mac://openRepo/https://github.com/attacker/repo?branch=--upload-pack=touch%20/tmp/pwned'
)
console.log(result)
// { name: 'open-repository-from-url', url: 'https://github.com/attacker/repo',
//   branch: '--upload-pack=touch /tmp/pwned', pr: null, filepath: null }
```
This confirms `testForInvalidChars` does not block the leading `-`, matching the code path shown in `app/src/lib/parse-app-url.ts:114-124`. Full exploitation to code execution requires confirming the exact downstream consumer (`checkoutLocalBranch`'s implementation), which I was unable to retrieve in this session — recommend a follow-up code read of that method before treating this as a confirmed RCE.

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

**File:** app/test/unit/sanitize-ref-name-test.ts (L30-34)
```typescript
  it('does not allow name to start with minus', () => {
    const branchName = '--but-can-still-keep-the-rest'
    const result = sanitizedRefName(branchName)
    assert.equal(result, 'but-can-still-keep-the-rest')
  })
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

**File:** app/src/ui/dispatcher/dispatcher.ts (L1975-1996)
```typescript
  private async openBranchNameFromUrl(
    url: string,
    branchName: string
  ): Promise<Repository | null> {
    const repository = await this.openOrCloneRepository(url)

    if (repository === null) {
      return null
    }

    // ensure a fresh clone repository has it's in-memory state
    // up-to-date before performing the "Clone in Desktop" steps
    await this.appStore._refreshRepository(repository)

    // if the repo has a remote, fetch before switching branches to ensure
    // the checkout will be successful. This operation could be a no-op.
    await this.appStore._fetch(repository, FetchType.UserInitiatedTask)

    await this.checkoutLocalBranch(repository, branchName)

    return repository
  }
```

**File:** app/src/lib/git/checkout.ts (L28-36)
```typescript
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
