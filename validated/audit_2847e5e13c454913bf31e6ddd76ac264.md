[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** app/src/ui/add-repository/sanitized-repository-name.ts (L8-11)
```typescript
/** Sanitize a proposed repository name by replacing illegal characters. */
export function sanitizedRepositoryName(name: string): string {
  return name.replace(emojiRegExp, '-').replace(/[^\w.-]/g, '-')
}
```

**File:** app/src/ui/publish-repository/publish-repository.tsx (L196-208)
```typescript
  private renderSanitizedName() {
    const sanitizedName = this.props.settings.name
    if (this.name === sanitizedName) {
      return null
    }

    return (
      <Row className="warning-helper-text">
        <Octicon symbol={octicons.alert} />
        Will be created as {sanitizedName}
      </Row>
    )
  }
```

**File:** app/src/lib/api.ts (L1103-1118)
```typescript
  /** Create a new GitHub repository with the given properties. */
  public async createRepository(
    org: IAPIOrganization | null,
    name: string,
    description: string,
    private_: boolean
  ): Promise<IAPIFullRepository> {
    try {
      const apiPath = org ? `orgs/${org.login}/repos` : 'user/repos'
      const response = await this.ghRequest('POST', apiPath, {
        body: {
          name,
          description,
          private: private_,
        },
      })
```

**File:** app/src/lib/stores/app-store.ts (L5627-5641)
```typescript
  public async _publishRepository(
    repository: Repository,
    name: string,
    description: string,
    private_: boolean,
    account: Account,
    org: IAPIOrganization | null
  ): Promise<Repository> {
    const api = API.fromAccount(account)
    const apiRepository = await api.createRepository(
      org,
      name,
      description,
      private_
    )
```

**File:** app/src/ui/publish-repository/publish.tsx (L105-109)
```typescript
    const publicationSettings = {
      name: props.repository.name,
      description: '',
      private: true,
    }
```

**File:** app/src/ui/publish-repository/publish.tsx (L342-353)
```typescript
    const settings = currentTabState.settings
    const { org } = currentTabState.settings

    try {
      await this.props.dispatcher.publishRepository(
        this.props.repository,
        settings.name,
        settings.description,
        settings.private,
        account,
        org
      )
```

**File:** app/src/ui/lib/repository-path.tsx (L11-25)
```typescript
// We use this instead of sanitizedRepositoryName because it deals with
// valid repository names on GitHub.com but here we only care about whether
// we'll be able to create a directory with the given name. If a user
// creates a repository with a name that GitHub.com doesn't like here it'll
// get sanitized in the Publish dialog later on.
//
// Note that we don't sanitize `\` or `/` here since we use `Path.join` to
// create the full path and that will handle those characters appropriately
// letting users type something like OrgA\RepoB and have the new repo be
// created in the OrgA folder.
//
// macOS and Linux are way more allowing so there's no need to sanitize
const safeDirectoryName = (name: string) => {
  return __WIN32__ ? name.replace(/[<>:"|?*]/g, '-').replace(/\s+$/, '') : name
}
```
