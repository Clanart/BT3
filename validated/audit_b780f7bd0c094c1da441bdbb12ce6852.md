[1](#0-0) [2](#0-1)

### Citations

**File:** app/src/lib/get-account-for-repository.ts (L26-40)
```typescript
export function getAccountForCommitMessageGeneration(
  accounts: ReadonlyArray<Account>,
  repository: Repository
): Account | undefined {
  // Prefer the account that is associated to this repository.
  const repositoryAccount = getAccountForRepository(accounts, repository)
  if (
    repositoryAccount !== null &&
    enableCommitMessageGeneration(repositoryAccount)
  ) {
    return repositoryAccount
  }

  return accounts.find(enableCommitMessageGeneration)
}
```

**File:** app/src/lib/get-account-for-repository.ts (L65-79)
```typescript
export function getAccountForCopilotConflictResolution(
  accounts: ReadonlyArray<Account>,
  repository: Repository
): Account | undefined {
  // Prefer the account that is associated to this repository.
  const repositoryAccount = getAccountForRepository(accounts, repository)
  if (
    repositoryAccount !== null &&
    isAccountEligibleForCopilotConflictResolution(repositoryAccount)
  ) {
    return repositoryAccount
  }

  return accounts.find(isAccountEligibleForCopilotConflictResolution)
}
```
