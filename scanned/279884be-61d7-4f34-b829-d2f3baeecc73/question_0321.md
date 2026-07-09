# Q321: Solana signed-payload serialization recipient or message ambiguity via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public Solana deploy/init/finalize instructions` and then replay or reorder later bind, deploy, or metadata-consumption step so that `solana/programs/bridge_token_factory/src/state/message/* plus instruction callers` ends up accepting two inconsistent interpretations of the same economic event specifically around `recipient or message ambiguity` under serializes deploy, init, and finalize payloads into the bytes that Near later verifies and interprets, violating `serialized payloads must match Near’s decoding byte-for-byte so one Solana event cannot be verified as a different Near-side bridge action`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/state/message/* plus instruction callers`
- Entrypoint: `public Solana deploy/init/finalize instructions`
- Attacker controls: all payload fields that are serialized for Near and signed by the derived bridge key
- Exploit idea: Exploit non-canonical string, ByteArray, hex, or account-id forms to make one source-side intent resolve to a different destination-side recipient or message. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: serialized payloads must match Near’s decoding byte-for-byte so one Solana event cannot be verified as a different Near-side bridge action
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Cross-check source-side serialization against every downstream parser and assert that equivalent-looking inputs cannot resolve to distinct destination accounts or app messages. Then replay or reorder later bind, deploy, or metadata-consumption step and assert that the bridge still exposes only one valid economic outcome.
