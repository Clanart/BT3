# Q716: Exploit reorg boundary handling in deposit_constant

## Question
Can an unprivileged attacker exploit reorg timing around multiple kickoff/challenge/assert/disprove transactions referencing the same deposit context so `deposit_constant` treats a non-canonical object as canonical long enough to mutate bridge state, corrupting the kickoff/challenge/assert/disprove context treated as canonical for a deposit and violating the invariant that canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view, leading to Critical. Invalid state transition accepted as valid (soundness bug in proving/verifying/transition logic) leading to direct loss of funds or protocol insolvency?

## Target
- File/function: circuits-lib/src/bridge_circuit/mod.rs::deposit_constant
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: multiple kickoff/challenge/assert/disprove transactions referencing the same deposit context
- Exploit idea: reorder or replay multiple kickoff/challenge/assert/disprove transactions referencing the same deposit context across canonical and non-canonical views
- Invariant to test: canonical-chain tracking must never let a non-canonical header/tx/proof outrank the intended Bitcoin view
- Expected Immunefi impact: Critical. Invalid state transition accepted as valid (soundness bug in proving/verifying/transition logic) leading to direct loss of funds or protocol insolvency
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
