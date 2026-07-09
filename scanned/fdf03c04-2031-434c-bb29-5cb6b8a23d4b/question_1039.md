# Q1039: NEAR required_balance_for_init_transfer_message fee and principal split divergence through cross-module drift

## Question
Can an unprivileged attacker use `internal accounting helper reached from public init-transfer paths` with control over recipient chain, message size, fee structure, origin transfer id usage, and calculated storage account id and desynchronize `near/omni-bridge/src/storage.rs::required_balance_for_init_transfer_message` from the adjacent storage billing and refund bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `fee and principal split divergence` attack class because computes how much storage balance an outbound transfer needs before it can be stored or resumed, violating `storage quoting must upper-bound every live outbound state footprint so attackers cannot create undercollateralized pending transfers`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::required_balance_for_init_transfer_message`
- Entrypoint: `internal accounting helper reached from public init-transfer paths`
- Attacker controls: recipient chain, message size, fee structure, origin transfer id usage, and calculated storage account id
- Exploit idea: Focus on branches where fee checks happen before normalization, denormalization, callback resolution, or storage billing. Focus on drift between this module and the adjacent storage billing and refund bookkeeping.
- Invariant to test: storage quoting must upper-bound every live outbound state footprint so attackers cannot create undercollateralized pending transfers
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz amount/fee/native-fee edge cases around zero, max, and decimal boundaries and assert that emitted value plus stored fee always equals consumed value. Also assert cross-module consistency between `near/omni-bridge/src/storage.rs::required_balance_for_init_transfer_message` and the adjacent storage billing and refund bookkeeping after every branch.
