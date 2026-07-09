# Q2723: Solana signed-payload serialization hashed or padded seed collision

## Question
Can an unprivileged attacker reach `public Solana deploy/init/finalize instructions` with overlong or adversarial token identifiers and make `solana/programs/bridge_token_factory/src/state/message/* plus instruction callers` derive the same local seed or salt for two remote assets because of serializes deploy, init, and finalize payloads into the bytes that Near later verifies and interprets, violating `serialized payloads must match Near’s decoding byte-for-byte so one Solana event cannot be verified as a different Near-side bridge action`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/state/message/* plus instruction callers`
- Entrypoint: `public Solana deploy/init/finalize instructions`
- Attacker controls: all payload fields that are serialized for Near and signed by the derived bridge key
- Exploit idea: Target hashed token strings, low-half salts, and deterministic-address truncation.
- Invariant to test: serialized payloads must match Near’s decoding byte-for-byte so one Solana event cannot be verified as a different Near-side bridge action
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search the seed space for collisions and assert that every derivation function preserves uniqueness of remote asset identity.
