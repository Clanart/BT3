# Q1517: NEAR bind_token callback decimal cap creates wrong economic model via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `proof callback for public `bind_token`` and then replay or reorder later callback or refund resolution so that `near/omni-bridge/src/lib.rs::bind_token_callback` ends up accepting two inconsistent interpretations of the same economic event specifically around `decimal cap creates wrong economic model` under adds the token mapping, initializes `locked_tokens` for the foreign asset, measures storage delta, and requires sufficient attached deposit before emitting the bind event, violating `a token binding must not be replayable or partially writable in a way that creates duplicate mappings, inconsistent lock rows, or underfunded state`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::bind_token_callback`
- Entrypoint: `proof callback for public `bind_token``
- Attacker controls: decoded deploy-token message, chain kind, foreign token address, decimals, origin decimals, and attached deposit
- Exploit idea: Target capped decimals on EVM, Solana, and Starknet deployments and later amount conversions during sign/finalize/claim flows. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: a token binding must not be replayable or partially writable in a way that creates duplicate mappings, inconsistent lock rows, or underfunded state
- Expected Immunefi impact: Balance manipulation
- Fast validation: Deploy high-decimal assets and assert that every later amount conversion preserves one consistent economic relation to the source asset. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
