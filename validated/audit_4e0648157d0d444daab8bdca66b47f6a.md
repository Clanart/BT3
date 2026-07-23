Audit Report

## Title
Pool Admin Can Frontrun Fee Increases to Maximum Without Timelock, Causing Direct User Loss - (File: metric-core/contracts/MetricOmmPoolFactory.sol)

## Summary
`setPoolAdminFees` in `MetricOmmPoolFactory` applies fee changes immediately with no timelock, while the analogous price-provider change path enforces a configurable timelock. Because `createPool` is permissionless, any caller can become a pool admin and frontrun pending swaps by raising fees to the protocol-enforced maximum (20% spread + 1% notional), extracting value from victims whose `amountOutMinimum` tolerance is wider than the fee delta.

## Finding Description
`createPool` is open to any caller, who designates themselves as `poolAdmin` at line 212. `setPoolAdminFees` (lines 408–435) validates only that the new fees are within `maxAdminSpreadFeeE6` / `maxAdminNotionalFeeE8`, then immediately calls `IMetricOmmPoolFactoryActions(pool).setPoolFees(...)` (line 431–432), which writes `spreadFeeE6` and `notionalFeeE8` directly into pool state (lines 443–450 of `MetricOmmPool.sol`). There is no pending/execute two-step flow and no timestamp check. By contrast, `proposePoolPriceProvider` records `block.timestamp + timelock` and `executePoolPriceProviderUpdate` enforces it (lines 487–490, 497–499 of `MetricOmmPoolFactory.sol`). The notional fee is deducted from the output token on exact-in swaps (lines 756–761 of `MetricOmmPool.sol`) and added to the input token on exact-out swaps (lines 777–790), after the bin-curve math completes. The router's `amountOutMinimum` check (line 83 of `MetricOmmSimpleRouter.sol`) fires after the pool returns the already-fee-reduced delta, so it only protects users whose minimum is tighter than the fee increase; users with looser tolerances execute at the degraded price and suffer direct principal loss.

## Impact Explanation
A malicious pool admin can raise `adminSpreadFeeE6` to `maxAdminSpreadFeeE6` (200,000 = 20%) and `adminNotionalFeeE8` to `maxAdminNotionalFeeE8` (1,000,000 = 1%) in a single frontrun transaction. On a 1,000,000 USDC exact-input swap the victim can lose on the order of $200,000+ in output value relative to the price they observed. The excess accrues as pool fees collectible by the admin via `collectPoolFees`. This is a direct, quantifiable loss of user principal — a Critical/High allowed impact under the contest gate.

## Likelihood Explanation
Pool creation is permissionless; any attacker can become a pool admin at zero privilege cost. EVM mempools are public; large swaps are visible before inclusion. The attack requires one frontrun transaction with higher gas. The attacker profits directly and repeatably from each fee-spike/collect cycle. The only friction is bootstrapping enough liquidity to attract victims.

## Recommendation
Apply the same two-step propose/execute timelock pattern already used for price-provider changes to admin fee changes. Add `proposePoolAdminFees(pool, newSpread, newNotional)` that records pending values and `block.timestamp + feeTimelock`, and `executePoolAdminFees(pool)` callable only after the timelock elapses. This mirrors `proposePoolPriceProvider` / `executePoolPriceProviderUpdate` and gives users advance notice before fee changes take effect.

## Proof of Concept
1. Attacker calls `createPool` with `adminSpreadFeeE6 = 0`, `adminNotionalFeeE8 = 0`, designating themselves as `admin`. Pool is live with near-zero fees.
2. Legitimate users begin routing swaps through the pool.
3. Attacker observes a large pending `exactInputSingle` call (e.g., 1,000,000 USDC → TOKEN1) in the mempool with a loose `amountOutMinimum`.
4. Attacker submits `setPoolAdminFees(pool, 200_000, 1_000_000)` with higher gas, frontrunning the victim.
5. `setPoolAdminFees` immediately calls `IMetricOmmPoolFactoryActions(pool).setPoolFees(200_000, 1_000_000)`, writing the new fees into pool slot0 with no timelock check.
6. Victim's swap executes: the pool deducts ~20% spread fee and ~1% notional fee from the output. On a 1,000,000 USDC swap the victim loses on the order of $200,000+ in value relative to the quoted price, and the transaction does not revert because the degraded output still exceeds the victim's loose `amountOutMinimum`.
7. Attacker calls `collectPoolFees(pool)` to withdraw the accrued admin fee share.