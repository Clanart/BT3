# Q705: Exploit reorg boundary handling in verify_proof

## Question
Can an unprivileged attacker exploit reorg timing around the block/merkle proof nodes, indices, and ordering so `verify_proof` treats a non-canonical object as canonical long enough to mutate bridge state, corrupting the SPV inclusion result for the payout transaction and violating the invariant that kickoff/challenge/disprove handling must never let one deposit context inherit another deposit's proof or watchtower state, leading to Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs?

## Target
- File/function: circuits-lib/src/header_chain/mmr_native.rs::verify_proof
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: the block/merkle proof nodes, indices, and ordering
- Exploit idea: reorder or replay the block/merkle proof nodes, indices, and ordering across canonical and non-canonical views
- Invariant to test: kickoff/challenge/disprove handling must never let one deposit context inherit another deposit's proof or watchtower state
- Expected Immunefi impact: Critical. Direct theft of BTC/cBTC via deposit/withdraw verification bugs
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
