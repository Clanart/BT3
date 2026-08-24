## Finding confirmed: `testForInvalidChars` only enforces git ref-name syntax, not shell/argv-flag safety

### Title
Improper Input Validation of `branch` Parameter Allows Git Argument Injection via `openRepo` Deep Link - (File: `app/src/lib/parse-app-url.ts`)

### Summary
The `branch` value taken from an `x-github-client://openRepo/...?branch=...` deep link is validated only with `testForInvalidChars`, which is a git *ref-name character* filter, not an argv-safety filter. A value such as `-Xtheirs` or `--upload-pack=...` contains none of the characters that regex rejects, so it passes validation and is carried forward as the literal `branch` string in the `IOpenRepositoryFromURLAction`.

### Finding Description
`testForInvalidChars` is defined as: [1](#0-0) 

The regex blocks control chars, space, `~^:?*[\|""<>`, `@{`, consecutive dots, leading/trailing dot, `.lock` suffix, and trailing `/`. It does **not** reject a leading `-` or `--`. Note that the sibling function `sanitizedRefName` explicitly strips leading `-`/`+` via a second `.replace(/^[-\+]*/g, '')` pass, but `testForInvalidChars` has no equivalent check, so a dash‑prefixed string is a "valid" ref name for this validator's purposes even though it is unsafe as a bare CLI token.

In `parseAppURL`, the only guard applied to `branch` (when no `pr` parameter is present) is this check: [2](#0-1) 

So `branch = "--upload-pack=calc.exe"` or `branch = "-Xtheirs"` passes through unchanged and is returned inside the `open-repository-from-url` action.

Downstream, when a branch is eventually checked out, `checkoutBranch` in `app/src/lib/git/checkout.ts` builds the argv for `git checkout` as: [3](#0-2) 

Critically, the positional branch/ref argument (`branch.name`) is placed **before** the `--` separator, not after it:
```
['checkout', branch.name, '--']
```
The `--` end-of-options marker only protects arguments that come *after* it (which here is nothing, since there are no pathspecs). Because `branch.name` itself precedes `--`, if it starts with `-`, git's own argv parser will still treat it as an option token rather than a positional ref, since `--` has not yet been seen.

### Impact Explanation
This is a genuine input-validation/argv-construction gap: a `branch` name that is syntactically a "valid" git ref (per `testForInvalidChars`) can also be a leading-dash string that gets handed to `git checkout` as an un-neutralized flag token. That said, I could not confirm within this review that `git checkout` has any option (e.g. `--upload-pack=...`) that leads directly to process execution — `--upload-pack` is a valid option for `git clone`/`git fetch`/`git archive` (transport helper invocation) but is not a recognized `git checkout` option, so a value like `--upload-pack=calc.exe` passed to `checkout` would most likely produce a git "unknown option" error rather than code execution. The exploitable outcome is therefore more clearly "unexpected flag injection into the checkout invocation" (e.g. `-f`, `--orphan`, `--track`/`--no-track`, `-b`) which could alter checkout semantics or corrupt working-tree state, rather than a demonstrated RCE via this specific `checkout` call site.

I was not able to fully trace, within the available tool budget, whether the same unsanitized `branch` string also reaches a `git fetch`/`git clone` invocation elsewhere in the `openRepositoryFromURL` flow (in `app-store.ts`), where `--upload-pack=<program>` genuinely is a valid, dangerous option that spawns an arbitrary program as the "upload-pack" helper. That path would need to be traced in a full session (`app/src/lib/stores/app-store.ts`, search for consumers of `IOpenRepositoryFromURLAction`/`action.branch`) to determine definitively whether RCE is reachable, versus the confirmed-but-lower-severity argv-injection-into-`checkout` issue described above.

### Likelihood Explanation
The `branch` parameter originates from an unprivileged, attacker-controlled deep link that a user must click (`x-github-client://openRepo/...?branch=...`), which fits the "clicked link/deep link" attacker-controlled input category in scope. The validation gap (`testForInvalidChars` not rejecting leading dashes) is straightforward to trigger.

### Recommendation
- Add a check in `testForInvalidChars` (or at the `parse-app-url.ts` call site) to reject ref names beginning with `-`, matching the stripping logic already present in `sanitizedRefName`.
- In `app/src/lib/git/checkout.ts`, ensure the argv passed to `git` always places `--` immediately before the branch/ref positional argument (i.e., `['checkout', '--', branch.name, ...]` where git syntax allows, or explicitly reject/prefix ref arguments that start with `-`) so a hostile ref name cannot be parsed as an option regardless of upstream validation.
- Audit all other sinks that consume `action.branch` (in `app-store.ts`) for the same argv-construction pattern, particularly any `git fetch`/`git clone` calls, to rule out reachability of dangerous options like `--upload-pack`.

### Proof of Concept
```ts
import { testForInvalidChars } from './sanitize-ref-name'
console.log(testForInvalidChars('--upload-pack=calc.exe')) // false -> passes validation
console.log(testForInvalidChars('-Xtheirs'))                // false -> passes validation
```
Given a deep link `x-github-client://openRepo/some/repo?url=...&branch=--upload-pack=calc.exe`, `parseAppURL` returns an `open-repository-from-url` action with `branch: "--upload-pack=calc.exe"` unchanged, because the check at `parse-app-url.ts:114` only calls `testForInvalidChars`, which returns `false` (no invalid chars found) for this string.

### Citations

**File:** app/src/lib/sanitize-ref-name.ts (L5-16)
```typescript
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

**File:** app/src/lib/parse-app-url.ts (L114-116)
```typescript
    if (branch != null && testForInvalidChars(branch)) {
      return unknown
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
