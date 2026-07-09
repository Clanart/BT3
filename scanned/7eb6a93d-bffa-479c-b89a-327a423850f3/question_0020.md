# Q20: NEAR bind_token callback canonical token identity collision

## Question
Can an unprivileged attacker reach `proof callback for public `bind_token`` with a valid-looking remote asset identity and make `near/omni-bridge/src/lib.rs::bind_token_callback` map it onto an existing local token because of adds the token mapping, initializes `locked_tokens` for the foreign asset, measures storage delta, and requires sufficient attached deposit before emitting the bind event, violating `a token binding must not be replayable or partially writable in a way that creates duplicate mappings, inconsistent lock rows, or underfunded state`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::bind_token_callback`
- Entrypoint: `proof callback for public `bind_token``
- Attacker controls: decoded deploy-token message, chain kind, foreign token address, decimals, origin decimals, and attached deposit
- Exploit idea: Target hashed token ids, deterministic synthetic addresses, PDA seeds, and address-to-token maps.
- Invariant to test: a token binding must not be replayable or partially writable in a way that creates duplicate mappings, inconsistent lock rows, or underfunded state
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for collisions and alias conditions and assert that two distinct remote assets cannot share one local token identity or mapping row.
