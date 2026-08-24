Based on the searches performed, the closest structural analog to the Connext bug class ("a fraudulent/malicious entry, once trusted, has no removal path and is propagated to every future consumer") in GitHub Desktop is the **SSH host-trust flow**, where a single user acceptance of an unverified host key is persisted and Desktop provides no way to revoke it afterward.

### Title
Irrevocable trust of a malicious SSH host key with no in-app revocation path - (File: app/src/lib/trampoline/trampoline-askpass-handler.ts)

### Summary
When `ssh` challenges Desktop with "The authenticity of host '...' can't be established," the app parses the prompt and shows the user a one-time Yes/No dialog. If the user answers "yes"—including under an active MITM condition—the host key becomes permanently trusted for every subsequent git operation against that host, and Desktop exposes no UI or store method to invalidate/remove that decision, mirroring the "no way of removing fraudulent roots once accepted" defect class from the report.

### Finding Description
`parseAddSSHHostPrompt` extracts `host`, `ip`, `keyType`, and `fingerprint` from the raw SSH prompt text [1](#0-0) . `handleSSHHostAuthenticity` auto-accepts only the hardcoded GitHub.com RSA fingerprint; for every other host (GHE, self-hosted git servers, proxies) it forwards the decision to a user-facing popup [2](#0-1) . The popup (`AddSSHHost`) collects a single boolean and resolves the trust decision back to `ssh` via the trampoline [3](#0-2) [4](#0-3) . Once `ssh` receives "yes," it writes the (possibly forged) key into the user's `known_hosts` file — a decision entirely outside of GitHub Desktop's control from that point forward. A grep of the codebase for `known_hosts` handling shows the only references are in vendor test fixtures [5](#0-4) ; there is no store, dialog, settings pane, or IPC handler anywhere in `app/src` that lets a user list, inspect, or remove a previously trusted SSH host key from within Desktop. The only related "removal" capability that exists, `removeMostRecentSSHCredential`, is scoped to password/passphrase secrets for a failed operation, not to host-key trust [6](#0-5) .

### Impact Explanation
If a user is momentarily on a hostile network path (open Wi-Fi, compromised proxy/DNS, corporate MITM box) when Desktop first talks to a git remote over SSH, and mistakenly accepts the unfamiliar host-key prompt, that attacker-controlled key becomes trusted indefinitely. Every future push/pull/fetch to that hostname will transparently route through the attacker's SSH endpoint with no further warning, exactly like the "fraudulent root" that keeps propagating once accepted, because Desktop offers no path to undo the trust decision short of the user manually editing `~/.ssh/known_hosts` outside the app (a step nowhere referenced or surfaced in Desktop's UI). This can lead to interception/manipulation of push/pull traffic (silent corruption of what is pushed/pulled) and exposure of git credentials/SSH traffic to the attacker on every subsequent operation.

### Likelihood Explanation
Low-to-moderate: it requires a single moment of attacker-in-the-path network position plus a user misclick on a legitimate-looking Yes/No dialog (analogous to any first-connection TOFU SSH prompt), not local/physical access, malware, or leaked credentials. Given TOFU (trust-on-first-use) is inherently a one-shot decision point, and Desktop adds no compensating "review/remove trusted hosts" control, the residual risk after a bad first decision is unusually persistent and silent.

### Recommendation
Add a settings surface (e.g., under Git/SSH preferences) that lists SSH hosts trusted via the `AddSSHHost` flow and allows the user to remove/re-verify individual entries, forcing `ssh` to re-prompt on next connection. Consider also logging/notifying the user the first time a *previously different* fingerprint is seen for a host they already trust (key-change detection), rather than relying solely on system `ssh`'s own default behavior.

