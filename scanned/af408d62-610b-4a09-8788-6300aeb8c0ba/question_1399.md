# Q1399: EVM OmniBridge initTransfer1155 recipient or message ambiguity

## Question
Can an unprivileged attacker supply attacker-controlled recipient or message data through `public EVM ERC-1155 outbound transfer entrypoint` and make `evm/src/omni-bridge/contracts/OmniBridge.sol::initTransfer1155` encode or parse it differently than downstream chains expect via increments the origin nonce, transfers an ERC-1155 token into bridge custody, derives a deterministic alias, and forwards the transfer into `initTransferExtension`, violating `multi-token outbound transfers must preserve the exact `(tokenAddress, tokenId, amount)` identity that downstream chains will use for deployment and settlement`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::initTransfer1155`
- Entrypoint: `public EVM ERC-1155 outbound transfer entrypoint`
- Attacker controls: ERC-1155 contract, token id, amount, fee, native fee, recipient string, message bytes, and msg.value
- Exploit idea: Exploit non-canonical string, ByteArray, hex, or account-id forms to make one source-side intent resolve to a different destination-side recipient or message.
- Invariant to test: multi-token outbound transfers must preserve the exact `(tokenAddress, tokenId, amount)` identity that downstream chains will use for deployment and settlement
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Cross-check source-side serialization against every downstream parser and assert that equivalent-looking inputs cannot resolve to distinct destination accounts or app messages.
