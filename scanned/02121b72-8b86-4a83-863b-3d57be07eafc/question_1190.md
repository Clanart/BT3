# Q1190: NEAR bind_token callback partial deployment rollback leaves live alias at boundary values

## Question
Can an unprivileged attacker trigger `proof callback for public `bind_token`` with boundary-controlled inputs covering decimal caps, zero values, and normalization edges and make `near/omni-bridge/src/lib.rs::bind_token_callback` violate `a token binding must not be replayable or partially writable in a way that creates duplicate mappings, inconsistent lock rows, or underfunded state` in the `partial deployment rollback leaves live alias` attack class because adds the token mapping, initializes `locked_tokens` for the foreign asset, measures storage delta, and requires sufficient attached deposit before emitting the bind event becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::bind_token_callback`
- Entrypoint: `proof callback for public `bind_token``
- Attacker controls: decoded deploy-token message, chain kind, foreign token address, decimals, origin decimals, and attached deposit
- Exploit idea: Look for deployment flows that cross multiple contracts or callbacks before all state is committed. Concentrate on decimal caps, zero values, and normalization edges.
- Invariant to test: a token binding must not be replayable or partially writable in a way that creates duplicate mappings, inconsistent lock rows, or underfunded state
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force each subcall to fail independently and assert that the resulting state is either fully rolled back or fully usable, never half-bound. Sweep boundary values for decimal caps, zero values, and normalization edges and assert that the same invariant holds at every edge.
