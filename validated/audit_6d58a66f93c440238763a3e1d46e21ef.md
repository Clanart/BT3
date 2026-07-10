### Title
ERC1155 Tokens Permanently Locked When `initTransfer1155` Is Called Without Prior `logMetadata1155` — (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`initTransfer1155` accepts and locks ERC1155 tokens in the bridge without checking or setting the `multiTokens[deterministicToken]` mapping. That mapping is the sole mechanism by which `finTransfer` identifies which ERC1155 contract and token ID to release on the return leg. If a user calls `initTransfer1155` before `logMetadata1155` has been called for that `(tokenAddress, tokenId)` pair, the mapping remains unset. Any subsequent `finTransfer` for that deterministic address falls through to an ERC20 transfer attempt against a non-ERC20 address and reverts, leaving the ERC1155 tokens irrecoverably locked in the bridge.

---

### Finding Description

The ERC1155 bridging flow in `OmniBridge.sol` is split across two independent, permissionless entry points:

**`logMetadata1155`** (lines 234–270) — registers the `(tokenAddress, tokenId)` pair under the deterministic pseudo-address and emits `LogMetadata` so the NEAR side can index the token:

```solidity
MultiTokenInfo storage multiToken = multiTokens[deterministicToken];
if (multiToken.tokenAddress == address(0)) {
    multiToken.tokenAddress = tokenAddress;
    multiToken.tokenId = tokenId;
}
``` [1](#0-0) 

**`initTransfer1155`** (lines 439–490) — pulls ERC1155 tokens from the caller into the bridge and emits `InitTransfer`. It computes `deterministicToken` but **never reads or writes `multiTokens`**:

```solidity
address deterministicToken = deriveDeterministicAddress(tokenAddress, tokenId);
IERC1155(tokenAddress).safeTransferFrom(msg.sender, address(this), tokenId, amount, "");
// ... no multiTokens check or write ...
emit BridgeTypes.InitTransfer(msg.sender, deterministicToken, ...);
``` [2](#0-1) 

**`finTransfer`** (lines 279–367) — on the return leg, dispatches the release by reading `multiTokens[payload.tokenAddress]`. If the entry is zero (mapping was never set), it skips the ERC1155 branch and falls through to the ERC20 branch:

```solidity
MultiTokenInfo memory multiToken = multiTokens[payload.tokenAddress];

if (payload.tokenAddress == address(0)) { ... }
else if (multiToken.tokenAddress != address(0)) {          // ← skipped: mapping unset
    IERC1155(multiToken.tokenAddress).safeTransferFrom(...);
} else if (customMinters[...] != address(0)) { ... }
else if (isBridgeToken[...]) { ... }
else {
    IERC20(payload.tokenAddress).safeTransfer(...);        // ← reverts: not an ERC20
}
``` [3](#0-2) 

`deterministicToken` is `address(bytes20(keccak256(abi.encodePacked(tokenAddress, tokenId))))` — a pseudo-address that is not an ERC20 contract. The `safeTransfer` call reverts, and because the nonce write (`completedTransfers[payload.destinationNonce] = true`) is in the same transaction, it also reverts, leaving the nonce unconsumed. However, if the NEAR side never emitted a signed `finTransfer` payload (because no `LogMetadata` event was ever observed), no valid signature can ever be produced for this transfer, and the ERC1155 tokens remain permanently locked with no admin rescue path in the contract. [4](#0-3) 

---

### Impact Explanation

**Critical — Permanent freezing of user ERC1155 assets in the bridge.**

A user who calls `initTransfer1155` without a preceding `logMetadata1155` transfers their ERC1155 tokens into the bridge contract. If the NEAR indexer never observed a `LogMetadata` event for that `deterministicToken`, it will not register the token and will not produce a valid MPC-signed `finTransfer` payload. With no valid signature available and no admin ERC1155 rescue function in the contract, the tokens are irrecoverably locked. This matches the allowed impact: *"Critical. Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows."*

---

### Likelihood Explanation

**Medium.** `initTransfer1155` is a public, permissionless function. Nothing in its signature, NatSpec, or on-chain logic signals that `logMetadata1155` must be called first. A user who discovers the function via ABI inspection, a frontend that omits the prerequisite step, or a direct contract interaction can trivially trigger this path. The test suite itself always calls `logMetadata1155` first, but that ordering is not enforced on-chain. [5](#0-4) 

---

### Recommendation

Enforce the invariant inside `initTransfer1155`: either require that the mapping already exists, or set it atomically before accepting the token transfer:

```solidity
function initTransfer1155(
    address tokenAddress,
    uint256 tokenId,
    ...
) external payable whenNotPaused(PAUSED_INIT_TRANSFER) {
    ...
    address deterministicToken = deriveDeterministicAddress(tokenAddress, tokenId);

    // Option A: require prior registration
    require(
        multiTokens[deterministicToken].tokenAddress != address(0),
        "ERC1155 not registered: call logMetadata1155 first"
    );

    // Option B (alternative): register lazily here
    // MultiTokenInfo storage mt = multiTokens[deterministicToken];
    // if (mt.tokenAddress == address(0)) {
    //     mt.tokenAddress = tokenAddress;
    //     mt.tokenId = tokenId;
    // }

    IERC1155(tokenAddress).safeTransferFrom(msg.sender, address(this), tokenId, amount, "");
    ...
}
```

Option A is safer because it also ensures the NEAR side has already indexed the `LogMetadata` event before any tokens are committed to the bridge.

---

### Proof of Concept

1. Deploy `OmniBridge` and an ERC1155 token contract.
2. Mint `tokenId = 7` to `user`.
3. `user` approves the bridge and calls `initTransfer1155(erc1155, 7, 1, 0, 0, "victim.near", "")` — **without** calling `logMetadata1155` first.
4. Observe: `erc1155.balanceOf(bridge, 7) == 1`; `multiTokens[deterministicAddress(erc1155, 7)].tokenAddress == address(0)`.
5. Attempt `finTransfer` with `payload.tokenAddress = deterministicAddress(erc1155, 7)`:
   - `multiToken.tokenAddress == address(0)` → ERC1155 branch skipped.
   - Falls through to `IERC20(deterministicToken).safeTransfer(...)` → reverts (not an ERC20).
6. No valid MPC signature for this transfer exists on NEAR (no `LogMetadata` was ever emitted).
7. `erc1155.balanceOf(bridge, 7) == 1` permanently — no recovery path exists in the contract. [6](#0-5) [7](#0-6) [3](#0-2)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L234-270)
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

        logMetadataExtension(
            deterministicToken,
            Strings.toHexString(tokenAddress),
            "",
            0
        );

        emit BridgeTypes.LogMetadata(
            deterministicToken,
            Strings.toHexString(tokenAddress),
            "",
            0
        );
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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L576-584)
```text
    function deriveDeterministicAddress(
        address tokenAddress,
        uint256 tokenId
    ) public pure returns (address) {
        return
            address(
                bytes20(keccak256(abi.encodePacked(tokenAddress, tokenId)))
            );
    }
```
