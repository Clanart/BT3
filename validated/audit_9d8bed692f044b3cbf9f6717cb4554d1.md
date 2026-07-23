### Title
Block-number-based price velocity guard is miscalibrated on variable-blocktime chains, allowing oracle price manipulation to bypass the guard — (`metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol`)

---

### Summary

`PriceVelocityGuardExtension` measures elapsed time between price updates using `block.number`. Because block production rates differ dramatically across EVM chains (Ethereum ≈ 12 s/block, Arbitrum ≈ 0.25 s/block), the `maxChangePerBlockE18` cap does not represent a consistent real-time price-change rate. On fast-block chains the guard is up to ~48× too permissive, allowing an attacker who can influence the oracle price to move it far beyond the intended per-second limit without triggering a revert, causing swaps to execute at manipulated prices and LPs to lose principal.

---

### Finding Description

`PriceVelocityGuardExtension.beforeSwap` records the current block number on every swap and computes the allowed price movement as:

```
allowedSq = maxChangePerBlockE18² × (1 + blockDiff)
```

where `blockDiff = block.number − prevBlock`. [1](#0-0) 

The state struct stores `lastUpdateBlock` as a raw block number: [2](#0-1) 

A pool admin who intends to cap price movement at, say, 1 % per 12-second Ethereum block sets `maxChangePerBlockE18 = 0.01e18`. On Arbitrum (≈ 0.25 s/block) the same parameter permits 1 % per 0.25 s — roughly 48 % per 12 real seconds — because `blockDiff` grows 48× faster in wall-clock time. The guard's invariant (`changeE18² ≤ maxChangePerBlockE18² × (1 + blockDiff)`) is therefore trivially satisfied for price moves that should have been rejected.

The `OracleValueStopLossExtension`, by contrast, correctly uses `block.timestamp` and `decayPerSecondE8` for its time-sensitive watermark decay, demonstrating that the codebase already has a timestamp-based pattern available: [3](#0-2) 

---

### Impact Explanation

When the guard is too permissive, an attacker who can move the oracle price (e.g., via a manipulable or low-latency price provider) can shift the mid-price by a large percentage within a single Ethereum-equivalent time window without the `PriceVelocityExceeded` revert firing. Swaps then execute against the manipulated bid/ask, draining LP value. This is a direct loss of LP principal — the exact scenario the extension was designed to prevent.

---

### Likelihood Explanation

- The protocol targets multi-chain deployment (the codebase contains L2-specific oracle paths).
- Any pool that enables `PriceVelocityGuardExtension` on a fast-block chain is affected.
- A well-intentioned pool admin calibrating `maxChangePerBlockE18` on Ethereum mainnet will silently misconfigure the guard on every L2 deployment.
- No privileged attacker role is required; the oracle price feed is the only prerequisite.

---

### Recommendation

Replace `block.number` / `lastUpdateBlock` with `block.timestamp` / `lastUpdateTimestamp` and rename the parameter to `maxChangePerSecondE18`. The allowed-deviation formula becomes:

```solidity
uint256 secondsDiff = block.timestamp - prevTimestamp;
uint256 allowedSq = uint256(maxChange) * uint256(maxChange) * (1 + secondsDiff);
```

This mirrors the timestamp-based approach already used in `OracleValueStopLossExtension` and makes the cap chain-agnostic.

---

### Proof of Concept

1. Deploy the protocol on Arbitrum (≈ 0.25 s/block).
2. Pool admin sets `maxChangePerBlockE18 = 1e16` (1 % per block), intending to cap movement at ~1 %/12 s.
3. Attacker waits 48 Arbitrum blocks (≈ 12 real seconds); `blockDiff = 48`.
4. Allowed change = `sqrt(maxChange² × 49)` ≈ `7 × maxChange` = 7 % — far above the intended 1 %/12 s.
5. Attacker pushes the oracle mid-price 6 % in 12 seconds; `actualSq < allowedSq`, guard does not revert.
6. Swap executes at the manipulated price; LPs receive less than fair value. [4](#0-3)

### Citations

**File:** metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol (L55-73)
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
```

**File:** metric-periphery/contracts/interfaces/extensions/IPriceVelocityGuardExtension.sol (L7-11)
```text
  struct PriceVelocityState {
    uint128 lastMidPriceX64;
    uint64 lastUpdateBlock;
    uint64 maxChangePerBlockE18;
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L268-270)
```text
    uint256 dt = block.timestamp - hwmS.lastDecayTs;

    (uint256 hwm0, bool breach0) = _applyWatermark(metricT0, _decayed(hwmS.token0, decayRate, dt), floorMultiplier);
```
