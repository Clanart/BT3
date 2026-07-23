The claim requires careful verification against the actual code. The key question is whether the factory validation checks exist.

**Router (`MetricOmmSimpleRouter`):** The claim states the router has no factory check. This is **factually incorrect**. `_setNextCallbackContext` in `MetricOmmSwapRouterBase` calls `_requireFactoryPool(pool)` before storing the context, and `_requireExpectedCallbackCaller` also calls `FACTORY.isPool(caller)`. Both entry and callback paths are guarded.

**Liquidity Adder (`MetricOmmPoolLiquidityAdder`):** The claim is **correct** here. The contract has no `factory` reference, no `isPool` check, and the NatSpec explicitly acknowledges this gap. The callback only checks `msg.sender == expectedPool` — which is the attacker-supplied pool.

---

Audit Report

## Title
`MetricOmmPoolLiquidityAdder` accepts arbitrary pool addresses without factory validation, enabling malicious pool to drain user-approved tokens — (File: metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol)

## Summary
`MetricOmmPoolLiquidityAdder` accepts a caller-supplied `pool` address in `addLiquidityExactShares` and `addLiquidityWeighted` and stores it as the trusted callback caller without verifying it against the factory registry. A malicious pool implementing `IMetricOmmPoolActions` can call back `metricOmmModifyLiquidityCallback` and cause the adder to pull up to `maxAmountToken0`/`maxAmountToken1` from the user, receiving nothing in return.

## Finding Description
`_addLiquidity` stores the caller-supplied `pool` as the expected callback caller via `_setPayContext` and immediately calls `pool.addLiquidity(...)`. In `metricOmmModifyLiquidityCallback`, the only guard is `msg.sender == expectedPool` — which is the attacker-controlled address. The malicious pool's `addLiquidity` implementation calls back `metricOmmModifyLiquidityCallback` with `amount0Delta = max0` and `amount1Delta = max1`, passing the cap check. The callback then reads `token0`/`token1` from `IMetricOmmPool(msg.sender).getImmutables()` — again querying the malicious pool — and calls `pay(token0, payer, msg.sender, amount0Delta)`, transferring the user's tokens to the attacker. The contract's own NatSpec at lines 19–21 explicitly acknowledges the missing guard. Unlike `MetricOmmSimpleRouter`, which holds an immutable `FACTORY` reference and calls `_requireFactoryPool(pool)` inside every `_setNextCallbackContext` call, `MetricOmmPoolLiquidityAdder` has no factory reference at all.

## Impact Explanation
Direct loss of user principal up to `maxAmountToken0`/`maxAmountToken1` per call. The user receives no LP position in return. This is a Medium-severity direct loss of user funds reachable by any unprivileged caller who is induced to supply a malicious pool address.

## Likelihood Explanation
Deploying a contract implementing `IMetricOmmPoolActions` is permissionless and trivial. Users interacting via front-ends that resolve pool addresses are susceptible to phishing UIs or compromised integrations. Users commonly grant large ERC-20 approvals to liquidity adder contracts. The exploit is fully on-chain and repeatable.

## Recommendation
Add a `factory` immutable to `MetricOmmPoolLiquidityAdder` (mirroring `MetricOmmSwapRouterBase`) and call `IMetricOmmPoolFactory(factory).isPool(pool)` at the top of `_addLiquidity` (and before the probe call in `addLiquidityWeighted`) before `_setPayContext`. Revert with a dedicated error if the pool is not registered.

## Proof of Concept
1. Attacker deploys `MaliciousPool` implementing `IMetricOmmPoolActions`. Its `addLiquidity` calls back `metricOmmModifyLiquidityCallback(maxAmountToken0, maxAmountToken1, abi.encode(KIND_PAY))` and returns `(maxAmountToken0, maxAmountToken1)`. Its `getImmutables()` returns attacker-chosen `token0`/`token1`.
2. Victim approves the liquidity adder for `token0` up to `maxAmountToken0`.
3. Victim (or attacker on victim's behalf) calls `addLiquidityExactShares(MaliciousPool, victim, salt, deltas, maxAmountToken0, 0, "")`.
4. `_addLiquidity` calls `_setPayContext(MaliciousPool, victim, maxAmountToken0, 0)` then `MaliciousPool.addLiquidity(...)`.
5. `MaliciousPool` calls back `metricOmmModifyLiquidityCallback(maxAmountToken0, 0, abi.encode(KIND_PAY))`.
6. Guard passes: `msg.sender == expectedPool == MaliciousPool`. Cap check passes: `maxAmountToken0 <= max0`.
7. Adder calls `pay(token0, victim, MaliciousPool, maxAmountToken0)` — victim's tokens transferred to attacker.
8. `MaliciousPool.addLiquidity` returns `(maxAmountToken0, 0)` — outer call succeeds, `_clearPayContext` runs.
9. Victim loses `maxAmountToken0` tokens and receives no LP shares.