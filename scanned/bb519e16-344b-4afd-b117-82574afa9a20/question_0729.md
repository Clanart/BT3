# Q729: Exploit reorg boundary handling in total_work_and_watchtower_flags

## Question
Can an unprivileged attacker exploit reorg timing around Bitcoin transaction shape, witness bytes, annexes, and OP_RETURN contents so `total_work_and_watchtower_flags` treats a non-canonical object as canonical long enough to mutate bridge state, corrupting the L1 block hash carried from the light-client proof into bridge validation and violating the invariant that kickoff/challenge/disprove handling must never let one deposit context inherit another deposit's proof or watchtower state, leading to Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted?

## Target
- File/function: circuits-lib/src/bridge_circuit/mod.rs::total_work_and_watchtower_flags
- Entrypoint: broadcast crafted Bitcoin transactions, headers, witnesses, or proofs that later reach sync, prover, or circuit validation
- Attacker controls: Bitcoin transaction shape, witness bytes, annexes, and OP_RETURN contents
- Exploit idea: reorder or replay Bitcoin transaction shape, witness bytes, annexes, and OP_RETURN contents across canonical and non-canonical views
- Invariant to test: kickoff/challenge/disprove handling must never let one deposit context inherit another deposit's proof or watchtower state
- Expected Immunefi impact: Critical. Bitcoin-anchoring verification failure: accepting a batch/commitment/proof that should not be accepted
- Fast validation: build a regtest/property test that mutates the relevant Bitcoin tx/header/proof field and assert sync/prover/circuit code rejects it without changing canonical bridge state
