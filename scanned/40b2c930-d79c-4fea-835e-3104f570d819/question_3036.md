# Q3036: Corrupt work or canonical ordering in fetch_block_info_from_height

## Question
Can an unprivileged attacker shape the header sequence, timestamps, and `bits` values so `fetch_block_info_from_height` computes or compares work / ordering incorrectly, causing the wrong canonical chain or watchtower result to win, corrupting the watchtower ordering / max-total-work decision used to judge operator honesty and violating the invariant that canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view, leading to Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted?

## Target
- File/function: core/src/bitcoin_syncer.rs::fetch_block_info_from_height
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: the header sequence, timestamps, and `bits` values
- Exploit idea: make the wrong chain or watchtower result win by shaping the header sequence, timestamps, and `bits` values
- Invariant to test: canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view
- Expected Immunefi impact: Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
