[1](#0-0)

### Citations

**File:** app/src/lib/branch.ts (L19-26)
```typescript
export function findContributionTargetDefaultBranch(
  repository: Repository,
  { defaultBranch, upstreamDefaultBranch }: IBranchesState
): Branch | null {
  return isRepositoryWithGitHubRepository(repository)
    ? upstreamDefaultBranch ?? defaultBranch
    : defaultBranch
}
```
