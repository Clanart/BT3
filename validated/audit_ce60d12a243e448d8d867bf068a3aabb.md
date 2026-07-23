Audit Report

## Title
Missing Factory Validation for `pool` Address in `MetricOmmPoolLiquidityAdder` Allows Malicious Pool to Drain User Tokens — (`metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

## Summary
`MetricOmmPoolLiquidityAdder` accepts an arbitrary, unvalidated `pool` address in every public entry point and stores it directly as the authoritative callback caller via `_setPayContext`. Because the callback's only caller check is `msg.sender == expectedPool`, a malicious contract supplied as `pool` passes every guard and can pull the victim's tokens up to the user-supplied max caps through `pay()`.

## Finding Description
`MetricOmmSwapRouterBase._setNextCallbackContext` calls `_requireFactoryPool(pool)` before writing any transient state, ensuring only factory-registered pools can trigger callbacks. `MetricOmmPoolLiquidityAdder._setPayContext` has no equivalent guard — it writes the raw caller-supplied address directly to transient storage.

Every public entry point (`addLiquidityExactShares`, `addLiquidityWeighted`) routes through `_addLiquidity`, which calls `_setPayContext(pool, ...)` with the unvalidated address. The callback `metricOmmModifyLiquidityCallback` then enforces only:

```solidity
if (msg.sender != expectedPool) revert InvalidCallbackCaller(msg.sender, expectedPool);
```

Because the malicious pool **is** the stored `expectedPool`, this check passes. The callback then reads token addresses from `IMetricOmmPool(msg.sender).getImmutables()` — fully attacker-controlled — and executes `pay(token0, payer, msg.sender, amount0Delta)` and `pay(token1, payer, msg.sender, amount1Delta)`, transferring tokens from the victim to the attacker's contract. The `MaxAmountExceeded` check is the only remaining bound, and it is set by the victim themselves.

The NatDoc at lines 19–21 acknowledges this explicitly but does not change the on-chain behavior.

## Impact Explanation
A victim who has approved `MetricOmmPoolLiquidityAdder` and calls any `addLiquidity*` variant with an attacker-controlled pool address suffers direct loss of up to `maxAmountToken0` of token0 and `maxAmountToken1` of token1, transferred to the attacker's contract. This is a direct loss of user principal with no recovery path, meeting the Critical/High threshold for direct fund loss.

## Likelihood Explanation
The attack requires the victim to call an `addLiquidity*` function with an attacker-controlled pool address. This is achievable via a malicious or compromised frontend, a phishing link, or a wrapper contract. No privileged role is required; `MetricOmmPoolLiquidityAdder` is a public, permissionless contract. The asymmetry with `MetricOmmSimpleRouter` — which does validate — makes this an easy mistake for integrators and users to overlook.

## Recommendation
Add a factory membership check inside `_addLiquidity` (or `_setPayContext`) mirroring the pattern in `MetricOmmSwapRouterBase`:

```solidity
function _addLiquidity(address pool, ...) internal returns (...) {
    if (!FACTORY.isPool(pool)) revert InvalidPool(pool);
    _setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
    ...
}
```

This requires storing the factory address as an immutable in `MetricOmmPoolLiquidityAdder`, exactly as `MetricOmmSwapRouterBase` does with `IMetricOmmPoolFactory internal immutable FACTORY`.

## Proof of Concept
```solidity
contract MaliciousPool {
    address immutable adder;
    address immutable token0;
    address immutable token1;
    uint256 constant MAX0 = 1000e18;
    uint256 constant MAX1 = 1000e18;

    constructor(address _adder, address _t0, address _t1) {
        adder = _adder; token0 = _t0; token1 = _t1;
    }

    function addLiquidity(address, uint80, LiquidityDelta calldata, bytes calldata, bytes calldata)
        external returns (uint256, uint256)
    {
        // Trigger callback with max amounts; KIND_PAY = 1
        IMetricOmmPoolLiquidityAdder(adder)
            .metricOmmModifyLiquidityCallback(MAX0, MAX1, abi.encode(uint8(1)));
        return (MAX0, MAX1);
    }

    function getImmutables() external view returns (PoolImmutables memory imm) {
        imm.token0 = token0;
        imm.token1 = token1;
    }
}

// Attack:
// 1. Victim approves LiquidityAdder for token0 and token1.
// 2. Victim (or tricked UI) calls:
liquidityAdder.addLiquidityExactShares(
    address(maliciousPool), // pool — not validated against factory
    victim,
    0,
    deltas,
    MAX0,
    MAX1,
    ""
);
// 3. MaliciousPool.addLiquidity fires → metricOmmModifyLiquidityCallback
//    msg.sender == expectedPool ✓ (malicious pool IS the stored pool)
//    amount0Delta <= max0 ✓
//    pay(token0, victim, maliciousPool, MAX0) → victim loses MAX0 of token0
//    pay(token1, victim, maliciousPool, MAX1) → victim loses MAX1 of token1
```