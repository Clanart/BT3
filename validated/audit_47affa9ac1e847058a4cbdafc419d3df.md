### Title
ERC1155 `finTransfer` Permanently Locks Tokens When Recipient Contract Lacks `IERC1155Receiver` — (`evm/src/omni-bridge/contracts/OmniBridge.sol`)

### Summary
`OmniBridge.finTransfer` delivers ERC1155 tokens to the recipient via `IERC1155.safeTransferFrom`. If the recipient is a contract that does not implement `IERC1155Receiver`, the call always reverts. Because the recipient address is embedded in the MPC-signed payload and cannot be changed without a new MPC signature, the ERC1155 tokens become permanently irrecoverable from the bridge.

### Finding Description
`finTransfer` dispatches ERC1155 tokens using the safe variant:

```solidity
} else if (multiToken.tokenAddress != address(0)) {
    IERC1155(multiToken.tokenAddress).safeTransferFrom(
        address(this),
        payload.recipient,   // ← contract without IERC1155Receiver → always reverts
        multiToken.tokenId,
        payload.amount,
        ""
    );
}
``` [1](#0-0) 

Per EIP-1155, `safeTransferFrom` calls `onERC1155Received` on the recipient if it is a contract. If the recipient does not implement `IERC1155Receiver`, the ERC1155 token contract reverts the entire call.

The nonce is marked consumed *before* the transfer:

```solidity
completedTransfers[payload.destinationNonce] = true;
``` [2](#0-1) 

Because the revert unwinds the whole transaction, the nonce is *not* permanently consumed — the relayer can retry. However, the `payload.recipient` is part of the MPC-signed `TransferMessagePayload` and is immutable without a fresh MPC signature. If the recipient is a contract that will never implement `IERC1155Receiver` (e.g., a multisig, a DAO treasury, a proxy without the hook), every retry reverts identically. The ERC1155 tokens remain locked in the bridge with no in-protocol escape path.

The bridge itself correctly implements `IERC1155Receiver` for its own inbound transfers:

```solidity
function onERC1155Received(address operator, ...) external view override returns (bytes4) {
    if (operator != address(this)) {
        revert ERC1155DirectSendNotAllowed();
    }
    return this.onERC1155Received.selector;
}
``` [3](#0-2) 

But no equivalent guard or fallback exists for the outbound delivery path.

### Impact Explanation
When a user bridges ERC1155 tokens from EVM → NEAR and then initiates the return leg (NEAR → EVM) specifying a contract recipient that lacks `IERC1155Receiver`:

- The NEAR-side tokens are burned/locked at transfer initiation.
- Every `finTransfer` call on EVM reverts; the ERC1155 tokens remain in the bridge indefinitely.
- There is no in-protocol claim, rescue, or re-route function.
- Recovery requires out-of-band MPC re-signing with a different recipient — a trusted-role dependency outside the protocol's normal flow.

This matches the allowed impact: **permanent freezing / irrecoverable lock of user funds in bridge flows**.

### Likelihood Explanation
ERC1155 bridging is an explicitly supported, publicly callable feature (`initTransfer1155` / `finTransfer`). Contract recipients are common in DeFi: multisigs (Gnosis Safe), DAO treasuries, vaults, and aggregators routinely lack `IERC1155Receiver`. A user who bridges ERC1155 tokens to any such address triggers the lock with no warning. The protocol provides no pre-flight check or documentation of this constraint.

### Recommendation
1. **Pull-based delivery**: Instead of pushing tokens to the recipient in `finTransfer`, record a claimable balance and let the recipient call a `claim` function. This removes the callback dependency entirely.
2. **Try/catch with escrow**: Wrap the `safeTransferFrom` in a try/catch; on failure, credit the amount to an internal claimable mapping keyed by `(recipient, tokenAddress, tokenId)`.
3. **Pre-flight interface check**: Before calling `safeTransferFrom`, use `IERC165(payload.recipient).supportsInterface(type(IERC1155Receiver).interfaceId)` and revert with a descriptive error if the check fails, so the user is informed before the NEAR-side tokens are burned.

### Proof of Concept
1. Alice holds ERC1155 token `(tokenAddress, tokenId=7)` on EVM.
2. Alice calls `logMetadata1155(tokenAddress, 7)` and then `initTransfer1155(tokenAddress, 7, 100, 0, 0, "alice.near", "")`. Tokens are locked in the bridge.
3. NEAR side processes the `InitTransfer` event and credits Alice on NEAR.
4. Alice initiates a return transfer on NEAR, specifying `recipient = GnosisSafe_address` (a contract without `IERC1155Receiver`).
5. MPC signs the `TransferMessagePayload` with `recipient = GnosisSafe_address`.
6. Relayer calls `finTransfer(sig, payload)` on EVM.
7. `finTransfer` reaches line 324 and calls `IERC1155(tokenAddress).safeTransferFrom(bridge, GnosisSafe_address, 7, 100, "")`.
8. The ERC1155 token calls `GnosisSafe_address.onERC1155Received(...)` — Gnosis Safe does not implement this hook → reverts.
9. The entire `finTransfer` transaction reverts. `completedTransfers[nonce]` is unwound.
10. Every subsequent retry with the same signed payload reverts identically.
11. Alice's 100 ERC1155 tokens remain locked in `OmniBridge` with no recovery path. [4](#0-3) [5](#0-4)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L279-355)
```text
    function finTransfer(
        bytes calldata signatureData,
        BridgeTypes.TransferMessagePayload calldata payload
    ) external payable whenNotPaused(PAUSED_FIN_TRANSFER) {
        if (completedTransfers[payload.destinationNonce]) {
            revert NonceAlreadyUsed(payload.destinationNonce);
        }

        completedTransfers[payload.destinationNonce] = true;

        bytes memory borshEncoded = bytes.concat(
            bytes1(uint8(BridgeTypes.PayloadType.TransferMessage)),
            Borsh.encodeUint64(payload.destinationNonce),
            bytes1(payload.originChain),
            Borsh.encodeUint64(payload.originNonce),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(payload.tokenAddress),
            Borsh.encodeUint128(payload.amount),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(payload.recipient),
            bytes(payload.feeRecipient).length == 0 // None or Some(String) in rust
                ? bytes("\x00")
                : bytes.concat(
                    bytes("\x01"),
                    Borsh.encodeString(payload.feeRecipient)
                ),
            bytes(payload.message).length == 0
                ? bytes("")
                : Borsh.encodeBytes(payload.message)
        );
        bytes32 hashed = keccak256(borshEncoded);

        if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
            revert InvalidSignature();
        }

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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L522-535)
```text
    function onERC1155Received(
        address operator,
        address,
        uint256,
        uint256,
        bytes calldata
    ) external view override returns (bytes4) {
        // Only accept transfers that were initiated by this contract itself
        if (operator != address(this)) {
            revert ERC1155DirectSendNotAllowed();
        }

        return this.onERC1155Received.selector;
    }
```
