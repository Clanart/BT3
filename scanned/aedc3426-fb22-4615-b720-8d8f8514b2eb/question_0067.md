# Q67: EVM OmniBridge initTransfer1155 origin and destination nonce desynchronization

## Question
Can an unprivileged attacker enter through `public EVM ERC-1155 outbound transfer entrypoint` with control over ERC-1155 contract, token id, amount, fee, native fee, recipient string, message bytes, and msg.value and make `evm/src/omni-bridge/contracts/OmniBridge.sol::initTransfer1155` advance or reuse bridge nonces inconsistently with increments the origin nonce, transfers an ERC-1155 token into bridge custody, derives a deterministic alias, and forwards the transfer into `initTransferExtension`, so that one economic transfer can be emitted, resumed, or signed under multiple identifiers, violating `multi-token outbound transfers must preserve the exact `(tokenAddress, tokenId, amount)` identity that downstream chains will use for deployment and settlement`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::initTransfer1155`
- Entrypoint: `public EVM ERC-1155 outbound transfer entrypoint`
- Attacker controls: ERC-1155 contract, token id, amount, fee, native fee, recipient string, message bytes, and msg.value
- Exploit idea: Drive retries, resume paths, or recursive bridge legs until one deposit appears under more than one transfer identity.
- Invariant to test: multi-token outbound transfers must preserve the exact `(tokenAddress, tokenId, amount)` identity that downstream chains will use for deployment and settlement
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Trace every nonce mutation site and fuzz repeated calls plus reordered callbacks to prove that one deposit cannot create two valid transfer ids or destination messages.
