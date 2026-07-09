# Q1719: EVM OmniBridge finTransfer recipient or fee-recipient rebinding through cross-module drift

## Question
Can an unprivileged attacker use `public EVM settlement entrypoint` with control over signature bytes, destination nonce, origin chain, origin nonce, token address, amount, recipient, fee recipient, and message bytes and desynchronize `evm/src/omni-bridge/contracts/OmniBridge.sol::finTransfer` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `recipient or fee-recipient rebinding` attack class because marks `completedTransfers[destinationNonce] = true`, hashes a Borsh-encoded transfer payload, validates the signature, then releases ETH, transfers ERC-1155, calls a custom minter, mints a bridge token, or transfers an ERC-20, violating `one signed settlement payload must release value exactly once to the intended token branch and recipient without letting state updates, branch selection, or message handling fork the outcome`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::finTransfer`
- Entrypoint: `public EVM settlement entrypoint`
- Attacker controls: signature bytes, destination nonce, origin chain, origin nonce, token address, amount, recipient, fee recipient, and message bytes
- Exploit idea: Exploit optional fee-recipient fields, fast-transfer relayer substitution, or predecessor-captured identities. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: one signed settlement payload must release value exactly once to the intended token branch and recipient without letting state updates, branch selection, or message handling fork the outcome
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Build pairs of proofs/messages that vary only in recipient-oriented fields and assert that settlement, fee claim, and event emission stay bound to one tuple. Also assert cross-module consistency between `evm/src/omni-bridge/contracts/OmniBridge.sol::finTransfer` and the adjacent replay-protection bookkeeping after every branch.
