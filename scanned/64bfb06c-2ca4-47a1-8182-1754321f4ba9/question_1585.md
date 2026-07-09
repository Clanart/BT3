# Q1585: Starknet init_transfer recipient or message ambiguity via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public Starknet outbound transfer entrypoint` and then replay or reorder the later settlement leg on another chain so that `starknet/src/omni_bridge.cairo::init_transfer` ends up accepting two inconsistent interpretations of the same economic event specifically around `recipient or message ambiguity` under checks pause flags, increments the origin nonce, burns bridge tokens when required, and emits an `InitTransfer` event using a normalized token address, violating `one outbound Starknet transfer must consume the exact caller-held asset that the event later authorizes on destination chains`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::init_transfer`
- Entrypoint: `public Starknet outbound transfer entrypoint`
- Attacker controls: token address, amount, fee, native fee, recipient `ByteArray`, message `ByteArray`, and caller
- Exploit idea: Exploit non-canonical string, ByteArray, hex, or account-id forms to make one source-side intent resolve to a different destination-side recipient or message. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: one outbound Starknet transfer must consume the exact caller-held asset that the event later authorizes on destination chains
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Cross-check source-side serialization against every downstream parser and assert that equivalent-looking inputs cannot resolve to distinct destination accounts or app messages. Then replay or reorder the later settlement leg on another chain and assert that the bridge still exposes only one valid economic outcome.
