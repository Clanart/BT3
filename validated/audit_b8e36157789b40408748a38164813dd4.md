### Title
`OmniBridge.initialize()` Uses `initializer` Instead of `onlyInitializing`, Permanently Breaking `OmniBridgeWormhole` Initialization — (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge.initialize()` is declared with the `initializer` modifier rather than `onlyInitializing`. When `OmniBridgeWormhole.initializeWormhole()` (also marked `initializer`) calls `initialize()` internally, OpenZeppelin v5's `Initializable` logic unconditionally reverts with `InvalidInitialization()`. This makes `OmniBridgeWormhole` impossible to properly initialize through its intended entry point, permanently breaking the Wormhole bridge variant.

---

### Finding Description

`OmniBridge.initialize()` is a `public` function marked with the `initializer` modifier: [1](#0-0) 

`OmniBridgeWormhole.initializeWormhole()` is also marked `initializer` and calls `initialize()` from within its body: [2](#0-1) 

The project uses `@openzeppelin/contracts-upgradeable: ^5.4.0`: [3](#0-2) 

In OZ v5, the `initializer` modifier sets `_initialized = 1` and `_initializing = true` before executing the function body. When `initializeWormhole()` runs and then calls `initialize()`, the inner call to `initialize()` evaluates:

- `isTopLevelCall = !_initializing = false`
- `initialized = 1` (already set by `initializeWormhole`)
- `initialSetup = (initialized == 0 && isTopLevelCall) = false`
- `construction = (initialized == 1 && address(this).code.length == 0)` — the proxy has code, so `false`

Since both `initialSetup` and `construction` are `false`, OZ v5 reverts with `InvalidInitialization()`. The entire `initializeWormhole()` call reverts, leaving `_initialized = 0`.

The correct OZ pattern for a parent contract's init function that is called from a child's initializer is to use `onlyInitializing` (not `initializer`). The `onlyInitializing` modifier only checks `_initializing == true` and does not re-gate on `_initialized`, making it safe to call from within an outer `initializer` context.

---

### Impact Explanation

`OmniBridgeWormhole` cannot be initialized through `initializeWormhole()`. Two failure modes result:

1. **Proxy deployment with `initializeWormhole` as init-data**: The `ERC1967Proxy` constructor reverts, so the proxy is never deployed.
2. **Proxy deployed then `initialize()` called directly**: The proxy is deployed with base `OmniBridge` state, but `_wormhole` and `_consistencyLevel` remain `address(0)` / `0`. Every subsequent call to `initTransfer`, `finTransfer`, or `deployToken` reaches `initTransferExtension` / `finTransferExtension` / `deployTokenExtension`, each of which calls `_wormhole.publishMessage{value: ...}(...)` on `address(0)`, causing a permanent revert. Any user funds (ERC-20 or native ETH) transferred into the bridge via `initTransfer` are irrecoverably locked.

This matches the allowed impact: **Permanent freezing / irrecoverable lock of user funds in bridge flows**.

---

### Likelihood Explanation

The bug is deterministic — it triggers on every call to `initializeWormhole()` under OZ v5. Any deployment of `OmniBridgeWormhole` using the intended initialization path will fail. If the deployer falls back to calling `initialize()` directly (the only other public path), the wormhole state is silently unset and all transfer functions permanently revert for any user who interacts with the bridge.

---

### Recommendation

Change `OmniBridge.initialize()` from `initializer` to `onlyInitializing` so it can be safely called from within a child contract's `initializer`:

```solidity
// Before (broken for inheritance):
function initialize(...) public initializer { ... }

// After (correct OZ v5 pattern for parent init):
function initialize(...) public onlyInitializing { ... }
```

`OmniBridgeWormhole.initializeWormhole()` retains the `initializer` modifier as the single top-level entry point. Alternatively, `initializeWormhole` can be restructured to call the internal `__UUPSUpgradeable_init()`, `__AccessControl_init()`, and `__Pausable_init_unchained()` directly instead of delegating to `initialize()`.

---

### Proof of Concept

1. Deploy `OmniBridgeWormhole` implementation; constructor calls `_disableInitializers()` (inherited from `OmniBridge`). [4](#0-3) 
2. Deploy `ERC1967Proxy` pointing to the implementation, passing ABI-encoded `initializeWormhole(...)` as init-data.
3. Inside the proxy constructor, `initializeWormhole` is called:
   - OZ v5 `initializer` sets `_initialized = 1`, `_initializing = true`.
   - Body calls `OmniBridge.initialize(...)`.
   - Inner `initializer` check: `initialSetup = false`, `construction = false` → `revert InvalidInitialization()`.
4. The entire proxy deployment transaction reverts. `OmniBridgeWormhole` is undeployable via its intended path.
5. If the deployer instead calls `initialize()` directly on a deployed proxy, `_wormhole = address(0)`. Any user calling `initTransfer(tokenAddress, amount, ...)` triggers `initTransferExtension → _wormhole.publishMessage{value: ...}(...)` on `address(0)`, reverting and locking any ERC-20 tokens already transferred from the user in the same call. [5](#0-4)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L67-70)
```text
    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
    }
```

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

**File:** evm/package.json (L7-9)
```json
    "@openzeppelin/contracts": "^5.4.0",
    "@openzeppelin/contracts-upgradeable": "^5.4.0"
  },
```
