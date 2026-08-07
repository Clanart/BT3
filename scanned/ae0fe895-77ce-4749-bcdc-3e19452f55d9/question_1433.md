# Q1433: scan_accounts_without_data authorization check bypassed (accounts_file.rs)

## Question
Can an unprivileged attacker entering through ordinary transactions that create, write, resize, close and reopen accounts the attacker pays for reach `scan_accounts_without_data` in `accounts-db/src/accounts_file.rs` with the same account passed twice in the account list under different indices, and have the state change applied even though the authority stored in the target account never signed, so that the invariant "Every state change requires the signature of the authority stored in the account being changed." breaks and the result is Loss of Funds?

## Target
- File/function: `accounts-db/src/accounts_file.rs` -> `scan_accounts_without_data()` (around line 175)
- Entrypoint: ordinary transactions that create, write, resize, close and reopen accounts the attacker pays for
- Attacker controls: the same account passed twice in the account list under different indices
- Exploit idea: Reach `scan_accounts_without_data` on an account the attacker does not own and get the write applied anyway, because the check consumes a different value than the mutation does.
- Invariant to test: Every state change requires the signature of the authority stored in the account being changed.
- Expected Immunefi impact: Loss of Funds - theft or creation of lamports/tokens without the owner's signature (6,250-25,000 SOL)
- Fast validation: Unit test in `accounts-db/src/accounts_file.rs`: build the instruction with a victim account and an attacker signer; assert the call returns an error and the victim account bytes are unchanged.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
Critical. An unprivileged attacker can deploy a program that reads or writes host/guest memory outside its permitted regions, or forges an account reference, and extracts or corrupts state belonging to other accounts.
