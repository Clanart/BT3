# Q3269: NEAR foreign/native token mapping lookup asset mapping drifts away from actual token semantics

## Question
Can an unprivileged attacker exploit `public multi-hop settlement flows that map tokens across chains` so that `near/omni-bridge/src/lib.rs::get_bridged_token` keeps a token mapped as canonical after its actual runtime semantics or backing assumptions diverge, violating `multi-chain mapping lookup must never return a different asset than the one collateral actually backs, especially in foreign-to-foreign forwarding`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_bridged_token`
- Entrypoint: `public multi-hop settlement flows that map tokens across chains`
- Attacker controls: source address, target chain, and any mapping state created by deploy/bind flows
- Exploit idea: Target upgrades, migration swaps, fake bridge-controlled tokens, and deploy callbacks.
- Invariant to test: multi-chain mapping lookup must never return a different asset than the one collateral actually backs, especially in foreign-to-foreign forwarding
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Change token semantics around an existing mapping and assert that the bridge does not keep treating the token as a valid canonical representation.
