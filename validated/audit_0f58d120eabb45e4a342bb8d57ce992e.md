### Title
Frontrunnable Initializer Grants Attacker Full Admin Control Over OmniBridge — (`evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge.initialize` is declared `public initializer` with no access control. If the UUPS proxy is deployed without atomically encoding initialization data in the proxy constructor, any attacker who observes the pending proxy deployment can race to call `initialize` first, seizing `DEFAULT_ADMIN_ROLE` and `PAUSABLE_ADMIN_ROLE`. With those roles the attacker can immediately overwrite `nearBridgeDerivedAddress` with their own key, after which every signature-gated path (`deployToken`, `finTransfer`) accepts attacker-forged signatures.

---

### Finding Description

`OmniBridge.initialize` is the sole initialization entry-point for the UUPS proxy:

```solidity
function initialize(
    address tokenImplementationAddress_,
    address nearBridgeDerivedAddress_,
    uint8 omniBridgeChainId_
) public initializer {          // ← public, no caller restriction
    ...
    _grantRole(DEFAULT_ADMIN_ROLE, _msgSender());
    _grantRole(PAUSABLE_ADMIN_ROLE, _msgSender());
}
``` [1](#0-0) 

The implementation contract correctly calls `_disableInitializers()` in its constructor, protecting the bare implementation. However, the proxy itself has no such protection. If the proxy is deployed in one transaction and `initialize` is called in a subsequent transaction (a common deployment pattern), the window between those two transactions is exploitable.

`OmniBridgeWormhole` compounds this: its own `initializeWormhole` is `external initializer` and internally calls `initialize`. If an attacker calls `initialize` directly before `initializeWormhole` is ever called, the OpenZeppelin initializer version counter is incremented, causing `initializeWormhole` to revert permanently — the proxy is bricked for the legitimate deployer. [2](#0-1) 

Once the attacker holds `DEFAULT_ADMIN_ROLE`, they call `setNearBridgeDerivedAddress` to substitute their own Ethereum key:

```solidity
function setNearBridgeDerivedAddress(
    address nearBridgeDerivedAddress_
) external onlyRole(DEFAULT_ADMIN_ROLE) {
    nearBridgeDerivedAddress = nearBridgeDerivedAddress_;
}
``` [3](#0-2) 

`deployToken` and `finTransfer` both verify signatures against `nearBridgeDerivedAddress`:

```solidity
if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
    revert InvalidSignature();
}
``` [4](#0-3) 

With their own key installed as `nearBridgeDerivedAddress`, the attacker can forge valid signatures for any `MetadataPayload` or `TransferMessagePayload`, enabling arbitrary token deployment and arbitrary `finTransfer` execution (minting bridge tokens to any recipient).

---

### Impact Explanation

An attacker who wins the race on `initialize`:

1. Becomes `DEFAULT_ADMIN_ROLE` — controls `nearBridgeDerivedAddress`, token upgrades, pause state, and UUPS upgrade authority.
2. Replaces `nearBridgeDerivedAddress` with their own key in the same block.
3. Forges `finTransfer` signatures to mint bridge tokens to themselves without any corresponding locked collateral on NEAR — **unauthorized mint of bridged assets (Critical)**.
4. Forges `deployToken` signatures to register malicious token contracts — **unauthorized token deployment (High)**.
5. Can upgrade the proxy implementation to an arbitrary contract — **permanent protocol takeover**.

Even if the legitimate deployer notices and re-deploys, the `OmniBridgeWormhole` variant is permanently bricked for that proxy address because `initializeWormhole` will revert once `initialize` has been called.

---

### Likelihood Explanation

The attack requires only that the proxy deployment transaction and the `initialize` call are in separate transactions — a common pattern when using deployment scripts that separate `deploy` and `initialize` steps. The attacker needs only to monitor the mempool for a proxy deployment to a known implementation address and submit `initialize` with higher gas. No special privileges, leaked keys, or colluding parties are required. The attack is fully executable by any unprivileged user.

---

### Recommendation

1. **Atomic initialization**: Always deploy the proxy with initialization calldata encoded in the `ERC1967Proxy` constructor so deployment and initialization are a single atomic transaction.
2. **Restrict `initialize` visibility**: Change `public initializer` to `external initializer` (prevents internal re-entry from `initializeWormhole` calling it without the `initializer` guard being re-entrant-safe) and add a deployer check, e.g., store the deployer address in the implementation constructor and assert `msg.sender == deployer` inside `initialize`.
3. **Use `_disableInitializers` on the proxy too**: Consider a factory pattern that deploys and initializes atomically, or use OpenZeppelin's `Clones` with immutable args.

---

### Proof of Concept

```
1. Deployer broadcasts: deployProxy(OmniBridgeImpl, "")   // no init data
2. Attacker sees pending tx, broadcasts with higher gas:
       OmniBridgeProxy.initialize(
           tokenImpl,
           attackerEthAddress,   // attacker's own key
           chainId
       )
3. Attacker's tx mines first → attacker is DEFAULT_ADMIN_ROLE
4. Deployer's initialize() reverts (already initialized)
5. Attacker calls: proxy.setNearBridgeDerivedAddress(attackerEthAddress)
6. Attacker signs TransferMessagePayload with attackerKey:
       proxy.finTransfer(attackerSignature, payload{recipient: attacker, amount: X})
   → BridgeToken.mint(attacker, X) executes with no NEAR collateral
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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L151-153)
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
