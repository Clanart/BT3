# Q746: Exploit reorg boundary handling in compute_block_hash

## Question
Can an unprivileged attacker exploit reorg timing around the header sequence, timestamps, and `bits` values so `compute_block_hash` treats a non-canonical object as canonical long enough to mutate bridge state, corrupting the SPV inclusion result for the payout transaction and violating the invariant that kickoff/challenge/disprove handling must never let one deposit context inherit another deposit's proof or watchtower state, leading to Critical. Permanent freezing of bridged funds?

## Target
- File/function: circuits-lib/src/header_chain/mod.rs::compute_block_hash
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: the header sequence, timestamps, and `bits` values
- Exploit idea: reorder or replay the header sequence, timestamps, and `bits` values across canonical and non-canonical views
- Invariant to test: kickoff/challenge/disprove handling must never let one deposit context inherit another deposit's proof or watchtower state
- Expected Immunefi impact: Critical. Permanent freezing of bridged funds
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
