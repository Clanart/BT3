# Q0540: new_pending widens instruction privileges (lib.rs)

## Question
Can an unprivileged attacker entering through a transaction broadcast to a public TPU/QUIC endpoint by an ordinary funded keypair reach `new_pending` in `reserved-account-keys/src/lib.rs` with an ordering of instructions that leaves partial state from an earlier failure, and make the accounts marked writable in the sanitized message disagree with the accounts actually mutated during execution, so that the invariant "Callee privileges are always a subset of the caller's for the same account." breaks and the result is Loss of Funds?

## Target
- File/function: `reserved-account-keys/src/lib.rs` -> `new_pending()` (around line 123)
- Entrypoint: a transaction broadcast to a public TPU/QUIC endpoint by an ordinary funded keypair
- Attacker controls: an ordering of instructions that leaves partial state from an earlier failure
- Exploit idea: Use `new_pending` so the callee sees signer or writable privileges the caller never held, letting an attacker program act on a victim account.
- Invariant to test: Callee privileges are always a subset of the caller's for the same account.
- Expected Immunefi impact: Loss of Funds - theft or creation of lamports/tokens without the owner's signature (6,250-25,000 SOL)
- Fast validation: Assert in a test that the callee's `is_signer`/`is_writable` for each account is implied by the parent instruction's.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
Critical. An unprivileged attacker can deploy a program that reads or writes host/guest memory outside its permitted regions, or forges an account reference, and extracts or corrupts state belonging to other accounts.
