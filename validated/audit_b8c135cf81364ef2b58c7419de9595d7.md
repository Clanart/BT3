### Title
`OmniBridge.initialize` Uses `initializer` Instead of `onlyInitializing`, Permanently Bricking `OmniBridgeWormhole` Proxy Deployment — (`evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol`)

---

### Summary

`OmniBridge.initialize` is decorated with the `initializer` modifier. `OmniBridgeWormhole.initializeWormhole` is also decorated with `initializer` and calls `OmniBridge.initialize` internally. Under OpenZeppelin Upgradeable v5, a nested `initializer`-inside-`initializer` call outside a constructor context unconditionally reverts with `InvalidInitialization()`. As a result, `OmniBridgeWormhole` can never be successfully initialized through a proxy, making the contract permanently non-functional and any funds sent to such a proxy permanently locked.

---

### Finding Description

`OmniBridge.initialize` is declared `public initializer`: [1](#0-0) 

`OmniBridgeWormhole.initializeWormhole` is also declared `external initializer` and calls `initialize(...)` directly: [2](#0-1) 

Under OpenZeppelin Upgradeable v5, the `initializer` modifier logic is:

```solidity
bool initialSetup = initialized == 0 && isTopLevelCall;
bool construction = initialized == 1 && address(this).code.length == 0;
if (!initialSetup && !construction) {
    revert InvalidInitialization();
}
$._initialized = 1;
$._initializing = true;
```

When `initializeWormhole` is called on a fresh proxy:

1. **Outer call** (`initializeWormhole`): `_initialized == 0`, `isTopLevelCall == true` → `initialSetup = true` → passes. Sets `_initialized = 1`, `_initializing = true`.
2. **Inner call** (`initialize`): `_initialized == 1`, `isTopLevelCall == false` (because `_initializing == true`).
   - `initialSetup = (1 == 0 && false) = false`
   - `construction = (1 == 1 && address(this).code.length == 0)` → proxy has code → `false`
   - Both conditions false → **`revert InvalidInitialization()`**

There is no alternative initialization path. Calling `OmniBridge.initialize` directly first also fails because it sets `_initialized = 1`, after which `initializeWormhole` itself reverts (`initialSetup = false`, `construction = false`).

`SelectivePausableUpgradable` correctly uses `onlyInitializing` for its init functions, confirming the pattern is known and applied elsewhere in the codebase: [3](#0-2) 

---

### Impact Explanation

`OmniBridgeWormhole` is permanently undeployable as an upgradeable proxy. Any deployment attempt via `ERC1967Proxy` + `initializeWormhole` will revert. If a proxy is deployed with no initialization (e.g., via a factory that separates deployment from initialization), the contract remains in an uninitialized state with no owner/admin, and any ETH or ERC-20 tokens sent to it are permanently locked with no recovery path. This matches **Permanent freezing / irrecoverable lock of user or protocol funds in bridge flows**.

---

### Likelihood Explanation

This is triggered on every proxy deployment of `OmniBridgeWormhole` — a routine, unprivileged deployment step. No special attacker capability is required; the revert is deterministic and 100% reproducible. Any deployer or protocol operator attempting to stand up the Wormhole bridge variant will encounter this immediately.

---

### Recommendation

Change `OmniBridge.initialize` to use `onlyInitializing` and mark it `internal`, following the OpenZeppelin pattern for base upgradeable contracts (analogous to `__ERC20_init`, `__AccessControl_init`, etc.):

```solidity
// OmniBridge.sol
function __OmniBridge_init(
    address tokenImplementationAddress_,
    address nearBridgeDerivedAddress_,
    uint8 omniBridgeChainId_
) internal onlyInitializing {
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

Then in `OmniBridgeWormhole`:

```solidity
function initializeWormhole(...) external initializer {
    __OmniBridge_init(tokenImplementationAddress, nearBridgeDerivedAddress, omniBridgeChainId);
    _wormhole = IWormhole(wormholeAddress);
    _consistencyLevel = consistencyLevel;
}
```

If `OmniBridge` itself also needs to be deployable standalone, add a separate `initialize` with `initializer` that delegates to `__OmniBridge_init`.

---

### Proof of Concept

```solidity
// Deploy implementation
OmniBridgeWormhole impl = new OmniBridgeWormhole();

// Deploy proxy — initializeWormhole is the init calldata
ERC1967Proxy proxy = new ERC1967Proxy(
    address(impl),
    abi.encodeWithSelector(
        OmniBridgeWormhole.initializeWormhole.selector,
        tokenImpl, nearDerived, chainId, wormholeAddr, consistencyLevel
    )
);
// ^^^ ALWAYS reverts with InvalidInitialization()
// because initializeWormhole (initializer) calls initialize (initializer)
// and the nested initializer check fails outside constructor context.
```

### Citations

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

**File:** evm/src/omni-bridge/contracts/SelectivePausableUpgradable.sol (L47-54)
```text
    function __Pausable_init() internal onlyInitializing {
        __Pausable_init_unchained();
    }

    function __Pausable_init_unchained() internal onlyInitializing {
        SelectivePausableStorage storage $ = _getSelectivePausableStorage();
        $._pausedFlags = 0;
    }
```
