### Title
Missing `multiTokens` Mapping Population in `initTransfer1155` Causes Wrong Interface Dispatch in `finTransfer` — (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

### Summary

`initTransfer1155` locks ERC1155 tokens in the bridge and emits an `InitTransfer` event using a `deterministicToken` address, but never populates `multiTokens[deterministicToken]`. When `finTransfer` is later called with that address, the missing mapping causes the dispatch logic to fall through to `IERC20(deterministicToken).safeTransfer(...)` — calling the wrong interface on an address that is not an ERC20 contract — causing the finalization to revert and leaving the user's ERC1155 tokens unclaimable.

### Finding Description

`initTransfer1155` computes a deterministic address for the ERC1155 `(tokenAddress, tokenId)` pair, transfers the tokens into the bridge, and emits `InitTransfer` with that address as the token identifier: [1](#0-0) 

Critically, it never writes to `multiTokens[deterministicToken]`. The only function that populates this mapping is `logMetadata1155`: [2](#0-1) 

`finTransfer` dispatches the release by checking `multiTokens[payload.tokenAddress]` first. If that entry is zero (not set), it falls through the entire chain and reaches the final `else` branch: [3](#0-2) 

The `deterministicToken` address is derived as `address(bytes20(keccak256(abi.encodePacked(tokenAddress, tokenId))))`: [4](#0-3) 

This address is not an ERC20 contract. Calling `IERC20(deterministicToken).safeTransfer(recipient, amount)` on it will revert (no code or no matching selector), so the entire `finTransfer` transaction reverts. The nonce is not consumed (state is rolled back), but the ERC1155 tokens remain locked in the bridge.

The `logMetadata1155` function is permissionless (`external payable`, no role check), so anyone can call it to set the mapping and unblock finalization. However, until that call is made, every `finTransfer` attempt for that token will revert. If `deterministicToken` coincidentally resolves to an address that implements `IERC20.transfer` returning `true` (e.g., a deployed ERC20 token), `finTransfer` would succeed, the nonce would be consumed, and the ERC1155 tokens would be permanently locked with no recovery path.

### Impact Explanation

This is a token-mapping corruption issue. The bridge accepts ERC1155 tokens and locks them, but the mapping required for `finTransfer` to use the correct `IERC1155` interface is never written. The wrong interface (`IERC20`) is invoked on the deterministic address, causing finalization to fail. In the common case this is a temporary lock (recoverable by calling `logMetadata1155`). In the edge case where `deterministicToken` coincides with a live ERC20 contract, the nonce is consumed and the ERC1155 tokens are permanently irrecoverable — matching the "irrecoverable lock of user funds in bridge flows" impact class.

### Likelihood Explanation

Any unprivileged user can call `initTransfer1155` without `logMetadata1155` having been called first. There is no guard in `initTransfer1155` that checks whether `multiTokens[deterministicToken]` is already populated. The existing test suite even exercises this path (calling `initTransfer1155` without a prior `logMetadata1155` call) without testing that `finTransfer` subsequently succeeds: [5](#0-4) 

### Recommendation

`initTransfer1155` should populate `multiTokens[deterministicToken]` atomically, exactly as `logMetadata1155` does, before accepting the ERC1155 transfer. Alternatively, add a `require(multiTokens[deterministicToken].tokenAddress != address(0), "ERC1155 not registered")` guard at the top of `initTransfer1155` to enforce that `logMetadata1155` must be called first.

### Proof of Concept

1. Deploy `OmniBridge` with a valid `nearBridgeDerivedAddress`.
2. Mint ERC1155 token ID `7` to `user` and approve the bridge.
3. Call `bridge.initTransfer1155(erc1155Addr, 7, 1, 0, 0, "victim.near", "")` — succeeds, tokens locked, `multiTokens` NOT set.
4. Construct a valid MPC-signed `finTransfer` payload with `tokenAddress = deterministicToken`.
5. Call `bridge.finTransfer(sig, payload)` — reverts at `IERC20(deterministicToken).safeTransfer(...)` because `deterministicToken` has no ERC20 code.
6. ERC1155 tokens remain locked; `finTransfer` cannot succeed until `logMetadata1155` is called externally.
7. If `deterministicToken` happens to be a deployed ERC20 contract: `finTransfer` succeeds, nonce consumed, ERC1155 tokens permanently locked.

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
