# Q1721: EVM OmniBridge initTransfer1155 recipient or message ambiguity through cross-module drift

## Question
Can an unprivileged attacker use `public EVM ERC-1155 outbound transfer entrypoint` with control over ERC-1155 contract, token id, amount, fee, native fee, recipient string, message bytes, and msg.value and desynchronize `evm/src/omni-bridge/contracts/OmniBridge.sol::initTransfer1155` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `recipient or message ambiguity` attack class because increments the origin nonce, transfers an ERC-1155 token into bridge custody, derives a deterministic alias, and forwards the transfer into `initTransferExtension`, violating `multi-token outbound transfers must preserve the exact `(tokenAddress, tokenId, amount)` identity that downstream chains will use for deployment and settlement`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::initTransfer1155`
- Entrypoint: `public EVM ERC-1155 outbound transfer entrypoint`
- Attacker controls: ERC-1155 contract, token id, amount, fee, native fee, recipient string, message bytes, and msg.value
- Exploit idea: Exploit non-canonical string, ByteArray, hex, or account-id forms to make one source-side intent resolve to a different destination-side recipient or message. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: multi-token outbound transfers must preserve the exact `(tokenAddress, tokenId, amount)` identity that downstream chains will use for deployment and settlement
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Cross-check source-side serialization against every downstream parser and assert that equivalent-looking inputs cannot resolve to distinct destination accounts or app messages. Also assert cross-module consistency between `evm/src/omni-bridge/contracts/OmniBridge.sol::initTransfer1155` and the adjacent replay-protection bookkeeping after every branch.
