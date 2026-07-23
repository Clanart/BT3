# Q2101: Substitute a wrong proof path into fetch_new_blocks_backwards

## Question
Can an unprivileged attacker substitute part of attacker-controlled the header sequence, timestamps, and `bits` values so `fetch_new_blocks_backwards` accepts a proof, header, or path that should have been rejected, corrupting the canonical header-chain state and total work and breaking the invariant that canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/bitcoin_syncer.rs::fetch_new_blocks_backwards
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: the header sequence, timestamps, and `bits` values
- Exploit idea: swap part of attacker-controlled the header sequence, timestamps, and `bits` values while keeping the rest seemingly valid
- Invariant to test: canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
