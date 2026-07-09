# Q1483: Solana signed-payload serialization state update before full validation

## Question
Can an unprivileged attacker exploit `public Solana deploy/init/finalize instructions` so that `solana/programs/bridge_token_factory/src/state/message/* plus instruction callers` mutates finalization state before all signature or proof checks implied by serializes deploy, init, and finalize payloads into the bytes that Near later verifies and interprets are complete, violating `serialized payloads must match Near’s decoding byte-for-byte so one Solana event cannot be verified as a different Near-side bridge action`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/state/message/* plus instruction callers`
- Entrypoint: `public Solana deploy/init/finalize instructions`
- Attacker controls: all payload fields that are serialized for Near and signed by the derived bridge key
- Exploit idea: Look for `completed`, `finalised`, or bitmap writes that happen before every branch-specific validation step and external effect.
- Invariant to test: serialized payloads must match Near’s decoding byte-for-byte so one Solana event cannot be verified as a different Near-side bridge action
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force later validation or delivery to fail after replay state was consumed and assert that the transfer cannot be stranded or reopened inconsistently.
