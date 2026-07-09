# Q926: Starknet log_metadata native versus wrapped registration confusion via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public Starknet metadata logging entrypoint` and then replay or reorder later bind, deploy, or metadata-consumption step so that `starknet/src/omni_bridge.cairo::log_metadata` ends up accepting two inconsistent interpretations of the same economic event specifically around `native versus wrapped registration confusion` under performs low-level calls to infer whether metadata fields are returned as `felt252` or `ByteArray`, then emits a `LogMetadata` event, violating `metadata parsing across old and new Starknet token ABIs must not let one token produce ambiguous asset identity data on other chains`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::log_metadata`
- Entrypoint: `public Starknet metadata logging entrypoint`
- Attacker controls: token address and the token’s reported `name`, `symbol`, and `decimals` ABI behavior
- Exploit idea: Target vault-existence checks, deployed-token caches, origin-chain inference, and custom-minter registration. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: metadata parsing across old and new Starknet token ABIs must not let one token produce ambiguous asset identity data on other chains
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Flip each classification predicate around existing mappings and assert that deployment and later settlement always preserve the same custody model. Then replay or reorder later bind, deploy, or metadata-consumption step and assert that the bridge still exposes only one valid economic outcome.
