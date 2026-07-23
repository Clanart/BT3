### Title
Pool Admin Can Frontrun Fee Increases to Maximum Without Timelock, Causing Direct User Loss - (File: metric-core/contracts/MetricOmmPoolFactory.sol)

---

### Summary

`setPoolAdminFees` in `MetricOmmPoolFactory` takes effect immediately with no timelock. Because anyone can create a pool and become its admin, a malicious pool admin can observe a pending swap or liquidity transaction in the mempool and frontrun it by raising fees to the protocol-enforced maximum, extracting up to 20% spread fee plus 1% notional fee from the victim's trade.

---

### Finding Description

`MetricOmmPoolFactory.createPool` is permissionless — any caller can deploy a pool and designate themselves as `poolAdmin`. [1](#0-0) 

The pool admin can call `setPoolAdminFees` at any time to raise fees up to the hard caps: [2](#0-1) 

The hard caps are `HARD_MAX_SPREAD_FEE_E6 = 200_000` (20%) and `HARD_MAX_NOTIONAL_FEE_E8 = 1_000_000` (1%): [3](#0-2) 

There is **no timelock** on `setPoolAdminFees`. By contrast, the price-provider change path correctly enforces a timelock via `priceProviderTimelock[pool]`: [4](#0-3) 

Fee changes propagate immediately to the pool via `setPoolFees`: [5](#0-4) 

which writes `spreadFeeE6` and `notionalFeeE8` directly into pool slot0: [6](#0-5) 

The notional fee is then deducted from the user's output (exact-in) or added to the user's input (exact-out) during `_executeSwap`: [7](#0-6) 

---

### Impact Explanation

A malicious pool admin can frontrun any pending swap by raising `adminSpreadFeeE6` to `maxAdminSpreadFeeE6` (20%) and `adminNotionalFeeE8` to `maxAdminNotionalFeeE8` (1%). The victim's swap executes at up to ~21% worse effective price than the pool state they observed. The excess fee accrues to the pool and is later collected by the admin via `collectPoolFees`. This is a direct, quantifiable loss of user principal proportional to trade size, with no slippage-protection mechanism that accounts for sudden fee changes (the router's `amountOutMinimum` / `amountInMaximum` guards do not protect against fee increases because the fee is baked into the pool's swap math before the output amount is computed).

---

### Likelihood Explanation

- Pool creation is permissionless; any attacker can become a pool admin.
- The attack requires only a single frontrun transaction with no special privileges beyond pool admin role.
- Ethereum/EVM mempools are public; large swaps are visible before inclusion.
- The attacker profits directly from the fee increase, giving clear economic incentive.
- The only friction is that the attacker must have previously established a pool with enough liquidity to attract victims.

---

### Recommendation

Apply the same timelock pattern already used for price-provider changes to admin fee changes. Introduce a two-step propose/execute flow for `setPoolAdminFees`:

1. `proposePoolAdminFees(pool, newSpread, newNotional)` — records the pending values and a `block.timestamp + feeTimelock` execution timestamp.
2. `executePoolAdminFees(pool)` — callable only after the timelock elapses, then applies the new fees.

This mirrors the existing `proposePoolPriceProvider` / `executePoolPriceProviderUpdate` pattern and gives users advance notice of fee changes before they take effect.

---

### Proof of Concept

1. Attacker calls `createPool` with `adminSpreadFeeE6 = 0`, `adminNotionalFeeE8 = 0`, designating themselves as `admin`. Pool is live with near-zero fees.
2. Legitimate users begin routing swaps through the pool.
3. Attacker observes a large pending `exactInputSingle` call (e.g., 1,000,000 USDC → TOKEN1) in the mempool.
4. Attacker submits `setPoolAdminFees(pool, 200_000, 1_000_000)` with higher gas, frontrunning the victim.
5. `setPoolAdminFees` immediately calls `IMetricOmmPoolFactoryActions(pool).setPoolFees(200_000, 1_000_000)`, writing the new fees into pool slot0.
6. Victim's swap executes: the pool deducts ~20% spread fee and ~1% notional fee from the output. On a 1,000,000 USDC swap the victim loses on the order of $200,000+ in value relative to the quoted price.
7. Attacker calls `collectPoolFees(pool)` to withdraw the accrued admin fee share.

### Citations

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L44-45)
```text
  uint24 internal constant HARD_MAX_SPREAD_FEE_E6 = 200_000;
  uint24 internal constant HARD_MAX_NOTIONAL_FEE_E8 = 1_000_000;
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L156-157)
```text
  function createPool(PoolParameters calldata params) external override returns (address pool) {
    if (poolDeployer == address(0)) revert PoolDeployerNotSet();
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

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L487-490)
```text
    uint256 executeAfter = block.timestamp + timelock;
    pendingPriceProvider[pool] = newPriceProvider;
    pendingPriceProviderExecuteAfter[pool] = executeAfter;
    emit PoolPriceProviderChangeProposed(pool, current, newPriceProvider, executeAfter);
```

**File:** metric-core/contracts/MetricOmmPool.sol (L437-452)
```text
  function setPoolFees(uint24 newSpreadFeeE6, uint24 newNotionalFeeE8)
    external
    onlyFactory
    nonReentrant(PoolActions.SET_POOL_FEES)
  {
    unchecked {
      if (newSpreadFeeE6 != spreadFeeE6) {
        spreadFeeE6 = newSpreadFeeE6;
        emit SpreadFeeUpdated(newSpreadFeeE6);
      }
      if (newNotionalFeeE8 != notionalFeeE8) {
        notionalFeeE8 = newNotionalFeeE8;
        emit NotionalFeeUpdated(newNotionalFeeE8);
      }
    }
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L750-761)
```text
      if (notionalFeeE8 > 0) {
        if (amountSpecified > 0) {
          // exact in: notional fee on output token
          if (zeroForOne) {
            // safe because amount1DeltaScaled is bounded by uint128 total scaled token1 in bins.
            // forge-lint: disable-next-line(unsafe-typecast)
            uint256 notionalFeeScaled = uint256(-amount1DeltaScaled) * notionalFeeE8 / 1e8;
            if (notionalFeeScaled > 0) {
              // safe because notionalFeeScaled is bounded by uint128
              // forge-lint: disable-next-line(unsafe-typecast)
              amount1DeltaScaled = amount1DeltaScaled + int256(notionalFeeScaled);
              notionalFeeToken1Scaled = (uint256(notionalFeeToken1Scaled) + notionalFeeScaled).toUint128();
```
