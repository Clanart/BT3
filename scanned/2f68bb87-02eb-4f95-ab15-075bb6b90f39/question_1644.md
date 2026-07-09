# Q1644: Solana signed-payload serialization state update before full validation via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public Solana deploy/init/finalize instructions` and then replay or reorder later bind, deploy, or metadata-consumption step so that `solana/programs/bridge_token_factory/src/state/message/* plus instruction callers` ends up accepting two inconsistent interpretations of the same economic event specifically around `state update before full validation` under serializes deploy, init, and finalize payloads into the bytes that Near later verifies and interprets, violating `serialized payloads must match Near’s decoding byte-for-byte so one Solana event cannot be verified as a different Near-side bridge action`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/state/message/* plus instruction callers`
- Entrypoint: `public Solana deploy/init/finalize instructions`
- Attacker controls: all payload fields that are serialized for Near and signed by the derived bridge key
- Exploit idea: Look for `completed`, `finalised`, or bitmap writes that happen before every branch-specific validation step and external effect. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: serialized payloads must match Near’s decoding byte-for-byte so one Solana event cannot be verified as a different Near-side bridge action
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force later validation or delivery to fail after replay state was consumed and assert that the transfer cannot be stranded or reopened inconsistently. Then replay or reorder later bind, deploy, or metadata-consumption step and assert that the bridge still exposes only one valid economic outcome.
