# Q2945: First-Proof Versus Uncle-Proof Double Reward After Partial State Change

## Question
Can an unprivileged attacker enter through `beefy_consensus_proofs::submit_proof(origin, proof)` with attacker-controlled proof bytes, prover account or nonce bindings, reward position, stale-proof timing, and replay ordering and replaying the same public flow after one part of storage changed and another part did not, and make `settle_first_proof` collect both first-proof and uncle-proof rewards for the same underlying proof statement so `the one-time reward state for one proof statement` becomes inconsistent with `a single reward position for that underlying statement`, breaking the invariant that first-proof and uncle-proof settlement must be mutually exclusive per underlying statement and prover position and leading to Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets?

## Target
- File/function: modules/pallets/beefy-consensus-proofs/src/lib.rs::settle_first_proof
- Entrypoint: beefy_consensus_proofs::submit_proof(origin, proof)
- Attacker controls: proof bytes, prover account or nonce bindings, reward position, stale-proof timing, and replay ordering
- Exploit idea: Collect both first-proof and uncle-proof rewards for the same underlying proof statement. Drive a partial success or revert path first, then replay the same user-controlled input and check whether stale state is reused.
- Invariant to test: first-proof and uncle-proof settlement must be mutually exclusive per underlying statement and prover position
- Expected Immunefi impact: Critical: wrongful mint, unlock, withdrawal, refund, or redirect of protocol-controlled or user escrowed assets.
- Fast validation: Drive a first-proof path and then the stale-proof uncle path for the same statement and assert total payout stays within one allowed position. Exercise a success-then-replay or fail-then-replay sequence and assert claimed flags, receipts, escrow, and nonces stay coherent.
