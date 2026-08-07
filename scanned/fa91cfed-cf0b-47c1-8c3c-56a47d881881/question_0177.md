# Q0177: set_is_signer accepts input it should reject (instruction_accounts.rs)

## Question
Can an unprivileged attacker entering through deploying an attacker-authored sBPF program and invoking it with crafted instruction data and account lists reach `set_is_signer` in `transaction-context/src/instruction_accounts.rs` with input that makes the check pass on a value it later stops using, and have `set_is_signer` accept input that fails the property it is supposed to prove, so that the invariant "`set_is_signer` accepts exactly the valid set: no forged input passes and no valid input fails." breaks and the result is Loss of Funds?

## Target
- File/function: `transaction-context/src/instruction_accounts.rs` -> `set_is_signer()` (around line 49)
- Entrypoint: deploying an attacker-authored sBPF program and invoking it with crafted instruction data and account lists
- Attacker controls: input that makes the check pass on a value it later stops using
- Exploit idea: Construct input that `set_is_signer` accepts although it fails the property it is supposed to prove (signature, proof, derivation, ownership).
- Invariant to test: `set_is_signer` accepts exactly the valid set: no forged input passes and no valid input fails.
- Expected Immunefi impact: Loss of Funds - theft or creation of lamports/tokens without the owner's signature (6,250-25,000 SOL)
- Fast validation: Differential test `set_is_signer` against a reference implementation over random valid and mutated-invalid inputs.

## Bounty scope note
In-scope target per anza-xyz/agave SECURITY.md. Assumes no validator, leader,
staked-node, peer, gossip, operator, or leaked-key capability. Folder scope:
Critical. An unprivileged attacker can deploy or upgrade a program, or poison the program cache, so honest validators execute different bytecode or produce different results for the same instruction.
