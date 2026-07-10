### Title
`OmniBridgeWormhole.initializeWormhole()` Always Reverts Due to `initializer` vs `onlyInitializing` Mismatch, Leaving Proxy Permanently Uninitialized and Claimable — (`evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol`)

---

### Summary

`OmniBridgeWormhole.initializeWormhole()` internally calls `OmniBridge.initialize()`, but both functions carry OpenZeppelin's `initializer` modifier. The `initializer` modifier sets `_initialized = 1` and `_initializing = true` before executing the function body. When the inner `initialize()` call is reached, the modifier re-checks and finds `_initialized == 1` with `_initializing == true`, which fails the `initialSetup` guard and reverts with `InvalidInitialization()`. Because the revert rolls back all state, `_initialized` returns to `0`. The proxy is permanently stuck uninitialized, and any unprivileged caller can invoke `initialize()` directly to seize `DEFAULT_ADMIN_ROLE`.

---

### Finding Description

`OmniBridge.initialize()` is declared `public initializer`: [1](#0-0) 

`OmniBridgeWormhole.initializeWormhole()` is also declared `external initializer` and calls `initialize()` from its body: [2](#0-1) 

OpenZeppelin's `initializer` modifier (both v4 and v5) sets `_initialized = 1` and `_initializing = true` at the start of `initializeWormhole()`. When the body then calls `initialize()`, the modifier re-enters and evaluates:

- `isTopLevelCall = !_initializing = false`
- `initialSetup = (_initialized == 0 && isTopLevelCall) = false`
- `construction = (_initialized == 1 && address(this).code.length == 0) = false` (proxy has code)

Both guards are false → `revert InvalidInitialization()`.

The revert rolls back all state, so `_initialized` returns to `0`. Every call to `initializeWormhole()` will always revert. The proxy is left permanently uninitialized.

The correct OpenZeppelin pattern for a base-contract initializer that is called from a derived initializer is to use `onlyInitializing` (not `initializer`) on the base function. `onlyInitializing` only checks `_initializing == true` and does not re-set `_initialized`, so it is safe to call from within an outer `initializer` context.

The `OmniBridge` constructor correctly calls `_disableInitializers()` to protect the implementation: [3](#0-2) 

But this protection applies only to the implementation address. The proxy's storage starts with `_initialized = 0` and is never advanced because `initializeWormhole()` always reverts.

---

### Impact Explanation

An attacker who observes the failed `initializeWormhole()` transaction (or front-runs it) can immediately call `initialize(attackerAddress, attackerAddress, 0)` on the proxy. This succeeds because `_initialized == 0`. The attacker receives:

- `DEFAULT_ADMIN_ROLE` — full administrative control of the bridge
- `PAUSABLE_ADMIN_ROLE`

With `DEFAULT_ADMIN_ROLE` the attacker can:

1. Call `setNearBridgeDerivedAddress(attackerEOA)` to replace the MPC-derived signer address with their own key. [4](#0-3) 
2. Forge valid ECDSA signatures accepted by `finTransfer()` and `deployToken()`, minting arbitrary bridge tokens or releasing locked native ETH/ERC-20 to any recipient. [5](#0-4) 
3. Upgrade the proxy implementation to a malicious contract via `_authorizeUpgrade`. [6](#0-5) 

Impact: **Critical — direct unauthorized mint of bridged assets and/or theft of all locked funds.**

---

### Likelihood Explanation

The failure mode is deterministic and 100% reproducible: every call to `initializeWormhole()` reverts. Any deployment attempt will fail, leaving the proxy open. The attack requires no special privilege, no leaked key, and no chain-level assumption — only the ability to call a public function on an uninitialized proxy. The window is open from the moment the proxy is deployed until it is abandoned or redeployed.

---

### Recommendation

Change `OmniBridge.initialize()` from `initializer` to `onlyInitializing` so it can be safely called from within `initializeWormhole()`:

```solidity
// OmniBridge.sol
function initialize(
    address tokenImplementationAddress_,
    address nearBridgeDerivedAddress_,
    uint8 omniBridgeChainId_
) public onlyInitializing {   // ← was: initializer
    ...
}
```

This follows the standard OpenZeppelin pattern for upgradeable base contracts whose initializer is invoked by a derived contract's own `initializer`. Alternatively, inline the base initialization logic directly into `initializeWormhole()` and remove the separate `initialize()` entry point from `OmniBridgeWormhole`.

---

### Proof of Concept

```
1. Deploy OmniBridgeWormhole implementation (constructor calls _disableInitializers()).
2. Deploy ERC1967Proxy pointing to the implementation with empty init data.
3. Call proxy.initializeWormhole(impl, mpc, chainId, wormhole, level):
   - OZ initializer sets _initialized=1, _initializing=true
   - Body calls initialize(impl, mpc, chainId)
   - Inner initializer: initialSetup=(1==0&&false)=false, construction=false → revert InvalidInitialization()
   - Full transaction reverts; _initialized=0 on proxy
4. Attacker calls proxy.initialize(attacker, attacker, 0):
   - initialSetup=(0==0&&true)=true → passes
   - _initialized=1, _initializing=true→false
   - _grantRole(DEFAULT_ADMIN_ROLE, attacker)
   - _grantRole(PAUSABLE_ADMIN_ROLE, attacker)
5. Attacker calls proxy.setNearBridgeDerivedAddress(attackerEOA).
6. Attacker signs a TransferMessagePayload with attackerEOA private key.
7. Attacker calls proxy.finTransfer(forgedSig, payload) → mints tokens or releases ETH to attacker.
```

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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L311-313)
```text
        if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
            revert InvalidSignature();
        }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L568-572)
```text
    function setNearBridgeDerivedAddress(
        address nearBridgeDerivedAddress_
    ) external onlyRole(DEFAULT_ADMIN_ROLE) {
        nearBridgeDerivedAddress = nearBridgeDerivedAddress_;
    }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L594-596)
```text
    function _authorizeUpgrade(
        address newImplementation
    ) internal override onlyRole(DEFAULT_ADMIN_ROLE) {}
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
