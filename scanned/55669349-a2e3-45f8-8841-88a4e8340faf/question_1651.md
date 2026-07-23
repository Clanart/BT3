# Q1651: Exploit witness/annex edge cases in verify_tar_image_digest_inspect_only

## Question
Can an unprivileged attacker exploit witness, annex, or script-path edge cases in the block/merkle proof nodes, indices, and ordering so `verify_tar_image_digest_inspect_only` verifies a different Bitcoin statement than later settlement relies on, corrupting the storage-proof key/value binding used for deposit or withdrawal validation and violating the invariant that canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: bridge-circuit-host/src/docker.rs::verify_tar_image_digest_inspect_only
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: the block/merkle proof nodes, indices, and ordering
- Exploit idea: make verification hash a different Bitcoin statement than settlement later uses via the block/merkle proof nodes, indices, and ordering
- Invariant to test: canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