### Proof of Concept
1. Position as an on-path attacker (e.g., ARP/DNS spoofing on shared Wi-Fi) between the victim and `git.internal.example.com`, terminating SSH with an attacker-controlled key.
2. Victim opens Desktop and performs a fetch/clone against `git.internal.example.com` for the first time; Desktop's trampoline surfaces the `AddSSHHost` dialog with the attacker's fingerprint [7](#0-6) .
3. Victim clicks "Yes," trusting the attacker's key; `ssh` persists it to `known_hosts`.
4. Attacker now transparently intercepts all subsequent SSH git traffic to that host from Desktop.
5. Victim later suspects compromise but finds no way within Desktop to view or remove the trusted host entry — confirmed by the absence of any `known_hosts` management code outside of the vendor test fixture [5](#0-4) .

**Caveat on confidence:** this is a structural analog (irrevocable trust decision with no removal UI) rather than an exact match to the original Solidity `RootManager` bug. I could not find any Desktop-specific queue/list of "roots" or similarly-propagated fraud objects; the SSH host-trust flow was the strongest local-code match satisfying the stated attacker model (git remote/proxy response) and impact criteria (credential exposure, silent corruption of push/pull). If this analog is judged too indirect, I did not find a stronger match in the explored areas (submodule handling, avatar/image token injection, remote URL parsing, retry/multi-operation queues).

### Citations

**File:** app/src/lib/ssh/ssh.ts (L72-87)
```typescript
export function parseAddSSHHostPrompt(prompt: string) {
  const promptRegex =
    /^The authenticity of host '([^ ]+) \(([^\)]+)\)' can't be established[^.]*\.\n([^ ]+) key fingerprint is ([^.]+)\./

  const matches = promptRegex.exec(prompt)
  if (matches === null || matches.length < 5) {
    return null
  }

  return {
    host: matches[1],
    ip: matches[2],
    keyType: matches[3],
    fingerprint: matches[4],
  }
}
```

**File:** app/src/lib/trampoline/trampoline-askpass-handler.ts (L18-53)
```typescript
async function handleSSHHostAuthenticity(
  operationGUID: string,
  prompt: string
): Promise<'yes' | 'no' | undefined> {
  const info = parseAddSSHHostPrompt(prompt)

  if (info === null) {
    return undefined
  }

  // We'll accept github.com as valid host automatically. GitHub's public key
  // fingerprint can be obtained from
  // https://docs.github.com/en/github/authenticating-to-github/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints
  if (
    info.host === 'github.com' &&
    info.keyType === 'RSA' &&
    info.fingerprint === 'SHA256:nThbg6kXUpJWGl7E1IGOCspRomTxdCARLviKw6E5SY8'
  ) {
    return 'yes'
  }

  if (getIsBackgroundTaskEnvironment(operationGUID)) {
    log.debug(
      'handleSSHHostAuthenticity: background task environment, skipping prompt'
    )
    return undefined
  }

  const addHost = await trampolineUIHelper.promptAddingSSHHost(
    info.host,
    info.ip,
    info.keyType,
    info.fingerprint
  )
  return addHost ? 'yes' : 'no'
}
```

**File:** app/src/lib/trampoline/trampoline-askpass-handler.ts (L55-106)
```typescript
async function handleSSHKeyPassphrase(
  operationGUID: string,
  prompt: string
): Promise<string | undefined> {
  const promptRegex = /^Enter passphrase for key '(.+)': $/

  const matches = promptRegex.exec(prompt)
  if (matches === null || matches.length < 2) {
    return undefined
  }

  let keyPath = matches[1]

  // The ssh bundled with Desktop on Windows, for some reason, provides Unix-like
  // paths for the keys (e.g. /c/Users/.../id_rsa). We need to convert them to
  // Windows-like paths (e.g. C:\Users\...\id_rsa).
  if (__WIN32__ && /^\/\w\//.test(keyPath)) {
    const driveLetter = keyPath[1]
    keyPath = keyPath.slice(2)
    keyPath = `${driveLetter}:${keyPath}`
  }

  const storedPassphrase = await getSSHKeyPassphrase(keyPath)
  if (storedPassphrase !== null) {
    // Keep this stored passphrase around in case it's not valid and we need to
    // delete it if the git operation fails to authenticate.
    await setMostRecentSSHKeyPassphrase(operationGUID, keyPath)
    return storedPassphrase
  }

  if (getIsBackgroundTaskEnvironment(operationGUID)) {
    log.debug(
      'handleSSHKeyPassphrase: background task environment, skipping prompt'
    )
    return undefined
  }

  const { secret: passphrase, storeSecret: storePassphrase } =
    await trampolineUIHelper.promptSSHKeyPassphrase(keyPath)

  // If the user wanted us to remember the passphrase, we'll keep it around to
  // store it later if the git operation succeeds.
  // However, when running a git command, it's possible that the user will need
  // to enter the passphrase multiple times if there are failed attempts.
  // Because of that, we need to remove any pending passphrases to be stored
  // when, in one of those multiple attempts, the user chooses NOT to remember
  // the passphrase.
  if (passphrase !== undefined && storePassphrase) {
    setSSHKeyPassphrase(operationGUID, keyPath, passphrase)
  } else {
    removeMostRecentSSHCredential(operationGUID)
  }
```

**File:** app/src/ui/ssh/add-ssh-host.tsx (L17-61)
```typescript
export class AddSSHHost extends React.Component<IAddSSHHostProps> {
  public render() {
    return (
      <Dialog
        id="add-ssh-host"
        type="normal"
        title="SSH Host"
        backdropDismissable={false}
        onSubmit={this.onSubmit}
        onDismissed={this.onCancel}
      >
        <DialogContent>
          <p>
            The authenticity of host '{this.props.host} ({this.props.ip})' can't
            be established. {this.props.keyType} key fingerprint is{' '}
            {this.props.fingerprint}.
          </p>
          <p>Are you sure you want to continue connecting?</p>
        </DialogContent>
        <DialogFooter>
          <OkCancelButtonGroup
            okButtonText="Yes"
            cancelButtonText="No"
            onCancelButtonClick={this.onCancel}
          />
        </DialogFooter>
      </Dialog>
    )
  }

  private submit(addHost: boolean) {
    const { onSubmit, onDismissed } = this.props

    onSubmit(addHost)
    onDismissed()
  }

  private onSubmit = () => {
    this.submit(true)
  }

  private onCancel = () => {
    this.submit(false)
  }
}
```

**File:** app/src/lib/trampoline/trampoline-ui-helper.ts (L20-36)
```typescript
  public promptAddingSSHHost(
    host: string,
    ip: string,
    keyType: string,
    fingerprint: string
  ): Promise<boolean> {
    return new Promise(resolve => {
      this.dispatcher.showPopup({
        type: PopupType.AddSSHHost,
        host,
        ip,
        keyType,
        fingerprint,
        onSubmit: addHost => resolve(addHost),
      })
    })
  }
```

**File:** vendor/desktop-trampoline/test/ssh-wrapper-test.ts (L26-32)
```typescript
  it('attempts to use ssh-askpass program', async () => {
    // Try to connect to github.com with a non-existent known_hosts file to force
    // ssh to prompt the user and use askpass.
    const result = await run(
      sshWrapperPath,
      ['-o', 'UserKnownHostsFile=/path/to/fake/known_hosts', 'git@github.com'],
      {
```
