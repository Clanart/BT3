# Q1180: Exploit ordering assumptions in verify_tar_image_digest

## Question
Can an unprivileged attacker use attacker-controlled the block/merkle proof nodes, indices, and ordering so `verify_tar_image_digest` validates the right inputs in the wrong order, corrupting the SPV inclusion result for the payout transaction and breaking the invariant that canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: bridge-circuit-host/src/docker.rs::verify_tar_image_digest
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: the block/merkle proof nodes, indices, and ordering
- Exploit idea: validate the right pieces in the wrong order using the block/merkle proof nodes, indices, and ordering
- Invariant to test: canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
