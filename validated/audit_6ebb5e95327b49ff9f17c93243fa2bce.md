### Title
Accumulated `nativeFee` ETH Is Permanently Locked in `OmniBridge` — No `claimNativeFee` Handler Implemented - (File: evm/src/omni-bridge/contracts/OmniBridge.sol)

---

### Summary

Every call to `initTransfer` and `initTransfer1155` on the EVM bridge retains the `nativeFee` portion of `msg.value` inside the contract. The Wormhole extension only forwards `extensionValue = msg.value - nativeFee` to Wormhole; the `nativeFee` slice is never forwarded, never refunded, and never distributed. Although `BridgeTypes.PayloadType.ClaimNativeFee` (enum value `2`) exists in the type system, no corresponding handler function is implemented in either `OmniBridge` or `OmniBridgeWormhole`. The ETH is irrecoverably locked.

---

### Finding Description

`initTransfer` splits `msg.value` into three parts:

```
// ERC-20 path
extensionValue = msg.value - nativeFee;   // forwarded to Wormhole

// ETH path
extensionValue = msg.value - amount - nativeFee;  // forwarded to Wormhole
``` [1](#0-0) 

`initTransfer1155` does the same:

```
extensionValue = msg.value - nativeFee;
``` [2](#0-1) 

In `OmniBridgeWormhole.initTransferExtension`, only `value` (i.e., `extensionValue`) is forwarded to Wormhole:

```solidity
_wormhole.publishMessage{value: value}(wormholeNonce, payload, _consistencyLevel);
``` [3](#0-2) 

The `nativeFee` delta (`msg.value - extensionValue`) is silently retained by the contract. There is no `withdraw`, `rescueETH`, or `claimNativeFee` function anywhere in `OmniBridge.sol` or `OmniBridgeWormhole.sol`.

The `BridgeTypes.PayloadType` enum does define a `ClaimNativeFee` variant (value `2`), signalling that a claim path was planned:

```solidity
enum PayloadType {
    TransferMessage,
    Metadata,
    ClaimNativeFee      // ← defined but never handled on EVM
}
``` [4](#0-3) 

The NEAR type system also carries the variant: [5](#0-4) 

Neither `OmniBridge` nor `OmniBridgeWormhole` contains any function that decodes a `ClaimNativeFee` payload and transfers the accumulated ETH to a relayer or fee recipient. The contract does have a bare `receive()`:

```solidity
receive() external payable {}
``` [6](#0-5) 

but no corresponding egress path for ETH.

---

### Impact Explanation

Every EVM→NEAR transfer that carries a non-zero `nativeFee` permanently destroys that ETH inside the bridge contract. Because `nativeFee` is the on-chain incentive for relayers to call `sign_transfer` on NEAR (paying MPC signing costs), locking it also removes the economic incentive for relayers, which can stall pending transfers. The ETH is unrecoverable by any party — user, relayer, or admin — because no withdrawal path exists.

This matches the allowed impact: **Critical — permanent freezing / irrecoverable lock of user funds in bridge flows.**

---

### Likelihood Explanation

The `nativeFee` parameter is user-supplied and explicitly documented in the `InitTransfer` event. Any user who sets `nativeFee > 0` (the normal case when paying a relayer) triggers the lock. The `ClaimNativeFee` enum value confirms the team intended to implement a claim path; its absence means the bug is present in every deployed version of the contract until the handler is added.

---

### Recommendation

Implement a `claimNativeFee` function that:
1. Accepts a signed `ClaimNativeFee` payload from the NEAR MPC signer (analogous to `finTransfer`).
2. Verifies the ECDSA signature against `nearBridgeDerivedAddress`.
3. Transfers the specified ETH amount to the designated fee recipient.

Until then, either revert when `nativeFee > 0` is passed, or refund `nativeFee` to `msg.sender` at the end of `initTransfer`/`initTransfer1155`.

---

### Proof of Concept

1. Alice calls `initTransfer(tokenAddress=USDC, amount=1000e6, fee=0, nativeFee=0.01 ether, recipient="alice.near", message="")` and sends `msg.value = 0.01 ether + wormhole_fee`.
2. Inside `initTransfer`: `extensionValue = msg.value - 0.01 ether = wormhole_fee`.
3. `initTransferExtension` forwards only `wormhole_fee` to Wormhole; `0.01 ether` stays in the contract.
4. The `InitTransfer` event is emitted with `nativeFee = 0.01 ether`.
5. The NEAR bridge records the `nativeFee` but no EVM function exists to release it.
6. Alice's `0.01 ether` is permanently locked. Repeating across all users, the total locked ETH grows unboundedly with no recovery path. [7](#0-6) [8](#0-7)

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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L466-466)
```text
        uint256 extensionValue = msg.value - nativeFee;
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

**File:** evm/src/omni-bridge/contracts/BridgeTypes.sol (L67-71)
```text
    enum PayloadType {
        TransferMessage,
        Metadata,
        ClaimNativeFee
    }
```

**File:** near/omni-types/src/lib.rs (L1-5)
```rust
use std::string::ToString;

use borsh::{BorshDeserialize, BorshSerialize};
use core::fmt;
use core::str::FromStr;
```
