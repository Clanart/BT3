### Title
Fee-on-Transfer Token Accounting Mismatch in `initTransfer` Breaks Bridge Collateralization - (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge.initTransfer()` records and broadcasts the caller-supplied `amount` rather than the actual tokens received by the contract. For fee-on-transfer ERC20 tokens, the bridge holds fewer tokens than the cross-chain message claims, creating unbacked supply on NEAR and permanently locking funds when users attempt to bridge back.

---

### Finding Description

In `OmniBridge.initTransfer()`, when the token is a plain ERC20 (not a bridge token and not a custom-minter token), the contract pulls tokens from the caller: [1](#0-0) 

```solidity
} else {
    IERC20(tokenAddress).safeTransferFrom(
        msg.sender,
        address(this),
        amount
    );
}
```

Immediately after, the function emits `InitTransfer` — and in the Wormhole variant, publishes a cross-chain message — using the **caller-supplied** `amount`, not the actual balance delta received: [2](#0-1) 

In `OmniBridgeWormhole.initTransferExtension()`, the same `amount` is Borsh-encoded into the Wormhole payload sent to NEAR: [3](#0-2) 

For a fee-on-transfer token, `safeTransferFrom` delivers `amount - transfer_fee` to the bridge, but the cross-chain message asserts `amount`. NEAR processes the full `amount`, minting or releasing that quantity to the recipient. The EVM bridge's actual reserve is permanently short by `transfer_fee`.

There is no balance-before / balance-after check anywhere in the transfer path.

---

### Impact Explanation

Two concrete impacts arise:

1. **Unbacked supply / collateralization break (High):** Every `initTransfer` with a fee-on-transfer token inflates the NEAR-side supply relative to the EVM-side reserve. The bridge is no longer fully collateralized; the deficit grows with each such transfer.

2. **Permanent fund lock (Critical):** When any user later bridges the token back from NEAR to EVM via `finTransfer`, the bridge attempts `safeTransfer(recipient, amount)` from its own balance. Because the reserve is short by the accumulated fee shortfall, the transfer will revert once the deficit exceeds the remaining balance, permanently locking those tokens in the bridge contract.

Both impacts fall within the allowed scope:
- *High*: Balance/accounting corruption that breaks bridge collateralization.
- *Critical*: Permanent freezing / irrecoverable lock of user funds in bridge flows.

---

### Likelihood Explanation

- Any unprivileged user can call `initTransfer()` — no role or special permission is required.
- Fee-on-transfer tokens (e.g., STA, PAXG, tokens with deflationary mechanics) are a well-known ERC20 variant and are not blocked by any allowlist or check in the contract.
- The bridge does not validate that `tokenAddress` is a "standard" ERC20, so the path is fully reachable by a regular token holder.

---

### Recommendation

Measure the actual received amount using a balance-before / balance-after pattern and use that value for the cross-chain message:

```solidity
} else {
    uint256 balanceBefore = IERC20(tokenAddress).balanceOf(address(this));
    IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount);
    uint256 received = IERC20(tokenAddress).balanceOf(address(this)) - balanceBefore;
    require(received > 0, "Zero received");
    amount = uint128(received); // use actual received amount downstream
}
```

The corrected `amount` must then be passed to `initTransferExtension` and emitted in `InitTransfer`, so the NEAR side processes only what the bridge actually holds.

Alternatively, explicitly disallow fee-on-transfer tokens via a registry or a pre-transfer balance check that reverts if `received != amount`.

---

### Proof of Concept

1. Deploy or use any fee-on-transfer ERC20 token `T` (e.g., 1% fee per transfer) on the EVM chain.
2. Call `OmniBridge.initTransfer(address(T), 1000e18, 0, 0, "alice.near", "")`.
3. `safeTransferFrom` delivers `990e18` to the bridge (1% fee taken).
4. `InitTransfer` event and Wormhole message are emitted with `amount = 1000e18`.
5. NEAR bridge processes the message and releases/mints `1000e18` tokens to `alice.near`.
6. The EVM bridge now holds `990e18` but has committed to `1000e18`.
7. Repeat N times; the deficit accumulates.
8. When any user bridges back `amount_X` tokens from NEAR to EVM, `finTransfer` calls `IERC20(T).safeTransfer(recipient, amount_X)`. Once the bridge's balance is exhausted by the accumulated shortfall, this reverts — those tokens are permanently locked. [4](#0-3) [5](#0-4)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L373-437)
```text
    function initTransfer(
        address tokenAddress,
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

        emit BridgeTypes.InitTransfer(
            msg.sender,
            tokenAddress,
            currentOriginNonce,
            amount,
            fee,
            nativeFee,
            recipient,
            message
        );
    }
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
