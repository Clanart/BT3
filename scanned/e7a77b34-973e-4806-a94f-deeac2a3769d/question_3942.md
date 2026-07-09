# Q3942: Starknet old/new metadata ABI detection Starknet metadata ABI split changes remote asset identity via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public Starknet `log_metadata`` and then replay or reorder later bind, deploy, or metadata-consumption step so that `starknet/src/omni_bridge.cairo::log_metadata low-level ABI branching` ends up accepting two inconsistent interpretations of the same economic event specifically around `Starknet metadata ABI split changes remote asset identity` under switches between old-style felt252 metadata and new-style ByteArray metadata based on return-data length from untrusted token contracts, violating `ABI detection must not let a malicious token return shape-shifted metadata that downstream chains interpret as another asset identity`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::log_metadata low-level ABI branching`
- Entrypoint: `public Starknet `log_metadata``
- Attacker controls: token contract that can return `name`/`symbol` as one felt, many felts, or malformed ABI-encoded data
- Exploit idea: Exploit mixed old-style felt and new-style ByteArray return conventions from arbitrary token contracts. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: ABI detection must not let a malicious token return shape-shifted metadata that downstream chains interpret as another asset identity
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Return ambiguous metadata payloads and assert that the bridge rejects or canonically normalizes them before remote deployment. Then replay or reorder later bind, deploy, or metadata-consumption step and assert that the bridge still exposes only one valid economic outcome.
