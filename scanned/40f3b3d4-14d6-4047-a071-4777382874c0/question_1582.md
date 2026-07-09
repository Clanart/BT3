# Q1582: Starknet log_metadata fake bridge-controlled token accepted as canonical via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public Starknet metadata logging entrypoint` and then replay or reorder later bind, deploy, or metadata-consumption step so that `starknet/src/omni_bridge.cairo::log_metadata` ends up accepting two inconsistent interpretations of the same economic event specifically around `fake bridge-controlled token accepted as canonical` under performs low-level calls to infer whether metadata fields are returned as `felt252` or `ByteArray`, then emits a `LogMetadata` event, violating `metadata parsing across old and new Starknet token ABIs must not let one token produce ambiguous asset identity data on other chains`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::log_metadata`
- Entrypoint: `public Starknet metadata logging entrypoint`
- Attacker controls: token address and the token’s reported `name`, `symbol`, and `decimals` ABI behavior
- Exploit idea: Target checks that only inspect mint authority, owner, or one mapping row without proving the full asset identity. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: metadata parsing across old and new Starknet token ABIs must not let one token produce ambiguous asset identity data on other chains
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Construct plausible fake bridge-controlled assets and assert that deployment, settlement, and forwarding reject them unless they are the canonical mapping for that remote asset. Then replay or reorder later bind, deploy, or metadata-consumption step and assert that the bridge still exposes only one valid economic outcome.
