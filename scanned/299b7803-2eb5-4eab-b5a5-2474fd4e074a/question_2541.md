# Q2541: NEAR per-chain token origin detection custody accounting diverges from wrapped supply at boundary values

## Question
Can an unprivileged attacker trigger `public init/finalize/lock flows through token-origin checks` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-bridge/src/lib.rs::get_token_origin_chain` violate `origin-chain inference must never let an attacker make the bridge classify a token as native when it is wrapped, or vice versa, because that changes custody accounting` in the `custody accounting diverges from wrapped supply` attack class because infers origin chain from deployment caches, UTXO config, or token-account naming conventions before deciding whether to lock/unlock liquidity becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_token_origin_chain`
- Entrypoint: `public init/finalize/lock flows through token-origin checks`
- Attacker controls: token account ids whose prefixes influence inferred origin chain and whether the token is in deployed sets or UTXO configs
- Exploit idea: Target branches that mint, burn, lock, unlock, transfer vault assets, or unwrap native value. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: origin-chain inference must never let an attacker make the bridge classify a token as native when it is wrapped, or vice versa, because that changes custody accounting
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build a per-asset conservation model and assert that total claims never exceed total backing after every public flow. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
