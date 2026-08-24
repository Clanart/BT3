[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** app/src/lib/stores/app-store.ts (L5257-5282)
```typescript
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

**File:** app/src/lib/rebase.ts (L39-68)
```typescript
export function getCurrentBranchForcePushState(
  branchesState: IBranchesState,
  aheadBehind: IAheadBehind | null
): ForcePushBranchState {
  if (aheadBehind === null) {
    // no tracking branch found
    return ForcePushBranchState.NotAvailable
  }

  const { ahead, behind } = aheadBehind

  if (behind === 0 || ahead === 0) {
    // no a diverged branch to force push
    return ForcePushBranchState.NotAvailable
  }

  const { tip, forcePushBranches } = branchesState

  let canForcePushBranch = false
  if (tip.kind === TipState.Valid) {
    const localBranchName = tip.branch.nameWithoutRemote
    const { sha } = tip.branch.tip
    const foundEntry = forcePushBranches.get(localBranchName)
    canForcePushBranch = foundEntry === sha
  }

  return canForcePushBranch
    ? ForcePushBranchState.Recommended
    : ForcePushBranchState.Available
}
```

**File:** app/src/lib/git/push.ts (L66-70)
```typescript
  if (!remoteBranch) {
    args.push('--set-upstream')
  } else if (options?.forceWithLease) {
    args.push('--force-with-lease')
  }
```
