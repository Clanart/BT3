# Q2689: NEAR per-chain token origin detection origin inference changes custody branch

## Question
Can an unprivileged attacker choose a token through `public init/finalize/lock flows through token-origin checks` such that `near/omni-bridge/src/lib.rs::get_token_origin_chain` infers the wrong origin chain from naming, caches, or config, violating `origin-chain inference must never let an attacker make the bridge classify a token as native when it is wrapped, or vice versa, because that changes custody accounting`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_token_origin_chain`
- Entrypoint: `public init/finalize/lock flows through token-origin checks`
- Attacker controls: token account ids whose prefixes influence inferred origin chain and whether the token is in deployed sets or UTXO configs
- Exploit idea: Probe naming-convention inference and cache invalidation around deployed or migrated tokens.
- Invariant to test: origin-chain inference must never let an attacker make the bridge classify a token as native when it is wrapped, or vice versa, because that changes custody accounting
- Expected Immunefi impact: Balance manipulation
- Fast validation: Generate tokens near every naming boundary and assert that origin inference matches the canonical mapping and custody model.
