# Q3567: get_precompile_signature_details widens instruction privileges (signature_details.rs)

## Question
Can an unprivileged attacker entering through a transaction broadcast to a public TPU/QUIC endpoint by an ordinary funded keypair reach `get_precompile_signature_details` in `runtime-transaction/src/signature_details.rs` with a boundary value exactly on the accept/reject edge of the predicate, and make the rent state computed before execution disagree with the rent state asserted after execution, so that the invariant "Callee privileges are always a subset of the caller's for the same account." breaks and the result is Loss of Funds?

## Target
- File/function: `runtime-transaction/src/signature_details.rs` -> `get_precompile_signature_details()` (around line 61)
- Entrypoint: a transaction broadcast to a public TPU/QUIC endpoint by an ordinary funded keypair
- Attacker controls: a boundary value exactly on the accept/reject edge of the predicate
- Exploit idea: Use `get_precompile_signature_details` so the callee sees signer or writable privileges the caller never held, letting an attacker program act on a victim account.
- Invariant to test: Callee privileges are always a subset of the caller's for the same account.
- Expected Immunefi impact: Loss of Funds - theft or creation of lamports/tokens without the owner's signature (6,250-25,000 SOL)
- Fast validation: Assert in a test that the callee's `is_signer`/`is_writable` for each account is implied by the parent instruction's.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
Critical. An unprivileged attacker can deploy a program that reads or writes host/guest memory outside its permitted regions, or forges an account reference, and extracts or corrupts state belonging to other accounts.
