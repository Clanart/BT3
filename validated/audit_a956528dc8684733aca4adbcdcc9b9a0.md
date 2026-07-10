### Title
ERC1155 Tokens Permanently Locked in OmniBridge When `initTransfer1155` Is Called Without Prior `logMetadata1155` — (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`initTransfer1155` locks ERC1155 tokens in the `OmniBridge` contract without verifying that the token is registered in the `multiTokens` mapping. The sole release path, `finTransfer`, requires `multiTokens[payload.tokenAddress].tokenAddress != address(0)`. If `logMetadata1155` was never called for the `(tokenAddress, tokenId)` pair, the locked ERC1155 tokens are permanently irrecoverable — there is no emergency-withdrawal or rescue function.

---

### Finding Description

`OmniBridge` manages ERC1155 bridging through two separate, unlinked public functions:

**`logMetadata1155`** (lines 234–270) — registers the token by writing to `multiTokens[deterministicToken]` and emitting `LogMetadata`:

```solidity
MultiTokenInfo storage multiToken = multiTokens[deterministicToken];
if (multiToken.tokenAddress == address(0)) {
    multiToken.tokenAddress = tokenAddress;
    multiToken.tokenId = tokenId;
}
```
<cite repo="blackvul/omni-bridge--018" path="evm/src/omni-bridge/contracts/OmniBridge.sol" start="243" end="256"