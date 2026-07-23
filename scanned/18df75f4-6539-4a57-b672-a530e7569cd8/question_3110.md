# Q3110: Corrupt work or canonical ordering in calculate_work

## Question
Can an unprivileged attacker shape reorg timing around the same txid / outpoint / block height so `calculate_work` computes or compares work / ordering incorrectly, causing the wrong canonical chain or watchtower result to win, corrupting the L1 block hash carried from the light-client proof into bridge validation and violating the invariant that canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: circuits-lib/src/header_chain/mod.rs::calculate_work
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: reorg timing around the same txid / outpoint / block height
- Exploit idea: make the wrong chain or watchtower result win by shaping reorg timing around the same txid / outpoint / block height
- Invariant to test: canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
