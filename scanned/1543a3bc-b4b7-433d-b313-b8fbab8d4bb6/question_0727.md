# Q727: Exploit reorg boundary handling in total_work_from_wt_tx_test_util

## Question
Can an unprivileged attacker exploit reorg timing around multiple kickoff/challenge/assert/disprove transactions referencing the same deposit context so `total_work_from_wt_tx_test_util` treats a non-canonical object as canonical long enough to mutate bridge state, corrupting the L1 block hash carried from the light-client proof into bridge validation and violating the invariant that canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view, leading to High. Reorg-handling bug (Bitcoin-side or Citrea-side) that causes prolonged halt, inconsistent views, or unsafe rollback behavior?

## Target
- File/function: bridge-circuit-host/src/utils.rs::total_work_from_wt_tx_test_util
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: multiple kickoff/challenge/assert/disprove transactions referencing the same deposit context
- Exploit idea: reorder or replay multiple kickoff/challenge/assert/disprove transactions referencing the same deposit context across canonical and non-canonical views
- Invariant to test: canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view
- Expected Immunefi impact: High. Reorg-handling bug (Bitcoin-side or Citrea-side) that causes prolonged halt, inconsistent views, or unsafe rollback behavior
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
