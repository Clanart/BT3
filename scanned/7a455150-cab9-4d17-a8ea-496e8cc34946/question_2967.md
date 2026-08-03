# Q2967: Proof-To-Submitter Binding Bypass After Partial State Change

## Question
Can an unprivileged attacker enter through `beefy_consensus_proofs::submit_proof(origin, proof)` with attacker-controlled proof bytes, prover account or nonce bindings, reward position, stale-proof timing, and replay ordering and replaying the same public flow after one part of storage changed and another part did not, and make `offchain_key` claim reward for proof bytes that were cryptographically valid but committed to another prover identity so `the prover identity bound to the rewarded proof` becomes inconsistent with `the exact account or nonce committed inside the accepted proof`, breaking the invariant that rewarded proofs must bind to the exact submitter identity the proof committed to, not merely to valid cryptographic bytes and leading to Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets?

## Target
- File/function: modules/pallets/beefy-consensus-proofs/src/types.rs::offchain_key
- Entrypoint: beefy_consensus_proofs::submit_proof(origin, proof)
- Attacker controls: proof bytes, prover account or nonce bindings, reward position, stale-proof timing, and replay ordering
- Exploit idea: Claim reward for proof bytes that were cryptographically valid but committed to another prover identity. Drive a partial success or revert path first, then replay the same user-controlled input and check whether stale state is reused.
- Invariant to test: rewarded proofs must bind to the exact submitter identity the proof committed to, not merely to valid cryptographic bytes
- Expected Immunefi impact: Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets.
- Fast validation: Submit one valid proof from the rightful prover, then replay it or re-encode it from another account and assert no reward path opens. Exercise a success-then-replay or fail-then-replay sequence and assert claimed flags, receipts, escrow, and nonces stay coherent.
