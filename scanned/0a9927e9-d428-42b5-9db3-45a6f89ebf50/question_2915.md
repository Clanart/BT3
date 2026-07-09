# Q2915: NEAR required_balance_for_init_transfer_message derived storage account can collide across transfers through cross-module drift

## Question
Can an unprivileged attacker use `internal accounting helper reached from public init-transfer paths` with control over recipient chain, message size, fee structure, origin transfer id usage, and calculated storage account id and desynchronize `near/omni-bridge/src/storage.rs::required_balance_for_init_transfer_message` from the adjacent storage billing and refund bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `derived storage account can collide across transfers` attack class because computes how much storage balance an outbound transfer needs before it can be stored or resumed, violating `storage quoting must upper-bound every live outbound state footprint so attackers cannot create undercollateralized pending transfers`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::required_balance_for_init_transfer_message`
- Entrypoint: `internal accounting helper reached from public init-transfer paths`
- Attacker controls: recipient chain, message size, fee structure, origin transfer id usage, and calculated storage account id
- Exploit idea: Target attacker-controlled `external_id`, token, sender, or recipient fields used in derived storage-account identities. Focus on drift between this module and the adjacent storage billing and refund bookkeeping.
- Invariant to test: storage quoting must upper-bound every live outbound state footprint so attackers cannot create undercollateralized pending transfers
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Generate colliding-looking inputs and assert that each pending transfer gets a unique storage slot or else cleanly rejects the second attempt. Also assert cross-module consistency between `near/omni-bridge/src/storage.rs::required_balance_for_init_transfer_message` and the adjacent storage billing and refund bookkeeping after every branch.
