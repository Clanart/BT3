### Title
Missing `multiTokens` Mapping Validation in `initTransfer1155` Enables Permanent ERC1155 Token Lock — (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`initTransfer1155` locks ERC1155 tokens in the bridge without verifying that `logMetadata1155` has been called first to populate `multiTokens[deterministicToken]`. The `finTransfer` function, which releases tokens on the destination side, depends on `multiTokens[deterministicToken]` being populated to correctly handle ERC1155 tokens. If it is empty, `finTransfer` falls through to an ERC20 `safeTransfer` call against a hash-derived address with no contract code, causing a permanent revert and irrecoverable lock of the user's ERC1155 tokens.

---

### Finding Description

**Inconsistency between `initTransfer1155` and `finTransfer`:**

`initTransfer1155` derives a `deterministicToken` address and locks the ERC1155 tokens, but never checks whether `multiTokens[deterministicToken]` has been populated: [1](#0-0) 

Specifically, the function:
1. Derives `deterministicToken = keccak256(abi.encodePacked(tokenAddress, tokenId))` (truncated to `address`)
2. Calls `IERC1155(tokenAddress).safeTransferFrom(msg.sender, address(this), tokenId, amount, "")` — tokens are now locked
3. Emits `InitTransfer` with `deterministicToken` as the token address
4. **Never checks** `multiTokens[deterministicToken].tokenAddress != address(0)` [2](#0-1) 

The `logMetadata1155` function is the only way to populate `multiTokens[deterministicToken]`: [3](#0-2) 

`finTransfer` then checks `multiTokens[payload.tokenAddress]` to decide how to release tokens. If the mapping is empty, it falls through the entire conditional chain and reaches the final `IERC20(payload.tokenAddress).safeTransfer(...)` branch: [4](#0-3) 

Since `deterministicToken` is a hash-derived address with no deployed contract, `IERC20(deterministicToken).safeTransfer(...)` will always revert. There is no rescue or recovery function in `OmniBridge.sol` for stuck ERC1155 tokens.

---

### Impact Explanation

**Critical — Permanent freezing of user ERC1155 assets.**

Any user who calls `initTransfer1155` before `logMetadata1155` has been called for the specific `(tokenAddress, tokenId)` pair will have their ERC1155 tokens permanently locked in the bridge contract with no recovery path. The `finTransfer` call on the EVM side will always revert for that `deterministicToken`, and there is no admin rescue function.

---

### Likelihood Explanation

**Medium.** `logMetadata1155` is a separate, permissionless function with no on-chain enforcement that it must precede `initTransfer1155`. The protocol documentation or UI may instruct users to call `logMetadata1155` first, but there is no contract-level guard. Any user who skips this step — whether through ignorance, a UI bug, or direct contract interaction — will permanently lose their ERC1155 tokens. The `initTransfer1155` function is callable by any unprivileged user.

---

### Recommendation

Add a guard at the start of `initTransfer1155` to require that `multiTokens[deterministicToken]` has already been populated:

```solidity
function initTransfer1155(
    address tokenAddress,
    uint256 tokenId,
    ...
) external payable whenNotPaused(PAUSED_INIT_TRANSFER) {
    address deterministicToken = deriveDeterministicAddress(tokenAddress, tokenId);
    
    // ADD THIS CHECK:
    if (multiTokens[deterministicToken].tokenAddress == address(0)) {
        revert ERC1155NotRegistered();
    }
    
    // ... rest of function
}
```

This mirrors the validation pattern already present in `logMetadata1155` (lines 249–254) and ensures `finTransfer` will always find a valid `multiTokens` entry for any ERC1155 token that was accepted by `initTransfer1155`.

---

### Proof of Concept

1. Deploy an ERC1155 token contract and mint tokens to `attacker`.
2. `attacker` calls `initTransfer1155(tokenAddress, tokenId, amount, 0, 0, "near:recipient.near", "")` **without** first calling `logMetadata1155(tokenAddress, tokenId)`.
3. `IERC1155(tokenAddress).safeTransferFrom(attacker, bridge, tokenId, amount, "")` succeeds — tokens are now locked.
4. The NEAR bridge receives the `InitTransfer` event with `deterministicToken` as the token. Since `logMetadata1155` was never called, NEAR has no registered token for `deterministicToken` and cannot finalize the transfer.
5. If `finTransfer` is later called on the EVM side with `payload.tokenAddress = deterministicToken`:
   - `multiTokens[deterministicToken].tokenAddress == address(0)` → skips ERC1155 branch
   - `deterministicToken != address(0)` → skips ETH branch
   - `customMinters[deterministicToken] == address(0)` → skips custom minter branch
   - `isBridgeToken[deterministicToken] == false` → skips bridge token branch
   - `IERC20(deterministicToken).safeTransfer(recipient, amount)` → **REVERTS** (no contract at `deterministicToken`)
6. ERC1155 tokens are permanently locked with no recovery mechanism.

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L234-255)
```text
    function logMetadata1155(
        address tokenAddress,
        uint256 tokenId
    ) external payable {
        address deterministicToken = deriveDeterministicAddress(
            tokenAddress,
            tokenId
        );

        MultiTokenInfo storage multiToken = multiTokens[deterministicToken];

        if (multiToken.tokenAddress == address(0)) {
            multiToken.tokenAddress = tokenAddress;
            multiToken.tokenId = tokenId;
        } else {
            if (
                multiToken.tokenAddress != tokenAddress ||
                multiToken.tokenId != tokenId
            ) {
                revert ERC1155MappingMismatch();
            }
        }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L315-355)
```text
        MultiTokenInfo memory multiToken = multiTokens[payload.tokenAddress];

        if (payload.tokenAddress == address(0)) {
            // slither-disable-next-line arbitrary-send-eth
            (bool success, ) = payload.recipient.call{value: payload.amount}(
                ""
            );
            if (!success) revert FailedToSendEther();
        } else if (multiToken.tokenAddress != address(0)) {
            IERC1155(multiToken.tokenAddress).safeTransferFrom(
                address(this),
                payload.recipient,
                multiToken.tokenId,
                payload.amount,
                ""
            );
        } else if (customMinters[payload.tokenAddress] != address(0)) {
            ICustomMinter(customMinters[payload.tokenAddress]).mint(
                payload.tokenAddress,
                payload.recipient,
                payload.amount
            );
        } else if (isBridgeToken[payload.tokenAddress]) {
            if (payload.message.length == 0) {
                IBridgeToken(payload.tokenAddress).mint(
                    payload.recipient,
                    payload.amount
                );
            } else {
                IBridgeToken(payload.tokenAddress).mint(
                    payload.recipient,
                    payload.amount,
                    payload.message
                );
            }
        } else {
            IERC20(payload.tokenAddress).safeTransfer(
                payload.recipient,
                payload.amount
            );
        }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L439-490)
```text
    function initTransfer1155(
        address tokenAddress,
        uint256 tokenId,
        uint128 amount,
        uint128 fee,
        uint128 nativeFee,
        string calldata recipient,
        string calldata message
    ) external payable whenNotPaused(PAUSED_INIT_TRANSFER) {
        currentOriginNonce += 1;
        if (fee >= amount) {
            revert InvalidFee();
        }

        address deterministicToken = deriveDeterministicAddress(
            tokenAddress,
            tokenId
        );

        IERC1155(tokenAddress).safeTransferFrom(
            msg.sender,
            address(this),
            tokenId,
            amount,
            ""
        );

        uint256 extensionValue = msg.value - nativeFee;

        initTransferExtension(
            msg.sender,
            deterministicToken,
            currentOriginNonce,
            amount,
            fee,
            nativeFee,
            recipient,
            message,
            extensionValue
        );

        emit BridgeTypes.InitTransfer(
            msg.sender,
            deterministicToken,
            currentOriginNonce,
            amount,
            fee,
            nativeFee,
            recipient,
            message
        );
    }
```
