### Title
Missing Registration Check in `initTransfer1155` Allows Permanent Lock of ERC1155 Tokens - (File: evm/src/omni-bridge/contracts/OmniBridge.sol)

### Summary
`initTransfer1155` in `OmniBridge.sol` locks ERC1155 tokens and emits a cross-chain `InitTransfer` event without verifying that the token has been registered in the `multiTokens` mapping via `logMetadata1155`. The `finTransfer` function correctly guards ERC1155 processing with a `multiToken.tokenAddress != address(0)` check, but this guard is absent from `initTransfer1155`. A user who calls `initTransfer1155` before the token is registered causes their ERC1155 tokens to be permanently locked in the bridge with no on-chain recovery path.

### Finding Description
The `logMetadata1155` function registers an ERC1155 token by populating `multiTokens[deterministicToken]` and emitting a `LogMetadata` event that the NEAR side processes to set `token_decimals` for the derived token address. [1](#0-0) 

`finTransfer` correctly checks whether the token is registered before attempting an ERC1155 transfer: [2](#0-1) 

However, `initTransfer1155` performs no equivalent check. It derives `deterministicToken`, immediately locks the caller's ERC1155 tokens, and emits `InitTransfer` — all without verifying `multiTokens[deterministicToken].tokenAddress != address(0)`: [3](#0-2) 

On the NEAR side, `fin_transfer_callback` will panic with `TokenDecimalsNotFound` for any token whose `token_decimals` entry was never populated (i.e., whose `LogMetadata` event was never emitted and processed): [4](#0-3) 

Because the NEAR transaction panics, no finalization occurs and no refund is triggered. The EVM contract contains no admin rescue function for locked ERC1155 tokens; the only standard release path is `finTransfer` with a valid NEAR-signed payload, which NEAR will never produce for an unregistered token.

### Impact Explanation
A user who calls `initTransfer1155` before `logMetadata1155` has been called (and processed by NEAR) will have their ERC1155 tokens permanently locked in the bridge contract. There is no on-chain recovery mechanism: `finTransfer` requires a NEAR MPC signature that will never be issued for an unregistered token, and no admin withdrawal function exists for ERC1155 assets. This constitutes an irrecoverable lock of user funds in the bridge vault flow.

### Likelihood Explanation
`logMetadata1155` and `initTransfer1155` are separate, permissionless calls with no on-chain ordering enforcement. A user unfamiliar with the required two-step flow (register then transfer) can trivially call `initTransfer1155` first. The protocol provides no revert, warning, or guard to prevent this ordering mistake, making accidental permanent loss realistic for any ERC1155 user.

### Recommendation
Add a registration guard at the top of `initTransfer1155`, mirroring the check already present in `finTransfer`:

```solidity
function initTransfer1155(
    address tokenAddress,
    uint256 tokenId,
    ...
) external payable whenNotPaused(PAUSED_INIT_TRANSFER) {
    address deterministicToken = deriveDeterministicAddress(tokenAddress, tokenId);
    // Analog of the finTransfer guard — enforce registration before locking
    require(
        multiTokens[deterministicToken].tokenAddress != address(0),
        "ERR_TOKEN_NOT_REGISTERED"
    );
    ...
}
```

This ensures that ERC1155 tokens can only be locked after `logMetadata1155` has been called and the `multiTokens` mapping has been populated, matching the validation already enforced on the release path in `finTransfer`.

### Proof of Concept
1. Attacker or naive user holds ERC1155 token `(tokenAddress, tokenId)` that has never been passed to `logMetadata1155`.
2. User calls `initTransfer1155(tokenAddress, tokenId, amount, 0, 0, recipient, "")`.
3. `IERC1155(tokenAddress).safeTransferFrom(msg.sender, address(this), tokenId, amount, "")` succeeds — tokens are now held by the bridge.
4. `InitTransfer` event is emitted with `deterministicToken` as the token address.
5. Relayer submits proof to NEAR `fin_transfer`.
6. NEAR `fin_transfer_callback` calls `self.token_decimals.get(&init_transfer.token).near_expect(BridgeError::TokenDecimalsNotFound)` — panics because no `LogMetadata` event was ever processed for this token.
7. NEAR transaction reverts; no finalization, no refund.
8. ERC1155 tokens remain permanently locked in the EVM bridge contract with no recovery path.

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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L315-330)
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

**File:** near/omni-bridge/src/lib.rs (L715-718)
```rust
        let decimals = self
            .token_decimals
            .get(&init_transfer.token)
            .near_expect(BridgeError::TokenDecimalsNotFound);
```
