# Q3912: Starknet log_metadata Starknet metadata ABI split changes remote asset identity via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public Starknet metadata logging entrypoint` and then replay or reorder later bind, deploy, or metadata-consumption step so that `starknet/src/omni_bridge.cairo::log_metadata` ends up accepting two inconsistent interpretations of the same economic event specifically around `Starknet metadata ABI split changes remote asset identity` under performs low-level calls to infer whether metadata fields are returned as `felt252` or `ByteArray`, then emits a `LogMetadata` event, violating `metadata parsing across old and new Starknet token ABIs must not let one token produce ambiguous asset identity data on other chains`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::log_metadata`
- Entrypoint: `public Starknet metadata logging entrypoint`
- Attacker controls: token address and the token’s reported `name`, `symbol`, and `decimals` ABI behavior
- Exploit idea: Exploit mixed old-style felt and new-style ByteArray return conventions from arbitrary token contracts. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: metadata parsing across old and new Starknet token ABIs must not let one token produce ambiguous asset identity data on other chains
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Return ambiguous metadata payloads and assert that the bridge rejects or canonically normalizes them before remote deployment. Then replay or reorder later bind, deploy, or metadata-consumption step and assert that the bridge still exposes only one valid economic outcome.
