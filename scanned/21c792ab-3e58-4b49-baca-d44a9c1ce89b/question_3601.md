# Q3601: NEAR bind_token callback fake bridge-controlled token accepted as canonical at boundary values

## Question
Can an unprivileged attacker trigger `proof callback for public `bind_token`` with boundary-controlled inputs covering decimal caps, zero values, and normalization edges and make `near/omni-bridge/src/lib.rs::bind_token_callback` violate `a token binding must not be replayable or partially writable in a way that creates duplicate mappings, inconsistent lock rows, or underfunded state` in the `fake bridge-controlled token accepted as canonical` attack class because adds the token mapping, initializes `locked_tokens` for the foreign asset, measures storage delta, and requires sufficient attached deposit before emitting the bind event becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::bind_token_callback`
- Entrypoint: `proof callback for public `bind_token``
- Attacker controls: decoded deploy-token message, chain kind, foreign token address, decimals, origin decimals, and attached deposit
- Exploit idea: Target checks that only inspect mint authority, owner, or one mapping row without proving the full asset identity. Concentrate on decimal caps, zero values, and normalization edges.
- Invariant to test: a token binding must not be replayable or partially writable in a way that creates duplicate mappings, inconsistent lock rows, or underfunded state
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Construct plausible fake bridge-controlled assets and assert that deployment, settlement, and forwarding reject them unless they are the canonical mapping for that remote asset. Sweep boundary values for decimal caps, zero values, and normalization edges and assert that the same invariant holds at every edge.
