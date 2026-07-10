### Title
Fee-on-Transfer Token Accounting Corruption in `initTransfer` Breaks Bridge Collateralization — (`evm/src/omni-bridge/contracts/OmniBridge.sol`)

### Summary

`OmniBridge.initTransfer` transfers ERC20 tokens from the caller using `safeTransferFrom` and immediately records the caller-supplied `amount` in the emitted `InitTransfer` event (and Wormhole message) without verifying the actual balance received. For fee-on-transfer ERC20 tokens the bridge receives `amount − transferFee` but broadcasts `amount` to the NEAR side, which mints or releases the full `amount`. This permanently undercollateralizes the EVM vault for that token.

### Finding Description

In `OmniBridge.sol`, `initTransfer` handles non-bridge, non-custom ERC20 tokens with a plain `safeTransferFrom`:

```solidity
} else {
    IERC20(tokenAddress).safeTransferFrom(
        msg.sender,
        address(this),
        amount          // ← stated amount, not verified against actual receipt
    );
}
``` [1](#0-0) 

Immediately after, the function emits the event and calls `initTransferExtension` with the same caller-supplied `amount`:

```solidity
emit BridgeTypes.InitTransfer(
    msg.sender,
    tokenAddress,
    currentOriginNonce,
    amount,   // ← unverified
    fee,
    nativeFee,
    recipient,
    message
);
``` [2](#0-1) 

There is no balance-before / balance-after check anywhere in `initTransfer` or `initTransferExtension`. [3](#0-2) 

The Wormhole variant (`OmniBridgeWormhole`) inherits this path unchanged and publishes the same unverified `amount` into the Wormhole message: [4](#0-3) 

The NEAR bridge's `fin_transfer_callback` then denormalizes and mints/releases exactly the amount encoded in the proof: [5](#0-4) 

### Impact Explanation

For any ERC20 token that deducts a transfer fee (fee-on-transfer tokens), the EVM vault holds `amount − fee` while the NEAR side mints/releases `amount`. Every such deposit permanently inflates the NEAR-side supply relative to the EVM collateral. When users later bridge back, the EVM vault cannot cover all outstanding NEAR-side balances, resulting in direct theft of funds from later withdrawers or permanent freezing of the shortfall.

This matches the allowed impact: **Balance, decimal, fee, token-mapping, or accounting corruption that breaks bridge collateralization or misdirects value.**

### Likelihood Explanation

`initTransfer` has no token whitelist; any caller can supply any ERC20 address. Fee-on-transfer tokens are deployed on mainnet (e.g., deflationary tokens, tokens with configurable transfer taxes). The only prerequisite is that the token has been registered on the NEAR side via `log_metadata` / `bind_token`, which is a permissionless flow available to any user. A single deposit with a fee-on-transfer token is sufficient to trigger the accounting gap.

### Recommendation

Record the vault balance before and after the `safeTransferFrom` and use the actual received amount for the event and cross-chain message, mirroring the fix pattern from the referenced report:

```solidity
} else {
    uint256 balanceBefore = IERC20(tokenAddress).balanceOf(address(this));
    IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount);
    uint256 received = IERC20(tokenAddress).balanceOf(address(this)) - balanceBefore;
    require(received > 0, "zero received");
    amount = uint128(received); // use actual received amount downstream
}
```

Apply the same pattern in `initTransfer1155` if ERC-1155 tokens with transfer hooks are ever supported.

### Proof of Concept

1. Deploy or use an existing fee-on-transfer ERC20 token `T` with a 1% transfer tax. Register `T` on the NEAR bridge via `log_metadata` + `bind_token`.
2. Call `OmniBridge.initTransfer(T, 1_000_000, 0, 0, nearRecipient, "")`.
3. The bridge receives `990_000` tokens (1% fee deducted) but emits `InitTransfer(..., amount=1_000_000, ...)`.
4. The Wormhole/MPC message carries `1_000_000`. NEAR's `fin_transfer_callback` mints `1_000_000` tokens to `nearRecipient`.
5. The EVM vault is now short by `10_000` tokens. Repeat to widen the gap.
6. When any user bridges back the full minted supply, the last `10_000`-worth of withdrawals cannot be fulfilled — funds are permanently frozen or stolen from other depositors.

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

**File:** near/omni-bridge/src/lib.rs (L715-732)
```rust
        let decimals = self
            .token_decimals
            .get(&init_transfer.token)
            .near_expect(BridgeError::TokenDecimalsNotFound);

        let destination_nonce =
            self.get_next_destination_nonce(init_transfer.recipient.get_chain());
        let transfer_message = TransferMessage {
            origin_nonce: init_transfer.origin_nonce,
            token: init_transfer.token,
            amount: Self::denormalize_amount(init_transfer.amount.0, decimals).into(),
            recipient: init_transfer.recipient,
            fee: Self::denormalize_fee(&init_transfer.fee, decimals),
            sender: init_transfer.sender,
            msg: init_transfer.msg,
            destination_nonce,
            origin_transfer_id: None,
        };
```
