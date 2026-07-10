### Title
Child contract `initializeWormhole` and parent `initialize` both use `initializer` modifier, making `OmniBridgeWormhole` permanently uninitializable - (File: evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol)

### Summary
`OmniBridgeWormhole.initializeWormhole()` uses the `initializer` modifier and then calls `OmniBridge.initialize()`, which also uses the `initializer` modifier. Because OpenZeppelin's `initializer` sets `_initialized = 1` and `_initializing = true` on the first call, the nested call to the parent's `initializer`-guarded function always reverts. `OmniBridgeWormhole` can never be successfully initialized.

### Finding Description
`OmniBridgeWormhole` inherits from `OmniBridge`. Its entry-point initialization function is: [1](#0-0) 

It calls the parent's initialization function: [2](#0-1) 

Both functions carry the `initializer` modifier. When `initializeWormhole` is entered, OpenZeppelin's `Initializable` sets `_initialized = 1` and `_initializing = true`. The subsequent call to `OmniBridge.initialize()` then evaluates the `initializer` guard:

- `initialSetup = (_initialized == 0 && isTopLevelCall)` → `(false && false)` = **false**
- `construction = (_initialized == 1 && address(this).code.length == 0)` → `(true && false)` = **false** (deployed contract)

Both branches are false, so the modifier reverts with `InvalidInitialization()`. Every call to `initializeWormhole` will revert at the nested `initialize()` call, making the contract permanently uninitializable.

The root cause is identical to the reported Symmio pattern: the parent contract's init function uses `initializer` instead of `onlyInitializing`, which is reserved for leaf (final) initializers only. [3](#0-2) 

### Impact Explanation
`OmniBridgeWormhole` is the Wormhole-relay variant of the OmniBridge. It cannot be initialized, so it cannot be deployed in a functional state. Any proxy deployed pointing to this implementation will be permanently stuck in an uninitialized state: all guarded bridge functions (`finTransfer`, `initTransfer`, `deployToken`) are either inaccessible or operate without the required role/address configuration. ETH sent to the proxy via the inherited `receive()` fallback before or during failed initialization attempts would be permanently locked with no recovery path. This constitutes permanent freezing of bridge functionality and any funds that reach the contract. [4](#0-3) 

### Likelihood Explanation
The failure is deterministic and triggered on the very first (and only) initialization call. Any deployer who attempts to deploy `OmniBridgeWormhole` via a standard UUPS proxy and calls `initializeWormhole` will immediately encounter the revert. There is no workaround without a code change. Likelihood is certain for any deployment attempt.

### Recommendation
Follow OpenZeppelin's convention: the parent contract's init function should use `onlyInitializing` so it can be called from within a child's `initializer`-guarded function. Rename `OmniBridge.initialize()` to an internal `__OmniBridge_init()` with `onlyInitializing`, expose a public `initialize()` with `initializer` for standalone `OmniBridge` deployments, and have `OmniBridgeWormhole.initializeWormhole()` call `__OmniBridge_init()` directly:

```diff
// OmniBridge.sol
-function initialize(
+function initialize(
     address tokenImplementationAddress_,
     address nearBridgeDerivedAddress_,
     uint8 omniBridgeChainId_
-) public initializer {
+) public initializer {
+    __OmniBridge_init(tokenImplementationAddress_, nearBridgeDerivedAddress_, omniBridgeChainId_);
+}
+
+function __OmniBridge_init(
+    address tokenImplementationAddress_,
+    address nearBridgeDerivedAddress_,
+    uint8 omniBridgeChainId_
+) internal onlyInitializing {
     tokenImplementationAddress = tokenImplementationAddress_;
     ...
 }

// OmniBridgeWormhole.sol
 function initializeWormhole(...) external initializer {
-    initialize(tokenImplementationAddress, nearBridgeDerivedAddress, omniBridgeChainId);
+    __OmniBridge_init(tokenImplementationAddress, nearBridgeDerivedAddress, omniBridgeChainId);
     _wormhole = IWormhole(wormholeAddress);
     _consistencyLevel = consistencyLevel;
 }
```

### Proof of Concept

1. Deploy `OmniBridgeWormhole` implementation with `_disableInitializers()` in constructor (already present).
2. Deploy an `ERC1967Proxy` pointing to the implementation.
3. Call `initializeWormhole(tokenImpl, nearAddr, chainId, wormholeAddr, consistencyLevel)` on the proxy.
4. Execution enters `initializeWormhole`; OZ `initializer` sets `_initialized = 1`, `_initializing = true`.
5. `initialize(tokenImpl, nearAddr, chainId)` is called; OZ `initializer` evaluates: `initialSetup = (1==0 && false) = false`; `construction = (1==1 && codeLen==0) = false`; reverts with `InvalidInitialization()`.
6. The proxy is permanently stuck uninitialized; no bridge operation is possible. [1](#0-0) [2](#0-1)

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
