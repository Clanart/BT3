# Q1174: Exploit ordering assumptions in verify_watchtower_challenges

## Question
Can an unprivileged attacker use attacker-controlled multiple kickoff/challenge/assert/disprove transactions referencing the same deposit context so `verify_watchtower_challenges` validates the right inputs in the wrong order, corrupting the canonical header-chain state and total work and breaking the invariant that canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view, leading to Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted?

## Target
- File/function: circuits-lib/src/bridge_circuit/mod.rs::verify_watchtower_challenges
- Entrypoint: broadcast a crafted Bitcoin kickoff/challenge/assert/disprove transaction that later reaches sync and verifier logic
- Attacker controls: multiple kickoff/challenge/assert/disprove transactions referencing the same deposit context
- Exploit idea: validate the right pieces in the wrong order using multiple kickoff/challenge/assert/disprove transactions referencing the same deposit context
- Invariant to test: canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view
- Expected Immunefi impact: Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
