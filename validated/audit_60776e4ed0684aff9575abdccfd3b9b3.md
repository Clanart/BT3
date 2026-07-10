### Title
Fee-on-Transfer Token Accounting Mismatch in `initTransfer` Enables Undercollateralized Bridge State - (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge.initTransfer` uses the caller-supplied `amount` parameter directly in the emitted cross-chain event and extension call, without measuring the actual token balance change after `safeTransferFrom`. For fee-on-transfer (deflationary) ERC20 tokens, the bridge receives `amount - transfer_fee` tokens but records and broadcasts `amount` to the destination chain, creating an undercollateralized bridge.

---

### Finding Description

In `OmniBridge.initTransfer`, when the token is neither a bridge-deployed token nor a custom-minter token, the contract pulls tokens from the sender:

```solidity
} else {
    IERC20(tokenAddress).safeTransferFrom(
        msg.sender,
        address(this),
        amount
    );
}
``` [1](#0-0) 

Immediately after, the full caller-supplied `amount` is forwarded to `initTransferExtension` and emitted in the `InitTransfer` event:

```solidity
initTransferExtension(
    msg.sender, tokenAddress, currentOriginNonce,
    amount, fee, nativeFee, recipient, message, extensionValue
);
emit BridgeTypes.InitTransfer(
    msg.sender, tokenAddress, currentOriginNonce,
    amount, fee, nativeFee, recipient, message
);
``` [2](#0-1) 

For fee-on-transfer tokens, `safeTransferFrom(..., amount)` causes the token contract to deduct a fee, so the bridge actually receives `amount - fee_deducted`. The event and the Wormhole/cross-chain message (in `OmniBridgeWormhole.initTransferExtension`) still encode the full `amount`:

```solidity
Borsh.encodeUint128(amount),
``` [3](#0-2) 

The NEAR-side bridge (`fin_transfer_callback`) processes the proof and mints or releases the full `amount` to the recipient:

```solidity
amount: Self::denormalize_amount(init_transfer.amount.0, decimals).into(),
``` [4](#0-3) 

There is no pre/post balance measurement anywhere in the EVM `initTransfer` path to detect the discrepancy.

---

### Impact Explanation

Each `initTransfer` call with a fee-on-transfer token causes the EVM bridge to hold `amount - fee_deducted` tokens while the NEAR bridge mints/releases `amount` tokens. The bridge is undercollateralized by `fee_deducted` per transfer. Repeated transfers drain the EVM-side collateral pool relative to the outstanding NEAR-side supply. When users attempt to bridge back from NEAR to EVM, the EVM bridge will eventually be unable to release the full amount, causing permanent loss of funds for later redeemers. This matches the allowed impact: **balance/accounting corruption that breaks bridge collateralization**.

---

### Likelihood Explanation

Any unprivileged user can call `initTransfer` with any ERC20 token address that is not in `isBridgeToken` and has no `customMinters` entry. Fee-on-transfer tokens (e.g., USDT on some chains, STA, DEFX, and many DeFi tokens) are publicly deployed and usable by anyone. No privileged access is required. The attacker simply calls `initTransfer` with such a token and a valid recipient address.

---

### Recommendation

Measure the actual balance change before and after `safeTransferFrom` and use the measured delta as the canonical `amount` for the cross-chain message:

```solidity
uint256 balanceBefore = IERC20(tokenAddress).balanceOf(address(this));
IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount);
uint256 actualReceived = IERC20(tokenAddress).balanceOf(address(this)) - balanceBefore;
require(actualReceived > 0, "Zero received");
amount = uint128(actualReceived); // use actualReceived for all downstream accounting
```

Apply the same pattern in `finTransfer` for the `safeTransfer` path to ensure the recipient's shortfall is not silently absorbed.

---

### Proof of Concept

1. Deploy or identify a fee-on-transfer ERC20 token `T` (e.g., 1% fee per transfer) that is not registered as a bridge token or custom minter in `OmniBridge`.
2. Approve `OmniBridge` to spend `1000` units of `T`.
3. Call `OmniBridge.initTransfer(T, 1000, 0, 0, "near:alice.near", "")`.
4. Inside `initTransfer`, `safeTransferFrom(msg.sender, address(this), 1000)` executes. Due to the 1% fee, the bridge receives only `990` tokens.
5. The `InitTransfer` event is emitted with `amount = 1000`.
6. The Wormhole/cross-chain message encodes `amount = 1000`.
7. On NEAR, `fin_transfer_callback` processes the proof and mints `1000` tokens to `alice.near`.
8. The EVM bridge holds `990` tokens but has committed to `1000` tokens of NEAR-side supply — a `10`-token deficit per transfer.
9. After 100 such transfers, the EVM bridge holds `99,000` tokens but NEAR has `100,000` tokens outstanding, making `1,000` tokens permanently unclaimable for the last redeemers. [1](#0-0) [5](#0-4) [6](#0-5)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L406-412)
```text
            } else {
                IERC20(tokenAddress).safeTransferFrom(
                    msg.sender,
                    address(this),
                    amount
                );
            }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L415-436)
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

**File:** near/omni-bridge/src/lib.rs (L725-725)
```rust
            amount: Self::denormalize_amount(init_transfer.amount.0, decimals).into(),
```
