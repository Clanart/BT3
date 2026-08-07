# Q2851: get_account_from_overwrites_or_bank widens instruction privileges (account_resolver.rs)

## Question
Can an unprivileged attacker entering through one JSON-RPC call or websocket subscription from a single client, at most once per CLUSTER_SLOT_TIME_TARGET/2 reach `get_account_from_overwrites_or_bank` in `rpc/src/rpc/account_resolver.rs` with a zero-lamport or exactly-rent-exempt-minus-one account, and make the block the status lookup reads disagree with the block that is actually rooted, so that the invariant "Callee privileges are always a subset of the caller's for the same account." breaks and the result is Loss of Funds?

## Target
- File/function: `rpc/src/rpc/account_resolver.rs` -> `get_account_from_overwrites_or_bank()` (around line 6)
- Entrypoint: one JSON-RPC call or websocket subscription from a single client, at most once per CLUSTER_SLOT_TIME_TARGET/2
- Attacker controls: a zero-lamport or exactly-rent-exempt-minus-one account
- Exploit idea: Use `get_account_from_overwrites_or_bank` so the callee sees signer or writable privileges the caller never held, letting an attacker program act on a victim account.
- Invariant to test: Callee privileges are always a subset of the caller's for the same account.
- Expected Immunefi impact: Loss of Funds - theft or creation of lamports/tokens without the owner's signature (6,250-25,000 SOL)
- Fast validation: Assert in a test that the callee's `is_signer`/`is_writable` for each account is implied by the parent instruction's.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
Critical. An unprivileged attacker can deploy a program that reads or writes host/guest memory outside its permitted regions, or forges an account reference, and extracts or corrupts state belonging to other accounts.
