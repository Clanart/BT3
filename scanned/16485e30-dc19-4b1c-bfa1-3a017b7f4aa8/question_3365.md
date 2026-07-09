# Q3365: EVM OmniBridge initTransfer1155 native versus wrapped branch switch via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public EVM ERC-1155 outbound transfer entrypoint` and then replay or reorder later callback or refund resolution so that `evm/src/omni-bridge/contracts/OmniBridge.sol::initTransfer1155` ends up accepting two inconsistent interpretations of the same economic event specifically around `native versus wrapped branch switch` under increments the origin nonce, transfers an ERC-1155 token into bridge custody, derives a deterministic alias, and forwards the transfer into `initTransferExtension`, violating `multi-token outbound transfers must preserve the exact `(tokenAddress, tokenId, amount)` identity that downstream chains will use for deployment and settlement`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::initTransfer1155`
- Entrypoint: `public EVM ERC-1155 outbound transfer entrypoint`
- Attacker controls: ERC-1155 contract, token id, amount, fee, native fee, recipient string, message bytes, and msg.value
- Exploit idea: Target zero-address, deployed-token, custom-minter, native-vault, or bridge-token branch predicates. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: multi-token outbound transfers must preserve the exact `(tokenAddress, tokenId, amount)` identity that downstream chains will use for deployment and settlement
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force each branch predicate to flip around callbacks or mapping writes and assert that the same source asset can never produce two incompatible custody models. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
