# Q693: Exploit reorg boundary handling in save_unproven_block_cache

## Question
Can an unprivileged attacker exploit reorg timing around Bitcoin transaction shape, witness bytes, annexes, and OP_RETURN contents so `save_unproven_block_cache` treats a non-canonical object as canonical long enough to mutate bridge state, corrupting the L1 block hash carried from the light-client proof into bridge validation and violating the invariant that canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view, leading to Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted?

## Target
- File/function: core/src/header_chain_prover.rs::save_unproven_block_cache
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: Bitcoin transaction shape, witness bytes, annexes, and OP_RETURN contents
- Exploit idea: reorder or replay Bitcoin transaction shape, witness bytes, annexes, and OP_RETURN contents across canonical and non-canonical views
- Invariant to test: canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view
- Expected Immunefi impact: Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
