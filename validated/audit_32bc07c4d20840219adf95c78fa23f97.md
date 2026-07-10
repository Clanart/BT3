### Title
`OmniBridgeWormhole.initializeWormhole()` Calls `OmniBridge.initialize()` With Conflicting `initializer` Modifiers, Permanently Bricking Proxy Initialization - (File: `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol`)

---

### Summary

`OmniBridgeWormhole.initializeWormhole()` is decorated with the `initializer` modifier and internally calls `OmniBridge.initialize()`, which is also decorated with the `initializer` modifier. When the proxy is initialized via a standalone transaction (i.e., not inside the proxy's own constructor), the nested `initializer` call unconditionally reverts, making the `OmniBridgeWormhole` proxy permanently undeployable through the standard two-step pattern.

---

### Finding Description

`OmniBridgeWormhole` inherits from `OmniBridge`. Its entry-point initializer is:

```solidity
// OmniBridgeWormhole.sol L32-46
function initializeWormhole(...) external initializer {
    initialize(tokenImplementationAddress, nearBridgeDerivedAddress, omniBridgeChainId);
    _wormhole = IWormhole(wormholeAddress);
    _consistencyLevel = consistencyLevel;
}
```

It calls the parent's initializer:

```solidity
// OmniBridge.sol L72-86
function initialize(...) public initializer {
    ...
}
```

Both functions carry the `initializer` modifier from OpenZeppelin's `Initializable`. When `initializeWormhole` is called as a regular transaction on a deployed proxy, the modifier's execution proceeds as follows (OZ v5 logic):

1. **`initializeWormhole`'s `initializer`**: `_initialized == 0` and `isTopLevelCall == true` → `initialSetup == true` → passes. Sets `_initialized = 1`, `_initializing = true`.
2. **`initialize()`'s `initializer`**: Now `_initialized == 1` and `isTopLevelCall == false` (because `_initializing` is already `true`).
   - `initialSetup = (initialized == 0 && isTopLevelCall)` → `(false && false)` = **false**
   - `construction = (initialized == 1 && address(this).code.length == 0)` → `(true && false)` = **false** (the proxy has code at this point)
   - Neither condition is satisfied → **reverts with `InvalidInitialization()`**

The only scenario where this does not revert is if `initializeWormhole` is invoked during the proxy's own constructor (e.g., by passing ABI-encoded calldata to `ERC1967Proxy`'s constructor), because in that narrow window `address(this).code.length == 0`. Any deployment that separates proxy creation from initialization — a common and legitimate pattern — permanently bricks the contract.

`OmniBridge.initialize()` is a `public initializer` function intended to serve as the standalone initializer for `OmniBridge` and as a sub-initializer called by child contracts. For the latter role it must use `onlyInitializing`, not `initializer`, exactly as OpenZeppelin's own `EIP712Upgradeable.__EIP712_init()` and all other OZ base-contract initializers do.

---

### Impact Explanation

If `OmniBridgeWormhole` is deployed as a UUPS proxy without passing initialization calldata to the proxy constructor, `initializeWormhole` will always revert. The proxy is left permanently uninitialized: `nearBridgeDerivedAddress` is `address(0)`, all roles are unset, and the contract is effectively a black hole. Any native ETH or ERC-20 tokens sent to the uninitialized proxy (e.g., by users who observe the proxy address before the deployer realizes the failure) are permanently locked with no recovery path, because `_authorizeUpgrade` requires `DEFAULT_ADMIN_ROLE` (never granted) and `finTransfer`/`initTransfer` are gated by `whenNotPaused` and signature checks against an unset `nearBridgeDerivedAddress`.

This matches: **Permanent freezing / irrecoverable lock of user or protocol funds in bridge flows.**

---

### Likelihood Explanation

The two-step deploy-then-initialize pattern (deploy proxy, then call initializer in a separate transaction) is standard practice and is the default behavior of many deployment scripts and frameworks that do not use `hardhat-upgrades deployProxy()`. Any operator who deploys the proxy first and calls `initializeWormhole` in a follow-up transaction will hit this revert every time, with no workaround short of redeploying. The root cause is a straightforward modifier misuse that is invisible at compile time and only surfaces at runtime.

---

### Recommendation

Change `OmniBridge.initialize()` to use `onlyInitializing` and make it `internal`, following the OpenZeppelin pattern for base-contract initializers. Expose a separate `public initializer` wrapper for standalone `OmniBridge` deployments:

```solidity
// OmniBridge.sol
function initialize(
    address tokenImplementationAddress_,
    address nearBridgeDerivedAddress_,
    uint8 omniBridgeChainId_
) public initializer {
    __OmniBridge_init(tokenImplementationAddress_, nearBridgeDerivedAddress_, omniBridgeChainId_);
}

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

---

### Proof of Concept

1. Deploy `OmniBridgeWormhole` implementation with `_disableInitializers()` in constructor (inherited from `OmniBridge`). [1](#0-0) 
2. Deploy `ERC1967Proxy(implementation, "")` — no initialization data, proxy created with empty calldata.
3. Call `proxy.initializeWormhole(...)` in a separate transaction.
4. `initializeWormhole`'s `initializer` modifier fires: sets `_initialized = 1`, `_initializing = true`. [2](#0-1) 
5. `initialize()` is called; its own `initializer` modifier evaluates `initialSetup = false` (initialized ≠ 0) and `construction = false` (proxy code exists) → **reverts**. [3](#0-2) 
6. The proxy is permanently stuck in an uninitialized state; all bridge operations revert; any funds sent to it are irrecoverable.

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L67-70)
```text
    /// @custom:oz-upgrades-unsafe-allow constructor
    constructor() {
        _disableInitializers();
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
