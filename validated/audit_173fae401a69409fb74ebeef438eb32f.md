### Title
`OmniBridgeWormhole` Cannot Be Initialized Due to Nested `initializer` Modifier Conflict — (File: `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol`)

---

### Summary

`OmniBridgeWormhole.initializeWormhole` carries the `initializer` modifier and internally calls `OmniBridge.initialize`, which also carries the `initializer` modifier. OpenZeppelin's `initializer` modifier does not permit nested invocations from within another active `initializer` context. The inner call always reverts, making `OmniBridgeWormhole` permanently un-initializable and therefore permanently non-functional as a deployed proxy.

---

### Finding Description

`OmniBridgeWormhole` extends `OmniBridge` and provides its own entry-point initializer:

```solidity
// OmniBridgeWormhole.sol lines 32-46
function initializeWormhole(
    address tokenImplementationAddress,
    address nearBridgeDerivedAddress,
    uint8 omniBridgeChainId,
    address wormholeAddress,
    uint8 consistencyLevel
) external initializer {          // <-- sets _initializing = true, _initialized = 1
    initialize(                   // <-- calls OmniBridge.initialize which also has `initializer`
        tokenImplementationAddress,
        nearBridgeDerivedAddress,
        omniBridgeChainId
    );
    _wormhole = IWormhole(wormholeAddress);
    _consistencyLevel = consistencyLevel;
}
``` [1](#0-0) 

The parent function it calls:

```solidity
// OmniBridge.sol lines 72-86
function initialize(
    address tokenImplementationAddress_,
    address nearBridgeDerivedAddress_,
    uint8 omniBridgeChainId_
) public initializer {            // <-- also has `initializer`
    ...
}
``` [2](#0-1) 

OpenZeppelin's `initializer` modifier (both v4 and v5) tracks two flags in proxy storage: `_initializing` and `_initialized`. When `initializeWormhole` is entered as the top-level call:

1. `_initialized` is set to `1` and `_initializing` is set to `true`.
2. `initialize(...)` is then called. Inside that call, `isTopLevelCall = false` (because `_initializing` is already `true`).
3. The guard evaluates to `(false && _initialized < 1) || (!isContract && _initialized == 1)` → `false || false` → **reverts** with `"Initializable: contract is already initialized"` (v4) or `InvalidInitialization()` (v5).

The `OmniBridgeWormhole` proxy can therefore never complete initialization. The `_wormhole` address and `_consistencyLevel` are never set, and all role grants from `OmniBridge.initialize` never execute.

---

### Impact Explanation

**Critical — Permanent freezing of bridge funds and complete loss of Wormhole bridge functionality.**

- Any `ERC1967Proxy` deployed pointing to `OmniBridgeWormhole` will be stuck in an uninitialized state: no admin roles, no `nearBridgeDerivedAddress`, no `_wormhole`.
- The `receive()` function inherited from `OmniBridge` accepts ETH unconditionally even on an uninitialized proxy. ETH sent to the proxy before the initialization failure is discovered is permanently locked with no recovery path.
- `initTransfer`, `finTransfer`, and `deployToken` are all gated by `whenNotPaused` and role checks that depend on successful initialization; they will all revert or behave incorrectly.
- The entire Wormhole cross-chain bridge path is rendered permanently inoperable. [3](#0-2) 

---

### Likelihood Explanation

**High.** The failure is deterministic and triggered on the very first deployment step. Any operator who deploys an `ERC1967Proxy` for `OmniBridgeWormhole` and calls `initializeWormhole` will hit the revert unconditionally. There are no preconditions, no attacker involvement, and no race conditions required.

---

### Recommendation

Replace the `initializer` modifier on `OmniBridge.initialize` with `onlyInitializing`, which is specifically designed for parent-contract initializers that are called from within a child's `initializer`:

```solidity
// OmniBridge.sol
function initialize(
    address tokenImplementationAddress_,
    address nearBridgeDerivedAddress_,
    uint8 omniBridgeChainId_
) public onlyInitializing {   // changed from `initializer`
    ...
}
```

This allows `OmniBridge` to be initialized directly (when used standalone) via a thin wrapper with `initializer`, and also to be called from `OmniBridgeWormhole.initializeWormhole` without triggering the nested-initializer revert.

---

### Proof of Concept

```solidity
// Pseudocode demonstrating the revert path
OmniBridgeWormhole impl = new OmniBridgeWormhole();
ERC1967Proxy proxy = new ERC1967Proxy(address(impl), "");
OmniBridgeWormhole bridge = OmniBridgeWormhole(payable(address(proxy)));

// This call ALWAYS reverts:
bridge.initializeWormhole(
    tokenImpl,
    nearDerived,
    chainId,
    wormholeAddr,
    consistencyLevel
);
// Revert reason: "Initializable: contract is already initialized"
// Root cause: initializeWormhole (initializer) sets _initialized=1, _initializing=true,
//             then calls OmniBridge.initialize (also initializer),
//             which sees _initializing=true → isTopLevelCall=false,
//             condition (false && 1<1)||(false) = false → REVERT
``` [1](#0-0) [2](#0-1)

### Citations

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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L72-86)
```text
    function initialize(
        address tokenImplementationAddress_,
        address nearBridgeDerivedAddress_,
        uint8 omniBridgeChainId_
    ) public initializer {
        tokenImplementationAddress = tokenImplementationAddress_;
        nearBridgeDerivedAddress = nearBridgeDerivedAddress_;
        omniBridgeChainId = omniBridgeChainId_;

        __UUPSUpgradeable_init();
        __AccessControl_init();
        __Pausable_init_unchained();
        _grantRole(DEFAULT_ADMIN_ROLE, _msgSender());
        _grantRole(PAUSABLE_ADMIN_ROLE, _msgSender());
    }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L574-574)
```text
    receive() external payable {}
```
