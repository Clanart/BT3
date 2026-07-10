### Title
Excess `msg.value` Permanently Lost in `OmniBridgeWormhole` Due to Missing Strict Fee Validation — (File: `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol`)

---

### Summary

`OmniBridgeWormhole` forwards `msg.value` (or a derived `extensionValue`) directly to `_wormhole.publishMessage{value: ...}(...)` without enforcing that the caller sent exactly the required Wormhole fee. Any excess ETH is silently forwarded to the Wormhole core contract, which does not refund it, causing permanent loss of user or relayer funds.

---

### Finding Description

**Vulnerability class:** Fee/value accounting — excess `msg.value` not refunded.

There are two affected call sites in `OmniBridgeWormhole`:

**1. `finTransferExtension` — entire `msg.value` forwarded to Wormhole** [1](#0-0) 

The function passes `msg.value` verbatim to `_wormhole.publishMessage`. The only implicit constraint is that `msg.value >= wormholeFee` (Wormhole reverts if underpaid). There is no upper-bound check and no refund path. Any ETH above the Wormhole fee is consumed by the Wormhole core contract and never returned.

**2. `initTransferExtension` — `extensionValue` (= `msg.value − nativeFee`) forwarded to Wormhole** [2](#0-1) 

The base `OmniBridge.initTransfer` computes:

```
extensionValue = msg.value - nativeFee          // ERC-20 path
extensionValue = msg.value - amount - nativeFee // native ETH path
``` [3](#0-2) 

This `extensionValue` is then passed as `value` to `initTransferExtension`, which forwards it to Wormhole:

```solidity
_wormhole.publishMessage{value: value}(wormholeNonce, payload, _consistencyLevel);
``` [4](#0-3) 

The base contract's `initTransferExtension` protects against this by reverting when `value != 0`: [5](#0-4) 

But `OmniBridgeWormhole` overrides this guard entirely, replacing it with an unconditional forward to Wormhole. If `msg.value > nativeFee + wormholeFee`, the surplus is silently lost.

---

### Impact Explanation

Any ETH above the exact Wormhole fee is forwarded to the Wormhole core contract. Wormhole's `publishMessage` only requires `msg.value >= messageFee` and does not refund the surplus. The excess ETH is permanently locked in the Wormhole contract with no recovery path for the sender. This constitutes permanent loss of user funds — matching the "High. Balance, decimal, fee, token-mapping, or accounting corruption that breaks bridge collateralization or misdirects value" impact class.

---

### Likelihood Explanation

Low. A user or relayer must send more ETH than required. This can happen when:
- The Wormhole fee changes between the time the user estimates it and the time the transaction is submitted.
- A user manually constructs a transaction and miscalculates the required `msg.value` (e.g., adds `nativeFee + wormholeFee` but also includes an extra buffer).
- A relayer reuses a stale fee estimate.

---

### Recommendation

In `OmniBridgeWormhole.initTransferExtension`, enforce that `value` equals exactly the Wormhole message fee:

```solidity
uint256 wormholeFee = _wormhole.messageFee();
require(value == wormholeFee, "Incorrect Wormhole fee");
_wormhole.publishMessage{value: wormholeFee}(...);
```

In `OmniBridgeWormhole.finTransferExtension`, apply the same strict check:

```solidity
uint256 wormholeFee = _wormhole.messageFee();
require(msg.value == wormholeFee, "Incorrect Wormhole fee");
_wormhole.publishMessage{value: wormholeFee}(...);
```

Alternatively, refund any surplus to `msg.sender` after the Wormhole call, though strict equality is safer and avoids reentrancy considerations.

---

### Proof of Concept

1. Wormhole message fee is 0.001 ETH.
2. User calls `OmniBridgeWormhole.initTransfer(erc20Token, amount, fee, nativeFee, recipient, "")` with `msg.value = nativeFee + 0.002 ETH` (user over-estimated the Wormhole fee by 0.001 ETH).
3. `extensionValue = msg.value - nativeFee = 0.002 ETH`.
4. `initTransferExtension` calls `_wormhole.publishMessage{value: 0.002 ETH}(...)`.
5. Wormhole accepts the call (0.002 ETH ≥ 0.001 ETH fee) and retains the full 0.002 ETH.
6. The 0.001 ETH surplus is permanently lost; no refund is issued to the user.

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L96-116)
```text
    function finTransferExtension(
        BridgeTypes.TransferMessagePayload memory payload
    ) internal override {
        bytes memory messagePayload = bytes.concat(
            bytes1(uint8(MessageType.FinTransfer)),
            bytes1(payload.originChain),
            Borsh.encodeUint64(payload.originNonce),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(payload.tokenAddress),
            Borsh.encodeUint128(payload.amount),
            Borsh.encodeString(payload.feeRecipient)
        );
        // slither-disable-next-line reentrancy-eth
        _wormhole.publishMessage{value: msg.value}(
            wormholeNonce,
            messagePayload,
            _consistencyLevel
        );

        wormholeNonce++;
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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L492-506)
```text
    function initTransferExtension(
        address /*sender*/,
        address /*tokenAddress*/,
        uint64 /*originNonce*/,
        uint128 /*amount*/,
        uint128 /*fee*/,
        uint128 /*nativeFee*/,
        string calldata /*recipient*/,
        string calldata /*message*/,
        uint256 value
    ) internal virtual {
        if (value != 0) {
            revert InvalidValue();
        }
    }
```
