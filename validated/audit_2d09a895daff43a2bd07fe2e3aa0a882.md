[1](#0-0) [2](#0-1)

### Citations

**File:** app/src/lib/stores/repositories-store.ts (L144-159)
```typescript
  private async toRepository(repo: IDatabaseRepository) {
    assertNonNullable(repo.id, "can't convert to Repository without id")
    return new Repository(
      repo.path,
      repo.id,
      repo.gitHubRepositoryID !== null
        ? await this.findGitHubRepositoryByID(repo.gitHubRepositoryID)
        : await Promise.resolve(null), // Dexie gets confused if we return null
      repo.missing,
      repo.alias,
      repo.workflowPreferences,
      repo.isTutorialRepository,
      repo.gitDir,
      repo.mainWorktreePath
    )
  }
```

**File:** app/src/lib/stores/repositories-store.ts (L242-264)
```typescript
    const repository = await this.db.transaction(
      'rw',
      this.db.repositories,
      this.db.gitHubRepositories,
      this.db.owners,
      async () => {
        const existing = await this.db.repositories.get({ path })

        if (existing !== undefined) {
          return await this.toRepository(existing)
        }

        const dbRepo: IDatabaseRepository = {
          path,
          gitHubRepositoryID: null,
          missing: opts?.missing ?? false,
          lastStashCheckDate: null,
          alias: null,
          gitDir,
        }
        const id = await this.db.repositories.add(dbRepo)
        return this.toRepository({ id, ...dbRepo })
      }
```
