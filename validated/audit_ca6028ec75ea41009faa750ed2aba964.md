### Title
Create Branch dialog resolves `startPoint` by mutable ref name, letting a background fetch/attacker-controlled remote silently change the commit a new branch is based on - ([File: app/src/lib/create-branch.ts](), [File: app/src/ui/create-branch/create-branch-dialog.tsx](), [File: app/src/lib/git/branch.ts]())

### Summary
The `MuteBond` finding is fundamentally a TOCTOU/price‑staleness bug: the value shown to the user at "quote time" is not what's actually used at "execution time" because a mutable state variable (`epochStart`) can be advanced by anyone (or by the owner) between those two moments, and there is no check that the executed outcome still matches what the user was shown. The GitHub Desktop analog is the Create Branch flow: the dialog shows the user a specific base (`upstreamDefaultBranch` / `defaultBranch`) resolved from `props` at render time, but the actual branch-creation Git command is executed later using the *branch name* (a mutable ref), not the SHA the user was shown, and Desktop's background fetcher can move that ref in between.

### Finding Description
When the user opens the "Create Branch" dialog, `CreateBranch.renderBranchSelection` in `app/src/ui/create-branch/create-branch-dialog.tsx` displays a base branch resolved via `getStartPoint`/`getBranchForStartPoint` (`app/src/lib/create-branch.ts:10-49`), showing e.g. "based on `upstream/main`". Crucially, when the user clicks "Create Branch," `createBranch()` (lines 347-386) does **not** capture or pass the SHA that was displayed. Instead it resolves:

```
startPoint = upstreamDefaultBranch.name   // or defaultBranch.name
```

i.e., a **ref name string**, not a fixed commit. This string is passed all the way down to `createBranch()` in `app/src/lib/git/branch.ts:21-38`, which runs:

```
git branch <name> <startPoint>
```

Git resolves `<startPoint>` (a branch/ref name) to whatever commit it currently points to **at the moment the command executes**, not at the moment the user saw the preview or clicked the button.

Between the dialog being shown/submitted and the `git branch` invocation actually running, `BackgroundFetcher` (`app/src/lib/stores/helpers/background-fetcher.ts`) can run a fetch (`_fetch(r, FetchType.BackgroundTask)` in `app-store.ts`) that updates `refs/remotes/<remote>/<default-branch>` via `git fetch --prune` (`app/src/lib/git/fetch.ts`). A malicious or compromised remote (or a MITM/legitimate push race by another contributor) can push new commits to the default branch of the fork's upstream (or of `origin`) right after the user opens the dialog. If the background fetch lands before the user clicks "Create Branch," the new local branch will silently be based on a **different, remote-controlled commit** than the one the user was shown and believed they were branching from — with no re-validation and no diff of "what changed since I opened this dialog."

This mirrors the report's broken invariant exactly: a payout/quote (`bondPrice` at view time) is not pinned before the transaction executes, so an attacker-influenced update to shared mutable state (`epochStart` in the contract; the branch ref in Desktop) changes the outcome without the user's knowledge or consent, and no "expected value" check exists to catch and reject the drift.

### Impact Explanation
If the resolved start point differs from what the user was shown, the new branch is silently created from attacker-influenced content. Depending on workflow, the user may then build work atop, or push, or open a PR against a base commit they never reviewed — this is a "silent corruption of what the user commits/pushes" scenario: their subsequent commits get layered on unexpected/attacker-controlled history without any warning banner, hash comparison, or confirmation step in the create-branch flow. This does not require local/physical access or leaked credentials — it only requires control of, or a race with, content on a remote that Desktop already trusts and fetches (exactly the attacker model called out as valid: "attacker controls ... a git remote/proxy response").

### Likelihood Explanation
Background fetching is enabled by default in Desktop and runs on a fixed interval, independent of user actions (see `startBackgroundFetching`/`BackgroundFetcher` in `app/src/lib/stores/app-store.ts` and `app/src/lib/stores/helpers/background-fetcher.ts`). Any window between the dialog opening (or the branch-selection UI refreshing via `componentWillReceiveProps`) and clicking the OK button is a viable race window; forks (which is exactly when `upstreamDefaultBranch` matters) are precisely the case where the "attacker" is the untrusted upstream. No special local privileges, malware, or social engineering are needed — an attacker who can push to (or race a push to) the observed remote branch is sufficient.

### Recommendation
Capture and use a concrete SHA rather than a mutable ref name when creating the branch:
- In `create-branch-dialog.tsx`, resolve `defaultBranch.tip.sha` / `upstreamDefaultBranch.tip.sha` at the moment the dialog is rendered/submitted and pass that fixed SHA as `startPoint` instead of `.name`.
- In `app/src/lib/git/branch.ts`'s `createBranch`, accept and use a SHA, so `git branch <name> <sha>` pins the base regardless of any concurrent ref movement.
- Optionally, if the ref moves between dialog display and submission (detectable via a fresh `getAheadBehind`/tip comparison), surface a warning to the user before proceeding, analogous to adding a "min payout" / expected-value check in the smart-contract fix.

### Proof of Concept
1. User is in a fork with `upstreamDefaultBranch` (e.g., `upstream/main` at SHA `A`) and opens "Create Branch," which displays "based on `upstream/main`."
2. Before the user clicks "Create Branch," an attacker controlling `upstream` pushes new commits so `upstream/main` now points to SHA `B`.
3. Desktop's `BackgroundFetcher` runs its periodic `_fetch` (`FetchType.BackgroundTask`), updating the local `refs/remotes/upstream/main` to `B` via `git fetch --prune` (`app/src/lib/git/fetch.ts`).
4. The user clicks "Create Branch." `createBranch()` in `create-branch-dialog.tsx` sets `startPoint = upstreamDefaultBranch.name` (`"upstream/main"`), which is dispatched to `createBranch(repository, name, startPoint, noTrack)` → `git branch <name> upstream/main` in `app/src/lib/git/branch.ts`.
5. Git resolves `upstream/main` to its *current* tip, `B`, not `A`, the commit the user actually reviewed/saw in the dialog — with no notification that the base changed underneath them.

Note: I could not fully verify the exact end-to-end wiring inside `app-store.ts`'s `_createBranch`/`createBranch` implementation (only its call sites were located, not its full body) due to index truncation; a Devin session with full repo access would be needed to confirm there is no additional SHA-pinning step performed there before invoking `git branch`.