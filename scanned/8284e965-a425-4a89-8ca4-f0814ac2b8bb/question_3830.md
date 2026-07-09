# Q3830: Solana signed-payload serialization truncated seed or salt aliases remote assets

## Question
Can an unprivileged attacker reach `public Solana deploy/init/finalize instructions` and make `solana/programs/bridge_token_factory/src/state/message/* plus instruction callers` truncate or hash remote asset identifiers in a way that aliases two deployable assets, violating `serialized payloads must match Near’s decoding byte-for-byte so one Solana event cannot be verified as a different Near-side bridge action`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/state/message/* plus instruction callers`
- Entrypoint: `public Solana deploy/init/finalize instructions`
- Attacker controls: all payload fields that are serialized for Near and signed by the derived bridge key
- Exploit idea: Target low-half salts, 20-byte address truncation, hashed token strings, and fixed-width seed buffers.
- Invariant to test: serialized payloads must match Near’s decoding byte-for-byte so one Solana event cannot be verified as a different Near-side bridge action
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for seed collisions and assert that distinct remote assets cannot share a local deploy address or mint PDA.
