# Q3822: EVM bridge-token proxy deploy rollback asset mapping drifts away from actual token semantics

## Question
Can an unprivileged attacker exploit `public EVM `deployToken`` so that `evm/src/omni-bridge/contracts/OmniBridge.sol::deployToken around proxy creation and mapping writes` keeps a token mapped as canonical after its actual runtime semantics or backing assumptions diverge, violating `deployment ordering must not allow a reentrant or partially-failed extension to leave a live token proxy outside the bridge’s canonical mapping or vice versa`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::deployToken around proxy creation and mapping writes`
- Entrypoint: `public EVM `deployToken``
- Attacker controls: token id, metadata fields, signature bytes, and reordering between proxy deployment and map writes
- Exploit idea: Target upgrades, migration swaps, fake bridge-controlled tokens, and deploy callbacks.
- Invariant to test: deployment ordering must not allow a reentrant or partially-failed extension to leave a live token proxy outside the bridge’s canonical mapping or vice versa
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Change token semantics around an existing mapping and assert that the bridge does not keep treating the token as a valid canonical representation.
