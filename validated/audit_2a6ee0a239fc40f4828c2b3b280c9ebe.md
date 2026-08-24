### Title
Insecure fail-open default in `fetchPushControl` causes silent suppression of branch-protection warnings before commit/push - (File: `app/src/lib/api.ts`)

### Summary
The reported bug class is: when an external, potentially-unreliable dependency (a Chainlink price feed) fails or reverts, the contract has no safe fallback and either DOSes or silently substitutes a value that isn't validated as safe. The equivalent primitive in GitHub Desktop is `API.fetchPushControl`, which queries GitHub's `push_control` endpoint to determine whether the current branch is protected. When that request fails for *any* reason (network blip, GHE outage, deprecated/renamed endpoint, rate limiting, a malicious/broken proxy or MITM response), the method does not propagate the failure or return a "restrictive"/"unknown" state — it silently returns a hard-coded object that claims the user has **full permissive access**: `allow_actor: true, allow_deletions: true, allow_force_pushes: true`.

### Finding Description
`fetchPushControl` is documented to fail open: [1](#0-0) 

This return value is consumed directly by `refreshBranchProtectionState` in the app store, which computes `currentBranchProtected = !isBranchPushable(pushControl)`: [2](#0-1) 

`isBranchPushable` treats `allow_actor !== false` combined with zero required status checks/reviews as "pushable" — exactly what the fail-open default satisfies: [3](#0-2) 

The resulting `currentBranchProtected` flag flows into the Changes sidebar / commit UI, where it is used to warn the user before they commit and push to a protected branch: [4](#0-3) 

This is structurally identical to the Solidity bug: an external, occasionally-unreachable oracle (`push_control` API, analogous to the Chainlink price feed) is queried to gate a security-relevant decision, and when that oracle is unreachable, the code silently substitutes an "optimistic"/permissive value instead of a safe, restrictive one or surfacing the uncertainty to the caller.

### Impact Explanation
This is a fail-open default, not fail-closed. Any transient failure of the `push_control` request — including one induced by a hostile network intermediary, proxy, or Enterprise Server instance the user is pointed at (which the report explicitly lists as an attacker-controlled surface: "a git remote/proxy response") — causes GitHub Desktop to treat a protected branch as unprotected in its UI. The user is not warned before creating/pushing a commit that violates branch protection expectations they rely on (e.g., "don't let me accidentally push directly to `main`"), so the commit corruption/oversight is silent from Desktop's perspective. Note that GitHub's server-side branch protection still enforces the actual push (the push itself would be rejected by GitHub), so this does not grant unauthorized code execution or an unauthorized push, it degrades a client-side safety warning into a false negative.

### Likelihood Explanation
Likelihood is moderate to low for triggering the fail-open path via a genuinely attacker-controlled channel: an attacker able to intercept/tamper with the `push_control` API call (e.g., a malicious HTTP proxy or a compromised GitHub Enterprise endpoint the user is signed into) can trivially cause any error status/timeout to trip the `catch` branch, since `fetchPushControl` fails open on *any* exception — including a deliberately malformed/aborted response — without inspecting whether the failure is transient/expected (e.g., 404) versus suspicious.

### Recommendation
Do not fail open. On request failure, either:
- Propagate the failure (return `null` / a distinct "unknown" sentinel, as `fetchProtectedBranches` and `fetchAllRepoRulesets` already do elsewhere in `api.ts`), and have `refreshBranchProtectionState` preserve the previous known protection state (or default to "protected"/show a warning) rather than assuming full access, or
- Return a maximally restrictive default (`allow_actor: false`, `allow_deletions: false`, `allow_force_pushes: false`) so that failures degrade to "warn the user" rather than "silently allow."

### Proof of Concept
1. Sign in to a GitHub Enterprise/GitHub.com account in Desktop and open a repository whose current branch is protected (push restricted / requires PR review).
2. Intercept or otherwise cause the `GET repos/:owner/:repo/branches/:branch/push_control` request to fail (e.g., via a MITM proxy that resets the connection, a rate-limited/5xx response, or pointing the GHE endpoint at a host that doesn't implement this preview API) — see `fetchPushControl`: [5](#0-4) .
3. Observe that `refreshBranchProtectionState` receives the fail-open default and sets `currentBranchProtected = false` even though the branch is actually protected: [6](#0-5) .
4. The Changes sidebar/commit UI therefore does not warn the user about committing/pushing to a protected branch, which it would have done had the request succeeded or failed closed: [7](#0-6) .

### Citations

**File:** app/src/lib/api.ts (L1629-1668)
```typescript
  /**
   * Get branch protection info to determine if a user can push to a given branch.
   *
   * Note: if request fails, the default returned value assumes full access for the user
   */
  public async fetchPushControl(
    owner: string,
    name: string,
    branch: string
  ): Promise<IAPIPushControl> {
    const path = `repos/${owner}/${name}/branches/${encodeURIComponent(
      branch
    )}/push_control`

    const headers: any = {
      Accept: 'application/vnd.github.phandalin-preview',
    }

    try {
      const response = await this.ghRequest('GET', path, {
        customHeaders: headers,
      })
      return await parsedResponse<IAPIPushControl>(response)
    } catch (err) {
      log.info(
        `[fetchPushControl] unable to check if branch is potentially pushable`,
        err
      )
      return {
        pattern: null,
        required_signatures: false,
        required_status_checks: [],
        required_approving_review_count: 0,
        required_linear_history: false,
        allow_actor: true,
        allow_deletions: true,
        allow_force_pushes: true,
      }
    }
  }
```

**File:** app/src/lib/stores/app-store.ts (L1517-1523)
```typescript
      const name = gitHubRepo.name
      const owner = gitHubRepo.owner.login
      const api = API.fromAccount(account)

      const pushControl = await api.fetchPushControl(owner, name, branchName)
      const currentBranchProtected = !isBranchPushable(pushControl)

```

**File:** app/src/lib/helpers/push-control.ts (L23-49)
```typescript
export function isBranchPushable(pushControl: IAPIPushControl) {
  const {
    allow_actor,
    required_status_checks,
    required_approving_review_count,
  } = pushControl

  // See https://github.com/desktop/desktop/issues/9054#issuecomment-582768322
  // We'll guard against this being undefined until we can determine the
  // root cause and fix that.
  const requiredStatusCheckCount = Array.isArray(required_status_checks)
    ? required_status_checks.length
    : 0

  // If user is admin and branch is not admin-enforced,
  // required status checks and reviews get zeroed out in API response (no merge requirements).
  // If user is admin and branch is admin-enforced,
  // required status checks and reviews do NOT get zeroed out in API response.
  // If user is allowed to push based on `Restrict who can push` setting, they must still
  // respect the merge requirements, and can't push if checks or reviews are required for merging
  const noMergeRequirements =
    requiredStatusCheckCount === 0 && required_approving_review_count === 0

  // We check for !== false so that if a future version of the API decides to
  // remove or rename that property we'll revert to assuming that the user
  // _does_ have access rather than assuming that they _don't_.
  return allow_actor !== false && noMergeRequirements
```

**File:** app/src/ui/changes/sidebar.tsx (L392-472)
```typescript
  public render() {
    const {
      workingDirectory,
      commitMessage,
      showCoAuthoredBy,
      coAuthors,
      conflictState,
      selection,
      currentBranchProtected,
      currentRepoRulesInfo,
    } = this.props.changes
    let rebaseConflictState: RebaseConflictState | null = null
    if (conflictState !== null) {
      rebaseConflictState = isRebaseConflictState(conflictState)
        ? conflictState
        : null
    }

    const selectedFileIDs =
      selection.kind === ChangesSelectionKind.WorkingDirectory
        ? selection.selectedFileIDs
        : []

    const isShowingStashEntry = selection.kind === ChangesSelectionKind.Stash
    const repositoryAccount = getAccountForRepository(
      this.props.accounts,
      this.props.repository
    )

    return (
      <div className="panel" role="tabpanel" aria-labelledby="changes-tab">
        <FilterChangesList
          ref={this.changesListRef}
          dispatcher={this.props.dispatcher}
          repository={this.props.repository}
          repositoryAccount={repositoryAccount}
          workingDirectory={workingDirectory}
          conflictState={conflictState}
          mostRecentLocalCommit={this.props.mostRecentLocalCommit}
          rebaseConflictState={rebaseConflictState}
          selectedFileIDs={selectedFileIDs}
          onFileSelectionChanged={this.onFileSelectionChanged}
          onCreateCommit={this.onCreateCommit}
          onIncludeChanged={this.onIncludeChanged}
          onDiscardChanges={this.onDiscardChanges}
          askForConfirmationOnDiscardChanges={
            this.props.askForConfirmationOnDiscardChanges
          }
          askForConfirmationOnCommitFilteredChanges={
            this.props.askForConfirmationOnCommitFilteredChanges
          }
          onDiscardChangesFromFiles={this.onDiscardChangesFromFiles}
          onOpenItem={this.onOpenItem}
          onRowClick={this.onChangedItemClick}
          commitAuthor={this.props.commitAuthor}
          branch={this.props.branch}
          commitMessage={commitMessage}
          focusCommitMessage={this.props.focusCommitMessage}
          isShowingModal={this.props.isShowingModal}
          isShowingFoldout={this.props.isShowingFoldout}
          autocompletionProviders={this.autocompletionProviders!}
          availableWidth={this.props.availableWidth}
          onIgnoreFile={this.onIgnoreFile}
          onIgnorePattern={this.onIgnorePattern}
          isCommitting={this.props.isCommitting}
          hookProgress={this.props.hookProgress}
          onShowCommitProgress={this.props.onShowCommitProgress}
          isGeneratingCommitMessage={this.props.isGeneratingCommitMessage}
          shouldShowGenerateCommitMessageCallOut={
            this.props.shouldShowGenerateCommitMessageCallOut
          }
          commitToAmend={this.props.commitToAmend}
          showCoAuthoredBy={showCoAuthoredBy}
          coAuthors={coAuthors}
          externalEditorLabel={this.props.externalEditorLabel}
          onOpenItemInExternalEditor={this.onOpenItemInExternalEditor}
          onChangesListScrolled={this.props.onChangesListScrolled}
          changesListScrollTop={this.props.changesListScrollTop}
          stashEntry={this.props.changes.stashEntry}
          isShowingStashEntry={isShowingStashEntry}
          currentBranchProtected={currentBranchProtected}
```
