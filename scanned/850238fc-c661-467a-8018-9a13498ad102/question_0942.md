# Q0942: null_tracer widens instruction privileges (points.rs)

## Question
Can an unprivileged attacker entering through a transaction that lands in a block and is replayed by every node on the cluster reach `null_tracer` in `runtime/src/inflation_rewards/points.rs` with an instruction sequence that re-enters the same code path within one transaction, and make the epoch boundary state computed by this node disagree with the state computed by a node that replayed the same blocks, so that the invariant "Callee privileges are always a subset of the caller's for the same account." breaks and the result is Loss of Funds?

## Target
- File/function: `runtime/src/inflation_rewards/points.rs` -> `null_tracer()` (around line 64)
- Entrypoint: a transaction that lands in a block and is replayed by every node on the cluster
- Attacker controls: an instruction sequence that re-enters the same code path within one transaction
- Exploit idea: Use `null_tracer` so the callee sees signer or writable privileges the caller never held, letting an attacker program act on a victim account.
- Invariant to test: Callee privileges are always a subset of the caller's for the same account.
- Expected Immunefi impact: Loss of Funds - theft or creation of lamports/tokens without the owner's signature (6,250-25,000 SOL)
- Fast validation: Assert in a test that the callee's `is_signer`/`is_writable` for each account is implied by the parent instruction's.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
Critical. An unprivileged attacker can deploy a program that reads or writes host/guest memory outside its permitted regions, or forges an account reference, and extracts or corrupts state belonging to other accounts.
