# Q1446: NEAR per-chain token origin detection asset mapping drifts away from actual token semantics

## Question
Can an unprivileged attacker exploit `public init/finalize/lock flows through token-origin checks` so that `near/omni-bridge/src/lib.rs::get_token_origin_chain` keeps a token mapped as canonical after its actual runtime semantics or backing assumptions diverge, violating `origin-chain inference must never let an attacker make the bridge classify a token as native when it is wrapped, or vice versa, because that changes custody accounting`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_token_origin_chain`
- Entrypoint: `public init/finalize/lock flows through token-origin checks`
- Attacker controls: token account ids whose prefixes influence inferred origin chain and whether the token is in deployed sets or UTXO configs
- Exploit idea: Target upgrades, migration swaps, fake bridge-controlled tokens, and deploy callbacks.
- Invariant to test: origin-chain inference must never let an attacker make the bridge classify a token as native when it is wrapped, or vice versa, because that changes custody accounting
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Change token semantics around an existing mapping and assert that the bridge does not keep treating the token as a valid canonical representation.
