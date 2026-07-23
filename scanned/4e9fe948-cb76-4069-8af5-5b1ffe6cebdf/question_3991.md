# Q3991: Accept wrong proof/network context in get_chain_state_from_height

## Question
Can an unprivileged attacker supply the header sequence, timestamps, and `bits` values through broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation so `get_chain_state_from_height` accepts it without fully binding network, method-id, genesis, or height context, corrupting the kickoff/challenge/assert/disprove context treated as canonical for a deposit and breaking the invariant that proof verification must bind method id, network, genesis context, proof inputs, and on-chain data to one canonical statement, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: core/src/header_chain_prover.rs::get_chain_state_from_height
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: the header sequence, timestamps, and `bits` values
- Exploit idea: omit full network, method-id, genesis, or height binding for the header sequence, timestamps, and `bits` values
- Invariant to test: proof verification must bind method id, network, genesis context, proof inputs, and on-chain data to one canonical statement
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
