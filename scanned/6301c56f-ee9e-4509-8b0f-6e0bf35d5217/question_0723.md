# Q723: Exploit reorg boundary handling in work_only_from_header_chain_test

## Question
Can an unprivileged attacker exploit reorg timing around the header sequence, timestamps, and `bits` values so `work_only_from_header_chain_test` treats a non-canonical object as canonical long enough to mutate bridge state, corrupting the storage-proof key/value binding used for deposit or withdrawal validation and violating the invariant that proof verification must bind method id, network, genesis context, proof inputs, and on-chain data to one canonical statement, leading to Critical. Invalid state transition accepted as valid (soundness bug in proving/verifying/transition logic) leading to direct loss of funds or protocol insolvency?

## Target
- File/function: bridge-circuit-host/src/bridge_circuit_host.rs::work_only_from_header_chain_test
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: the header sequence, timestamps, and `bits` values
- Exploit idea: reorder or replay the header sequence, timestamps, and `bits` values across canonical and non-canonical views
- Invariant to test: proof verification must bind method id, network, genesis context, proof inputs, and on-chain data to one canonical statement
- Expected Immunefi impact: Critical. Invalid state transition accepted as valid (soundness bug in proving/verifying/transition logic) leading to direct loss of funds or protocol insolvency
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
