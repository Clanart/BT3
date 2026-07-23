# Q3537: Drive state split inside verify_proof

## Question
Can an unprivileged attacker use broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation with crafted the block/merkle proof nodes, indices, and ordering so `verify_proof` updates one canonical value while another subsystem retains the older one for the same event, corrupting the SPV inclusion result for the payout transaction and breaking the invariant that kickoff/challenge/disprove handling must never let one deposit context inherit another deposit's proof or watchtower state, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: circuits-lib/src/header_chain/mmr_native.rs::verify_proof
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: the block/merkle proof nodes, indices, and ordering
- Exploit idea: update one canonical value while another subsystem keeps the old one for the same event via the block/merkle proof nodes, indices, and ordering
- Invariant to test: kickoff/challenge/disprove handling must never let one deposit context inherit another deposit's proof or watchtower state
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
