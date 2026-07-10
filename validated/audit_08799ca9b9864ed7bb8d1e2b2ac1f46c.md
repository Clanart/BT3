### Title
Native ETH `nativeFee` Permanently Locked in OmniBridge With No Withdrawal Mechanism - (`evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

In `OmniBridge.initTransfer`, users pay a `nativeFee` in ETH alongside the bridging amount. Only `extensionValue = msg.value - nativeFee` is forwarded to `initTransferExtension` (and ultimately to Wormhole). The `nativeFee` portion is retained in the contract but there is no function to withdraw or distribute it, causing it to accumulate permanently with no recovery path.

---

### Finding Description

In `OmniBridge.sol`, `initTransfer` computes `extensionValue` by subtracting `nativeFee` from `msg.value`: [1](#0-0) 

For token transfers: `extensionValue = msg.value - nativeFee`. For ETH transfers: `extensionValue = msg.value - amount - nativeFee`. Only `extensionValue` is passed to `initTransferExtension`: [2](#0-1) 

In `OmniBridgeWormhole`, `initTransferExtension` forwards only `value` (i.e., `extensionValue`) to Wormhole: [3](#0-2) 

The `nativeFee` ETH is never forwarded anywhere. The contract has a bare `receive()` but no ETH withdrawal function: [4](#0-3) 

On the NEAR side, the `native_fee` field in a transfer message is minted to the fee recipient for NEAR-originated transfers, but for EVM-originated transfers the ETH `nativeFee` paid on EVM is never claimed or distributed: [5](#0-4) 

`claim_fee_callback` on NEAR only distributes the token fee (`amount - denormalized_amount`), not the ETH `nativeFee` locked in the EVM contract: [6](#0-5) 

---

### Impact Explanation

Every `initTransfer` call with a non-zero `nativeFee` permanently locks ETH in the `OmniBridge` contract. There is no admin withdrawal function, no fee-distribution function, and no other ETH outflow path (the only ETH outflow is `finTransfer` for native ETH bridging, which sends `payload.amount` to the recipient — not accumulated fees). Over time, all `nativeFee` ETH paid by every bridge user is irrecoverably locked. This matches the allowed impact: **permanent freezing / irrecoverable lock of protocol funds in bridge flows**.

---

### Likelihood Explanation

High. Every user who calls `initTransfer` with a non-zero `nativeFee` (the normal case for paying relayer gas compensation) contributes ETH to the locked pool. This is a routine part of the bridge flow, not an edge case.

---

### Recommendation

Add an admin-restricted function to withdraw accumulated ETH fees, analogous to the fix recommended in the reference report:

```solidity
function withdrawAccumulatedFees(address payable recipient) external onlyRole(DEFAULT_ADMIN_ROLE) {
    uint256 balance = address(this).balance;
    (bool success, ) = recipient.call{value: balance}("");
    if (!success) revert FailedToSendEther();
}
```

Alternatively, forward `nativeFee` directly to a designated fee recipient address inside `initTransfer` rather than retaining it in the contract.

---

### Proof of Concept

1. User calls `initTransfer(tokenAddress, 100e18, 0, 1e18, recipient, "")` with `msg.value = 1e18` (paying 1 ETH as `nativeFee`).
2. `extensionValue = msg.value - nativeFee = 1e18 - 1e18 = 0`.
3. `initTransferExtension` is called with `value = 0`; Wormhole receives 0 ETH.
4. The 1 ETH `nativeFee` remains in `OmniBridge`.
5. No function exists to withdraw it. Repeat for every bridge user — all `nativeFee` ETH is permanently locked.

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L386-413)
```text
        uint256 extensionValue;
        if (tokenAddress == address(0)) {
            if (fee != 0) {
                revert InvalidFee();
            }
            extensionValue = msg.value - amount - nativeFee;
        } else {
            extensionValue = msg.value - nativeFee;
            if (customMinters[tokenAddress] != address(0)) {
                IERC20(tokenAddress).safeTransferFrom(
                    msg.sender,
                    customMinters[tokenAddress],
                    amount
                );
                ICustomMinter(customMinters[tokenAddress]).burn(
                    tokenAddress,
                    amount
                );
            } else if (isBridgeToken[tokenAddress]) {
                BridgeToken(tokenAddress).burn(msg.sender, amount);
            } else {
                IERC20(tokenAddress).safeTransferFrom(
                    msg.sender,
                    address(this),
                    amount
                );
            }
        }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L415-426)
```text
        initTransferExtension(
            msg.sender,
            tokenAddress,
            currentOriginNonce,
            amount,
            fee,
            nativeFee,
            recipient,
            message,
            extensionValue
        );

```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L574-574)
```text
    receive() external payable {}
```

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L118-150)
```text
    function initTransferExtension(
        address sender,
        address tokenAddress,
        uint64 originNonce,
        uint128 amount,
        uint128 fee,
        uint128 nativeFee,
        string calldata recipient,
        string calldata message,
        uint256 value
    ) internal override {
        bytes memory payload = bytes.concat(
            bytes1(uint8(MessageType.InitTransfer)),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(sender),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(tokenAddress),
            Borsh.encodeUint64(originNonce),
            Borsh.encodeUint128(amount),
            Borsh.encodeUint128(fee),
            Borsh.encodeUint128(nativeFee),
            Borsh.encodeString(recipient),
            Borsh.encodeString(message)
        );
        // slither-disable-next-line reentrancy-eth
        _wormhole.publishMessage{value: value}(
            wormholeNonce,
            payload,
            _consistencyLevel
        );

        wormholeNonce++;
    }
```

**File:** near/omni-bridge/src/lib.rs (L1131-1133)
```rust
        let fee = transfer_message.amount.0 - denormalized_amount;

        self.send_fee_internal(&transfer_message, fee_recipient, fee)
```

**File:** near/omni-bridge/src/lib.rs (L1736-1743)
```rust
            if transfer_message.fee.native_fee.0 > 0 {
                let native_token_id = self.get_native_token_id(transfer_message.get_origin_chain());

                ext_token::ext(native_token_id)
                    .with_static_gas(MINT_TOKEN_GAS)
                    .mint(fee_recipient.clone(), transfer_message.fee.native_fee, None)
                    .detach();
            }
```
