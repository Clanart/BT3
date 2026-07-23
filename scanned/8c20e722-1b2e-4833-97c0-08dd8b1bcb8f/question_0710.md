# Q710: Exploit reorg boundary handling in verify_bridge_circuit

## Question
Can an unprivileged attacker exploit reorg timing around reorg timing around the same txid / outpoint / block height so `verify_bridge_circuit` treats a non-canonical object as canonical long enough to mutate bridge state, corrupting the watchtower ordering / max-total-work decision used to judge operator honesty and violating the invariant that kickoff/challenge/disprove handling must never let one deposit context inherit another deposit's proof or watchtower state, leading to Critical. Invalid state transition accepted as valid (soundness bug in proving/verifying/transition logic) leading to direct loss of funds or protocol insolvency?

## Target
- File/function: bridge-circuit-host/src/structs.rs::verify_bridge_circuit
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: reorg timing around the same txid / outpoint / block height
- Exploit idea: reorder or replay reorg timing around the same txid / outpoint / block height across canonical and non-canonical views
- Invariant to test: kickoff/challenge/disprove handling must never let one deposit context inherit another deposit's proof or watchtower state
- Expected Immunefi impact: Critical. Invalid state transition accepted as valid (soundness bug in proving/verifying/transition logic) leading to direct loss of funds or protocol insolvency
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
