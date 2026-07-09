# Q2608: NEAR bind_token callback native versus wrapped registration confusion

## Question
Can an unprivileged attacker reach `proof callback for public `bind_token`` and make `near/omni-bridge/src/lib.rs::bind_token_callback` treat a wrapped asset as native or a native asset as wrapped because of adds the token mapping, initializes `locked_tokens` for the foreign asset, measures storage delta, and requires sufficient attached deposit before emitting the bind event, violating `a token binding must not be replayable or partially writable in a way that creates duplicate mappings, inconsistent lock rows, or underfunded state`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::bind_token_callback`
- Entrypoint: `proof callback for public `bind_token``
- Attacker controls: decoded deploy-token message, chain kind, foreign token address, decimals, origin decimals, and attached deposit
- Exploit idea: Target vault-existence checks, deployed-token caches, origin-chain inference, and custom-minter registration.
- Invariant to test: a token binding must not be replayable or partially writable in a way that creates duplicate mappings, inconsistent lock rows, or underfunded state
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Flip each classification predicate around existing mappings and assert that deployment and later settlement always preserve the same custody model.
