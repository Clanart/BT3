### Title
`OmniBridge.initialize` Is `public` on `OmniBridgeWormhole`, Enabling Front-Running of Proxy Initialization to Seize Admin and Mint Arbitrary Tokens — (File: `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol`)

### Summary

`OmniBridgeWormhole` uses a custom initializer (`initializeWormhole`) as its intended entry point, but the parent `OmniBridge.initialize` is declared `public initializer`. On a deployed `OmniBridgeWormhole` proxy, both functions are externally callable before initialization. If the proxy is deployed without atomic initialization data (empty `initData`), an attacker can front-run the deployer by calling `initialize` directly, seizing `DEFAULT_ADMIN_ROLE`, setting `nearBridgeDerivedAddress` to their own address, and subsequently minting arbitrary bridge tokens via `finTransfer`.

### Finding Description

`OmniBridgeWormhole` overrides `OmniBridge` and introduces `initializeWormhole` as the intended initializer:

```solidity
// OmniBridgeWormhole.sol:32-46
function initializeWormhole(
    address tokenImplementationAddress,
    address nearBridgeDerivedAddress,
    uint8 omniBridgeChainId,
    address wormholeAddress,
    uint8 consistencyLevel
) external initializer {
    initialize(tokenImplementationAddress, nearBridgeDerivedAddress, omniBridgeChainId);
    _wormhole = IWormhole(wormholeAddress);
    _consistencyLevel = consistencyLevel;
}
```

However, the parent's initializer is declared `public`:

```solidity
// OmniBridge.sol:72-86
function initialize(
    address tokenImplementationAddress_,
    address nearBridgeDerivedAddress_,
    uint8 omniBridgeChainId_
) public initializer {
    ...
    _grantRole(DEFAULT_ADMIN_ROLE, _msgSender());
    _grantRole(PAUSABLE_ADMIN_ROLE, _msgSender());
}
```

Because `initialize` is `public`, it is callable directly on any `OmniBridgeWormhole` proxy. The OZ `initializer` modifier only prevents a second call after the first succeeds — it does not restrict *which* of the two initializers is called first. If the proxy is deployed with empty `initData` (a two-step deployment pattern), the window between proxy creation and the deployer's `initializeWormhole` call is open to front-running.

An attacker who calls `initialize` first:
- Becomes `DEFAULT_ADMIN_ROLE` and `PAUSABLE_ADMIN_ROLE`
- Sets `nearBridgeDerivedAddress` to their own EOA
- Sets `tokenImplementationAddress` to a malicious contract
- Leaves `_wormhole = address(0)` and `_consistencyLevel = 0`

After this, `initializeWormhole` is permanently blocked (OZ `initializer` reverts on a second call). The attacker now controls the bridge.

Additionally, even without front-running, `OmniBridge.initialize` being `public` means a deployer can accidentally call it instead of `initializeWormhole`, leaving `_wormhole` unset and bricking all Wormhole-dependent operations (`deployTokenExtension`, `finTransferExtension`, `initTransferExtension`, `logMetadataExtension`) which all call `_wormhole.publishMessage{value: msg.value}(...)` on `address(0)`.

No deployment script for `OmniBridgeWormhole` is present in the visible codebase (the hardhat config only shows `deploy-hl-bridge-token-proxy` and `deploy-e-near-proxy`), making it impossible to verify that the deployment is always atomic.

### Impact Explanation

**Critical — unauthorized mint of bridged assets.**

After seizing `DEFAULT_ADMIN_ROLE` and setting `nearBridgeDerivedAddress` to their own address, the attacker can call `finTransfer` with a self-signed ECDSA signature. The signature check in `OmniBridge.finTransfer` is:

```solidity
// OmniBridge.sol:311
if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
    revert InvalidSignature();
}
```

Since `nearBridgeDerivedAddress` is now the attacker's address, any transfer payload they sign passes verification. They can mint arbitrary amounts of any registered bridge token to any recipient, or drain native ETH held by the bridge.

### Likelihood Explanation

**Low-to-Medium.** The attack requires the proxy to be deployed without atomic initialization (empty `initData` in the `ERC1967Proxy` constructor). This is a realistic deployment pattern when operators separate proxy creation from initialization for gas or operational reasons. The absence of a visible deployment script for `OmniBridgeWormhole` increases the risk that the deployment procedure is not standardized or audited. The attack itself is trivial to execute once the window is open (a single public function call).

### Recommendation

1. Change `OmniBridge.initialize` visibility from `public` to `internal` so it cannot be called directly on any proxy — only via `initializeWormhole` or other child initializers.
2. Alternatively, add an `onlyInitializing` guard (OZ's modifier for internal initializer chains) instead of `initializer` on `OmniBridge.initialize`.
3. Provide a deployment script for `OmniBridgeWormhole` that deploys the proxy with `initData` encoding `initializeWormhole(...)` in the `ERC1967Proxy` constructor, ensuring atomic initialization with no front-running window.

### Proof of Concept

```
1. Deployer deploys OmniBridgeWormhole implementation (constructor calls _disableInitializers() — OK).
2. Deployer deploys ERC1967Proxy(implementation, "") with empty initData (two-step pattern).
3. Attacker observes the proxy deployment in the mempool.
4. Attacker calls proxy.initialize(
       maliciousTokenImpl,   // tokenImplementationAddress_
       attacker,             // nearBridgeDerivedAddress_ (attacker's EOA)
       chainId               // omniBridgeChainId_
   ) before the deployer's initializeWormhole tx lands.
5. OZ initializer modifier: _initialized == 0, so it proceeds.
   _grantRole(DEFAULT_ADMIN_ROLE, attacker)
   _grantRole(PAUSABLE_ADMIN_ROLE, attacker)
   nearBridgeDerivedAddress = attacker
   _wormhole = address(0)  (never set)
6. Deployer's initializeWormhole tx reverts: "Initializable: contract is already initialized".
7. Attacker crafts a TransferMessagePayload with recipient=attacker, amount=MAX_UINT128,
   tokenAddress=any_registered_bridge_token, signs it with their own private key.
8. Attacker calls proxy.finTransfer(attackerSignature, payload).
   ECDSA.recover(hash, sig) == attacker == nearBridgeDerivedAddress → passes.
   IBridgeToken(tokenAddress).mint(attacker, MAX_UINT128) executes.
9. Attacker holds unbacked minted bridge tokens; bridge collateralization is broken.
```

**Root cause references:** [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L309-312)
```text
        bytes32 hashed = keccak256(borshEncoded);

        if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
            revert InvalidSignature();
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
