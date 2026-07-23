# Q1634: Exploit witness/annex edge cases in prove_block_headers

## Question
Can an unprivileged attacker exploit witness, annex, or script-path edge cases in Bitcoin transaction shape, witness bytes, annexes, and OP_RETURN contents so `prove_block_headers` verifies a different Bitcoin statement than later settlement relies on, corrupting the watchtower ordering / max-total-work decision used to judge operator honesty and violating the invariant that canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view, leading to Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted?

## Target
- File/function: core/src/header_chain_prover.rs::prove_block_headers
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: Bitcoin transaction shape, witness bytes, annexes, and OP_RETURN contents
- Exploit idea: make verification hash a different Bitcoin statement than settlement later uses via Bitcoin transaction shape, witness bytes, annexes, and OP_RETURN contents
- Invariant to test: canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view
- Expected Immunefi impact: Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
