### Title
`OmniBridgeWormhole` Permanently Non-Initializable Due to `OmniBridge.initialize` Using `initializer` Instead of `onlyInitializing` — (File: `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol`, `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridgeWormhole.initializeWormhole` is marked `initializer` and internally calls `OmniBridge.initialize`, which is also marked `initializer`. Under OpenZeppelin Contracts Upgradeable v5 (the version pinned in this repo), calling one `initializer`-guarded function from within another always reverts with `InvalidInitialization()`. As a result, `OmniBridgeWormhole` can never be initialized through its intended entry point, making the Wormhole bridge variant permanently non-functional.

---

### Finding Description

`OmniBridge.initialize` is declared `public initializer`:

```solidity
// OmniBridge.sol line 72-76
function initialize(
    address tokenImplementationAddress_,
    address nearBridgeDerivedAddress_,
    uint8 omniBridgeChainId_
) public initializer {
```

`OmniBridgeWormhole.initializeWormhole` is also declared `external initializer` and calls `initialize` from within its body:

```solidity
// OmniBridgeWormhole.sol line 32-46
function initializeWormhole(...) external initializer {
    initialize(
        tokenImplementationAddress,
        nearBridgeDerivedAddress,
        omniBridgeChainId
    );
    _wormhole = IWormhole(wormholeAddress);
    _consistencyLevel = consistencyLevel;
}
```

The project uses `@openzeppelin/contracts-upgradeable: ^5.4.0` (confirmed in `evm/package.json`). In OZ v5, the `initializer` modifier enforces:

```
bool initialSetup = initialized == 0 && isTopLevelCall;
bool construction = initialized == 1 && address(this).code.length == 0;
if (!initialSetup && !construction) revert InvalidInitialization();
```

When `initializeWormhole` is entered as the top-level call:
- `_initialized` is set to `1` and `_initializing` is set to `true`.

When `initialize` is then called from within `initializeWormhole`:
- `isTopLevelCall = false` (because `_initializing == true`)
- `initialized = 1`
- `initialSetup = (1 == 0 && false) = false`
- `construction = (1 == 1 && proxy.code.length == 0) = false` (proxy has bytecode)
- **Reverts with `InvalidInitialization()`**

The correct OZ v5 pattern for a base-contract initializer that must be callable from a child initializer is to use `onlyInitializing`, not `initializer`. `OmniBridge.initialize` should be `onlyInitializing` so that `initializeWormhole` can invoke it while holding the top-level initializer lock.

---

### Impact Explanation

Because `initializeWormhole` always reverts, the only viable fallback is to call `OmniBridge.initialize` directly on the `OmniBridgeWormhole` proxy. This succeeds but leaves `_wormhole` and `_consistencyLevel` at their zero values. Every bridge operation in `OmniBridgeWormhole` that overrides an extension hook (`deployTokenExtension`, `logMetadataExtension`, `finTransferExtension`, `initTransferExtension`) calls `_wormhole.publishMessage{value: msg.value}(...)`. With `_wormhole == address(0)`, the EVM CALL to the zero address succeeds silently (no code at `address(0)`, call returns success with empty data), the `uint64 sequence` return is decoded as `0`, and the Wormhole cross-chain message is **silently dropped**. Any ETH forwarded as the Wormhole message fee is burned to `address(0)`.

Concretely:
- `deployToken` completes on EVM but the Wormhole VAA is never emitted → NEAR never learns of the new token deployment.
- `finTransfer` marks the nonce used and releases tokens on EVM but the Wormhole VAA is never emitted → the settlement is invisible cross-chain.
- `initTransfer` burns/locks user tokens on EVM but the Wormhole VAA is never emitted → the transfer is permanently unclaimable on the destination chain.

This constitutes **permanent freezing and irrecoverable lock of user funds** in the bridge flow, and **accounting corruption** that breaks bridge collateralization.

---

### Likelihood Explanation

The hardhat deployment task explicitly uses `initializeWormhole` as the initializer for `OmniBridgeWormhole`. Every deployment attempt via `upgrades.deployProxy` with `initializer: "initializeWormhole"` will revert. The bug is triggered unconditionally on the first deployment attempt with no special attacker action required — any operator following the documented deployment path hits it immediately.

---

### Recommendation

Change `OmniBridge.initialize` from `initializer` to `onlyInitializing` so it can be safely called from within a child `initializer`:

```solidity
// OmniBridge.sol
function initialize(
    address tokenImplementationAddress_,
    address nearBridgeDerivedAddress_,
    uint8 omniBridgeChainId_
) public onlyInitializing {   // ← was `initializer`
    ...
}
```

`OmniBridgeWormhole.initializeWormhole` retains `initializer` as the single top-level entry point. This matches the standard OZ v5 pattern for base/child upgradeable contracts.

---

### Proof of Concept

1. Deploy `OmniBridgeWormhole` implementation (constructor calls `_disableInitializers()`).
2. Deploy `ERC1967Proxy` pointing to the implementation, passing ABI-encoded `initializeWormhole(...)` calldata.
3. The proxy constructor delegatecalls `initializeWormhole`:
   - `initializeWormhole` enters the `initializer` guard: sets `_initialized = 1`, `_initializing = true`.
   - `initializeWormhole` calls `initialize(...)`.
   - `initialize` enters its own `initializer` guard: `isTopLevelCall = false`, `initialized = 1`, `initialSetup = false`, `construction = false` → **reverts with `InvalidInitialization()`**.
4. The entire proxy deployment transaction reverts.
5. Alternatively, call `initialize` directly on the proxy (bypasses `initializeWormhole`): succeeds, but `_wormhole = address(0)`. Any subsequent `initTransfer` call burns user tokens and emits no Wormhole VAA; the transfer is permanently unclaimable on NEAR. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L72-76)
```text
    function initialize(
        address tokenImplementationAddress_,
        address nearBridgeDerivedAddress_,
        uint8 omniBridgeChainId_
    ) public initializer {
```

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L32-46)
```text
    function initializeWormhole(
        address tokenImplementationAddress,
        address nearBridgeDerivedAddress,
        uint8 omniBridgeChainId,
        address wormholeAddress,
        uint8 consistencyLevel
    ) external initializer {
        initialize(
            tokenImplementationAddress,
            nearBridgeDerivedAddress,
            omniBridgeChainId
        );
        _wormhole = IWormhole(wormholeAddress);
        _consistencyLevel = consistencyLevel;
    }
```

**File:** evm/package.json (L7-9)
```json
    "@openzeppelin/contracts": "^5.4.0",
    "@openzeppelin/contracts-upgradeable": "^5.4.0"
  },
```
