[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** app/src/lib/stores/pull-request-store.ts (L256-266)
```typescript
    const upsertRepo = mem(store.upsertGitHubRepositoryLight.bind(store), {
      // The first argument which we're ignoring here is the endpoint
      // which is constant throughout the lifetime of this function.
      // The second argument is an `IAPIRepository` which is basically
      // the raw object that we got from the API which could consist of
      // more than just the fields we've modelled in the interface. The
      // only thing we really care about to determine whether the
      // repository has already been inserted in the database is the clone
      // url since that's what the upsert method uses as its key.
      cacheKey: (_, repo) => repo.clone_url,
    })
```

**File:** app/src/lib/stores/repositories-store.ts (L610-616)
```typescript
    const { login, type } = gitHubRepository.owner
    const owner = await this.putOwner(endpoint, login, type)

    const existingRepo = await this.db.gitHubRepositories
      .where('[ownerID+name]')
      .equals([owner.id, gitHubRepository.name])
      .first()
```

**File:** app/src/lib/databases/repositories-database.ts (L7-17)
```typescript
export interface IDatabaseOwner {
  readonly id?: number
  /**
   * A case-insensitive lookup key which uniquely identifies a particular
   * user on a particular endpoint. See getOwnerKey for more information.
   */
  readonly key: string
  readonly login: string
  readonly endpoint: string
  readonly type?: GitHubAccountType
}
```
