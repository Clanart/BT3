### Title
`OmniBridge.initialize()` Is `public` and Callable Directly on `OmniBridgeWormhole` Proxy Before Atomic Initialization, Enabling Attacker to Seize Admin Control and Forge Transfer Signatures — (`evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol`, `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridgeWormhole` exposes two competing initialization entry points: the inherited `OmniBridge.initialize()` (`public initializer`) and its own `initializeWormhole()` (`external initializer`). Because `initialize()` is `public`, any caller can invoke it directly on the proxy before the deployer calls `initializeWormhole()`. Whichever function is called first wins — the `initializer` modifier permanently locks out the other. An attacker who front-runs the initialization gains `DEFAULT_ADMIN_ROLE`, sets `nearBridgeDerivedAddress` to their own key, and can thereafter forge MPC signatures to finalize arbitrary cross-chain transfers, minting unbacked bridged tokens to themselves.

---

### Finding Description

`OmniBridge` defines:

```solidity
// evm/src/omni-bridge/contracts/OmniBridge.sol L72-L86
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

`OmniBridgeWormhole` defines its own initializer that wraps the above:

```solidity
// evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol L32-L46
function initializeWormhole(...) external initializer {
    initialize(tokenImplementationAddress, nearBridgeDerivedAddress, omniBridgeChainId);
    _wormhole = IWormhole(wormholeAddress);
    _consistencyLevel = consistencyLevel;
}
```

Both carry the `initializer` modifier from OpenZeppelin's `Initializable`. The modifier allows exactly one call (the first one sets `_initialized = 1`; all subsequent calls revert). Because `OmniBridge.initialize()` is `public`, it is part of the proxy's external ABI and can be called by anyone — not just by `initializeWormhole()` internally.

**Attack window:** If the deployer deploys the `ERC1967Proxy` pointing to the `OmniBridgeWormhole` implementation without encoding `initializeWormhole()` call data in the same transaction (i.e., deploys the proxy first, then calls `initializeWormhole()` in a separate transaction), an attacker observing the mempool can front-run with a direct call to `OmniBridge.initialize(attacker_impl, attacker_key, chainId)`. This:

1. Sets `nearBridgeDerivedAddress = attacker_key` — the sole signature-verification anchor for `finTransfer()` and `deployToken()`.
2. Grants `DEFAULT_ADMIN_ROLE` and `PAUSABLE_ADMIN_ROLE` to the attacker.
3. Permanently blocks the deployer's subsequent `initializeWormhole()` call (reverts with `InvalidInitialization`).

The attacker then calls `setNearBridgeDerivedAddress(attacker_key)` (or it is already set) and forges valid ECDSA signatures to pass the check in `finTransfer()`:

```solidity
// evm/src/omni-bridge/contracts/OmniBridge.sol L311
if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
    revert InvalidSignature();
}
```

With a controlled `nearBridgeDerivedAddress`, the attacker can produce valid `signatureData` for any `TransferMessagePayload`, causing `finTransfer()` to mint arbitrary amounts of any `isBridgeToken` token to any recipient.

The same signature check governs `deployToken()`:

```solidity
// evm/src/omni-bridge/contracts/OmniBridge.sol L151
if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
    revert InvalidSignature();
}
```

So the attacker can also deploy arbitrary token mappings.

---

### Impact Explanation

**Critical.** An attacker who wins the initialization race:
- Controls `nearBridgeDerivedAddress`, the only cryptographic gate for `finTransfer()` and `deployToken()`.
- Can call `finTransfer()` with self-signed payloads to mint unbounded amounts of any registered bridge token to any address — direct unauthorized mint of bridged assets.
- Holds `DEFAULT_ADMIN_ROLE`, enabling them to upgrade the proxy implementation (UUPS `_authorizeUpgrade` is `onlyRole(DEFAULT_ADMIN_ROLE)`), permanently taking over the bridge.

---

### Likelihood Explanation

**Medium.** The vulnerability requires the deployer to use a two-step deployment (proxy deploy → separate `initializeWormhole()` call). This is a realistic deployment pattern, especially when using deployment scripts, multisig-based deployment flows, or factory contracts that separate proxy creation from initialization. The `initialize()` function being `public` (rather than `internal`) makes it trivially callable by any EOA or contract. No special knowledge or capital is required — only mempool observation and a single transaction.

---

### Recommendation

1. **Change `OmniBridge.initialize()` visibility from `public` to `internal`** so it cannot be called directly on the proxy. Only `initializeWormhole()` (or equivalent per-subclass initializers) should be externally callable.
2. **Ensure all proxy deployments atomically encode the initializer call** in the `ERC1967Proxy` constructor data (i.e., `new ERC1967Proxy(impl, abi.encodeCall(OmniBridgeWormhole.initializeWormhole, (...)))`), never deploying the proxy in a separate transaction from initialization.
3. Consider adding a dedicated constructor to `OmniBridgeWormhole` that calls `_disableInitializers()` explicitly (even though it is inherited) to make the intent clear and guard against future inheritance changes.

---

### Proof of Concept

```
1. Deployer broadcasts: ERC1967Proxy(OmniBridgeWormhole_impl, "")
   → proxy deployed, _initialized == 0, no roles set

2. Attacker observes mempool, front-runs with:
   proxy.initialize(
       attacker_token_impl,   // tokenImplementationAddress
       attacker_signer,       // nearBridgeDerivedAddress  ← attacker controls this key
       1                      // omniBridgeChainId
   )
   → _initialized set to 1
   → DEFAULT_ADMIN_ROLE granted to attacker
   → nearBridgeDerivedAddress = attacker_signer

3. Deployer's initializeWormhole(...) reverts: InvalidInitialization()

4. Attacker constructs a TransferMessagePayload:
   {destinationNonce: 1, originChain: 1, originNonce: 1,
    tokenAddress: <any isBridgeToken>, amount: 1_000_000e18,
    recipient: attacker_address, feeRecipient: ""}

5. Attacker signs the Borsh-encoded payload with attacker_signer's private key.

6. Attacker calls proxy.finTransfer(attacker_sig, payload)
   → ECDSA.recover(hash, attacker_sig) == attacker_signer == nearBridgeDerivedAddress ✓
   → completedTransfers[1] = true
   → IBridgeToken(tokenAddress).mint(attacker_address, 1_000_000e18)
   → Unbacked tokens minted to attacker.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L309-313)
```text
        bytes32 hashed = keccak256(borshEncoded);

        if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
            revert InvalidSignature();
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
