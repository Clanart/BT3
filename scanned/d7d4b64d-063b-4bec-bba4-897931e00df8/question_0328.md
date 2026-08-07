# Q0328: withdraw_nonce_account widens instruction privileges (system_instruction.rs)

## Question
Can an unprivileged attacker entering through a system/stake/vote instruction sent by an ordinary funded keypair, directly or via CPI reach `withdraw_nonce_account` in `programs/system/src/system_instruction.rs` with a zero-lamport or exactly-rent-exempt-minus-one account, and make the lamports moved by the instruction disagree with the lamports accounted for in the bank capitalization, so that the invariant "Callee privileges are always a subset of the caller's for the same account." breaks and the result is Loss of Funds?

## Target
- File/function: `programs/system/src/system_instruction.rs` -> `withdraw_nonce_account()` (around line 80)
- Entrypoint: a system/stake/vote instruction sent by an ordinary funded keypair, directly or via CPI
- Attacker controls: a zero-lamport or exactly-rent-exempt-minus-one account
- Exploit idea: Use `withdraw_nonce_account` so the callee sees signer or writable privileges the caller never held, letting an attacker program act on a victim account.
- Invariant to test: Callee privileges are always a subset of the caller's for the same account.
- Expected Immunefi impact: Loss of Funds - theft or creation of lamports/tokens without the owner's signature (6,250-25,000 SOL)
- Fast validation: Assert in a test that the callee's `is_signer`/`is_writable` for each account is implied by the parent instruction's.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
Critical. An unprivileged attacker can deploy a program that reads or writes host/guest memory outside its permitted regions, or forges an account reference, and extracts or corrupts state belonging to other accounts.
