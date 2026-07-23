### Title
`block.number` Dependency in `PriceVelocityGuardExtension` Causes Swap DoS on L2s with L1-Anchored Block Numbers — (File: `metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol`)

---

### Summary

`PriceVelocityGuardExtension` uses `block.number` to measure elapsed blocks between oracle price observations and scale the allowed price movement. On L2s like Arbitrum where `block.number` returns the **L1 block number** (~12 s cadence), many consecutive L2 swaps see `blockDiff = 0`, collapsing the allowed-change budget to its minimum and reverting any swap where the oracle price moved more than `maxChangePerBlockE18` since the last swap — even when that movement is entirely legitimate over the elapsed L2 blocks.

---

### Finding Description

`beforeSwap` records and compares oracle mid-prices using `block.number`: [1](#0-0) 

```
s.lastUpdateBlock = uint64(block.number);   // line 58
...
uint256 blockDiff = block.number - prevBlock;  // line 63
uint256 allowedSq = uint256(maxChange) * uint256(maxChange) * (1 + blockDiff);  // line 70
```

The design intent (NatSpec line 15–17) is that the allowed deviation **scales with elapsed blocks**: `maxChangePerBlockE18 * sqrt(1 + blockDiff)`. On Arbitrum, `block.number` returns the L1 block number. Approximately 48 L2 blocks are produced per L1 block. Any two swaps that fall within the same L1 block produce `blockDiff = 0`, so:

```
allowedSq = maxChange² × 1   (minimum possible budget)
```

If the oracle price moved by more than `maxChangePerBlockE18` between those two L2 blocks — a completely normal occurrence when the oracle updates every L2 block — the check `actualSq > allowedSq` fires and the swap reverts with `PriceVelocityExceeded`. [2](#0-1) 

The same stale-block problem exists in `setLastMidPrice`, which also stamps `block.number`: [3](#0-2) 

---

### Impact Explanation

Any pool on Arbitrum (or another L2 where `block.number` returns L1 block numbers) that attaches `PriceVelocityGuardExtension` will have its `beforeSwap` hook revert whenever the oracle price moves by more than `maxChangePerBlockE18` between two swaps that land in the same L1 block. Because L1 blocks span ~48 L2 blocks, this condition is triggered routinely during normal oracle price updates, rendering the pool's swap flow **unusable** for the duration of each L1 block. This matches the allowed impact: *"Broken core pool functionality causing unusable swap flows."*

---

### Likelihood Explanation

Medium. The trigger requires only that:
1. The pool is deployed on an L2 where `block.number` returns L1 block numbers (Arbitrum is the primary example).
2. The oracle price moves by more than `maxChangePerBlockE18` between two swaps within the same L1 block.

Both conditions are routine in production. No privileged actor or malicious setup is required; any ordinary swap attempt is sufficient.

---

### Recommendation

Replace `block.number` with `block.timestamp` in both `beforeSwap` and `setLastMidPrice`, renaming the parameter to `maxChangePerSecondE18`. Timestamps are consistent across L1 and L2 environments and directly reflect elapsed real time. Alternatively, document explicitly that the extension must not be used on chains where `block.number` does not return the chain's own block number, and provide a chain-specific override (e.g., `ArbSys(100).arbBlockNumber()` for Arbitrum).

---

### Proof of Concept

1. Deploy a pool on Arbitrum with `PriceVelocityGuardExtension`; set `maxChangePerBlockE18 = 1e16` (1 % per block, calibrated for L2 blocks).
2. **Swap A** executes at L2 block N (L1 block M). `lastMidPriceX64 = P`, `lastUpdateBlock = M`.
3. Oracle price updates at L2 block N+10 (still L1 block M): new mid = `P × 1.03` (3 % move, normal for 10 L2 blocks).
4. **Swap B** executes at L2 block N+10 (still L1 block M):
   - `prevBlock = M`, `block.number = M` → `blockDiff = 0`
   - `allowedSq = (1e16)² × 1 = 1e32`
   - `changeE18 = 3e16`, `actualSq = 9e32`
   - `9e32 > 1e32` → **revert `PriceVelocityExceeded`**
5. Every subsequent swap within L1 block M that observes any oracle movement above 1 % also reverts. The pool is effectively frozen for ~12 seconds per L1 block. [4](#0-3)

### Citations

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L29-33)
```text
  function setLastMidPrice(address pool_, uint128 newLastMidPriceX64) external onlyPoolAdmin(pool_) {
    PriceVelocityState storage s = priceVelocityState[pool_];
    s.lastMidPriceX64 = newLastMidPriceX64;
    s.lastUpdateBlock = uint64(block.number);
    emit LastMidPriceUpdated(pool_, newLastMidPriceX64);
```

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L55-74)
```text
    uint64 prevBlock = s.lastUpdateBlock;

    s.lastMidPriceX64 = midPrice;
    s.lastUpdateBlock = uint64(block.number);

    if (prevMid != 0) {
      uint64 maxChange = s.maxChangePerBlockE18;
      if (maxChange != 0) {
        uint256 blockDiff = block.number - prevBlock;

        uint256 delta = midPrice > prevMid ? uint256(midPrice - prevMid) : uint256(prevMid - midPrice);

        uint256 changeE18 = (delta * 1e18) / uint256(prevMid);

        uint256 actualSq = changeE18 * changeE18;
        uint256 allowedSq = uint256(maxChange) * uint256(maxChange) * (1 + blockDiff);

        if (actualSq > allowedSq) {
          revert PriceVelocityExceeded(actualSq, allowedSq);
        }
```
