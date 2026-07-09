# Q786: NEAR per-chain token origin detection numeric cast or overflow changes economic meaning

## Question
Can an unprivileged attacker use `public init/finalize/lock flows through token-origin checks` to push `near/omni-bridge/src/lib.rs::get_token_origin_chain` through a cast, overflow, or truncation path that changes amount or nonce semantics, violating `origin-chain inference must never let an attacker make the bridge classify a token as native when it is wrapped, or vice versa, because that changes custody accounting`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_token_origin_chain`
- Entrypoint: `public init/finalize/lock flows through token-origin checks`
- Attacker controls: token account ids whose prefixes influence inferred origin chain and whether the token is in deployed sets or UTXO configs
- Exploit idea: Focus on `u128`/`u64`/`usize` casts, PDA bucket indices, and amount conversions around maximum values.
- Invariant to test: origin-chain inference must never let an attacker make the bridge classify a token as native when it is wrapped, or vice versa, because that changes custody accounting
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz maximal numbers and assert that every accepted numeric value preserves its economic meaning across all intermediate representations.
