# Q1617: Exploit witness/annex edge cases in new_with_proof_assumption

## Question
Can an unprivileged attacker exploit witness, annex, or script-path edge cases in Bitcoin transaction shape, witness bytes, annexes, and OP_RETURN contents so `new_with_proof_assumption` verifies a different Bitcoin statement than later settlement relies on, corrupting the watchtower ordering / max-total-work decision used to judge operator honesty and violating the invariant that canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view, leading to Critical. Invalid state transition accepted as valid (soundness bug in proving/verifying/transition logic) leading to direct loss of funds or protocol insolvency?

## Target
- File/function: core/src/header_chain_prover.rs::new_with_proof_assumption
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: Bitcoin transaction shape, witness bytes, annexes, and OP_RETURN contents
- Exploit idea: make verification hash a different Bitcoin statement than settlement later uses via Bitcoin transaction shape, witness bytes, annexes, and OP_RETURN contents
- Invariant to test: canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view
- Expected Immunefi impact: Critical. Invalid state transition accepted as valid (soundness bug in proving/verifying/transition logic) leading to direct loss of funds or protocol insolvency
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
