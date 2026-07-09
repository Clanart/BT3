# Q3633: EVM OmniBridge finTransfer final settlement and later fee claim can diverge at boundary values

## Question
Can an unprivileged attacker trigger `public EVM settlement entrypoint` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `evm/src/omni-bridge/contracts/OmniBridge.sol::finTransfer` violate `one signed settlement payload must release value exactly once to the intended token branch and recipient without letting state updates, branch selection, or message handling fork the outcome` in the `final settlement and later fee claim can diverge` attack class because marks `completedTransfers[destinationNonce] = true`, hashes a Borsh-encoded transfer payload, validates the signature, then releases ETH, transfers ERC-1155, calls a custom minter, mints a bridge token, or transfers an ERC-20 becomes fragile at those edges?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::finTransfer`
- Entrypoint: `public EVM settlement entrypoint`
- Attacker controls: signature bytes, destination nonce, origin chain, origin nonce, token address, amount, recipient, fee recipient, and message bytes
- Exploit idea: Target differences between settle-time denormalization and claim-time recomputation of fee, dust, or relayer substitution. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: one signed settlement payload must release value exactly once to the intended token branch and recipient without letting state updates, branch selection, or message handling fork the outcome
- Expected Immunefi impact: Balance manipulation
- Fast validation: Compare settled principal, stored transfer record, and fee-claim proof under edge amounts and assert that the three always reconstruct one consistent event. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
