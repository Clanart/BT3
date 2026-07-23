# Q684: Exploit reorg boundary handling in fetch_new_blocks_forward

## Question
Can an unprivileged attacker exploit reorg timing around the header sequence, timestamps, and `bits` values so `fetch_new_blocks_forward` treats a non-canonical object as canonical long enough to mutate bridge state, corrupting the SPV inclusion result for the payout transaction and violating the invariant that canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view, leading to Critical. Invalid state transition accepted as valid (soundness bug in proving/verifying/transition logic) leading to direct loss of funds or protocol insolvency?

## Target
- File/function: core/src/bitcoin_syncer.rs::fetch_new_blocks_forward
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: the header sequence, timestamps, and `bits` values
- Exploit idea: reorder or replay the header sequence, timestamps, and `bits` values across canonical and non-canonical views
- Invariant to test: canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view
- Expected Immunefi impact: Critical. Invalid state transition accepted as valid (soundness bug in proving/verifying/transition logic) leading to direct loss of funds or protocol insolvency
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
