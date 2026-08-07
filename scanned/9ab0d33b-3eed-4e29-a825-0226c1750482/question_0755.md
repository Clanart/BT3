# Q0755: check_reserved_keys settles one authorization twice (bank.rs)

## Question
Can an unprivileged attacker entering through a transaction that lands in a block and is replayed by every node on the cluster reach `check_reserved_keys` in `runtime/src/bank.rs` with a boundary value exactly on the accept/reject edge of the predicate, and have `check_reserved_keys` apply the same authorized effect a second time, so that the invariant "One signed authorization produces exactly one state effect." breaks and the result is Loss of Funds?

## Target
- File/function: `runtime/src/bank.rs` -> `check_reserved_keys()` (around line 5580)
- Entrypoint: a transaction that lands in a block and is replayed by every node on the cluster
- Attacker controls: a boundary value exactly on the accept/reject edge of the predicate
- Exploit idea: Get `check_reserved_keys` to apply the same logical effect twice from a single user authorization by re-entering it or replaying the surrounding flow.
- Invariant to test: One signed authorization produces exactly one state effect.
- Expected Immunefi impact: Loss of Funds - theft or creation of lamports/tokens without the owner's signature (6,250-25,000 SOL)
- Fast validation: Integration test: submit the flow twice (and once re-entrantly) and assert the second application is rejected and balances moved once.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
Critical. An unprivileged attacker can escalate instruction privileges through CPI, account serialization, or signer-seed handling and mutate or drain an account the top-level transaction never authorized as writable or signed.
