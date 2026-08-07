# Q0230: from_sol_account_info reads or writes outside its permitted region (cpi.rs)

## Question
Can an unprivileged attacker entering through deploying an attacker-authored sBPF program and invoking it with crafted instruction data and account lists reach `from_sol_account_info` in `program-runtime/src/cpi.rs` with the same account passed twice in the account list under different indices, and make `from_sol_account_info` compute an address or length that lands outside its mapped region, so that the invariant "All accesses stay inside the regions mapped for the current instruction, at the mapped permission." breaks and the result is Loss of Funds?

## Target
- File/function: `program-runtime/src/cpi.rs` -> `from_sol_account_info()` (around line 408)
- Entrypoint: deploying an attacker-authored sBPF program and invoking it with crafted instruction data and account lists
- Attacker controls: the same account passed twice in the account list under different indices
- Exploit idea: Make `from_sol_account_info` compute an address or length from attacker data so it touches memory belonging to another account or to the host.
- Invariant to test: All accesses stay inside the regions mapped for the current instruction, at the mapped permission.
- Expected Immunefi impact: Loss of Funds - theft or creation of lamports/tokens without the owner's signature (6,250-25,000 SOL)
- Fast validation: Fuzz the address/length arguments and assert every out-of-region access returns an access-violation error.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
Critical. An unprivileged attacker can deploy a program that reads or writes host/guest memory outside its permitted regions, or forges an account reference, and extracts or corrupts state belonging to other accounts.
