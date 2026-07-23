# Q672: Exploit reorg boundary handling in get_tip_header_chain_proof

## Question
Can an unprivileged attacker exploit reorg timing around multiple kickoff/challenge/assert/disprove transactions referencing the same deposit context so `get_tip_header_chain_proof` treats a non-canonical object as canonical long enough to mutate bridge state, corrupting the storage-proof key/value binding used for deposit or withdrawal validation and violating the invariant that proof verification must bind method id, network, genesis context, proof inputs, and on-chain data to one canonical statement, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/header_chain_prover.rs::get_tip_header_chain_proof
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: multiple kickoff/challenge/assert/disprove transactions referencing the same deposit context
- Exploit idea: reorder or replay multiple kickoff/challenge/assert/disprove transactions referencing the same deposit context across canonical and non-canonical views
- Invariant to test: proof verification must bind method id, network, genesis context, proof inputs, and on-chain data to one canonical statement
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
