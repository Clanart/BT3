Audit Report

## Title
Block-number-based price velocity guard is miscalibrated on variable-blocktime chains, allowing oracle price manipulation to bypass the guard — (`metric-periphery/contracts/extensions/PriceVelocityGuardExtension.sol`)

## Summary

`PriceVelocityGuardExtension.beforeSwap` measures elapsed time using `block.number` and computes allowed price movement as `maxChangePerBlockE18² × (1 + blockDiff)`. On fast-block chains such as Arbitrum (~0.25 s/block), `blockDiff` accumulates ~48× faster in wall-clock time than on Ethereum (~12 s/block), making the guard ~5× more permissive per real-time window and allowing oracle price moves that should have been rejected to pass unchecked, causing swaps to execute at manipulated prices and draining LP value.

## Finding Description

In `PriceVelocityGuardExtension.beforeSwap`, the guard records and reads `lastUpdateBlock` as a raw `block.number`:

```solidity
uint64 prevBlock = s.lastUpdateBlock;          // line 55
s.lastUpdateBlock = uint64(block.number);      // line 58
uint256 blockDiff = block.number - prevBlock;  // line 63
uint256 allowedSq = uint256(maxChange) * uint256(maxChange) * (1 + blockDiff); // line 70
``` [1](#0-0) 

The `PriceVelocityState` struct stores `lastUpdateBlock` as a `uint64` block number, not a timestamp: [2](#0-1) 

A pool admin intending to cap price movement at 1%/12 s sets `maxChangePerBlockE18 = 1e16`. On Ethereum (1 block ≈ 12 s), after 12 real seconds `blockDiff = 1`, so `allowedChange = maxChange × sqrt(2) ≈ 1.41%`. On Arbitrum (1 block ≈ 0.25 s), after the same 12 real seconds `blockDiff = 48`, so `allowedChange = maxChange × sqrt(49) = 7%` — approximately **5× more permissive** per real-time window. Any oracle price mover who can shift the mid-price by, say, 6% in 12 real seconds satisfies `actualSq < allowedSq` on Arbitrum and the `PriceVelocityExceeded` revert never fires.

The codebase already demonstrates the correct pattern: `OracleValueStopLossExtension` uses `block.timestamp` for its time-sensitive decay: [3](#0-2) 

## Impact Explanation

The guard's sole purpose is to prevent bad-price execution by capping oracle mid-price velocity. When miscalibrated on fast-block chains, the guard fails to reject manipulated oracle prices, and swaps execute against those prices. LPs receive less than fair value — a direct loss of LP principal. This falls squarely within the allowed impact gate: **bad-price execution** and **direct loss of LP principal**.

## Likelihood Explanation

The protocol targets multi-chain deployment (L2-specific oracle paths exist in the codebase). Any pool that enables `PriceVelocityGuardExtension` on a fast-block chain is affected. A well-intentioned pool admin calibrating `maxChangePerBlockE18` on Ethereum mainnet will silently misconfigure the guard on every L2 deployment. No privileged attacker role is required; the only prerequisite is the ability to influence the oracle price feed (e.g., via a manipulable or low-latency price provider), which is the standard threat model the extension is designed to defend against.

## Recommendation

Replace `block.number` / `lastUpdateBlock` with `block.timestamp` / `lastUpdateTimestamp` and rename the parameter to `maxChangePerSecondE18`. The corrected formula:

```solidity
uint256 secondsDiff = block.timestamp - prevTimestamp;
uint256 allowedSq = uint256(maxChange) * uint256(maxChange) * (1 + secondsDiff);
```

This mirrors the timestamp-based approach already used in `OracleValueStopLossExtension` and makes the cap chain-agnostic. [4](#0-3) 

## Proof of Concept

1. Deploy the protocol on Arbitrum (~0.25 s/block).
2. Pool admin sets `maxChangePerBlockE18 = 1e16` (1%), intending to cap movement at ~1%/12 s.
3. Attacker waits 48 Arbitrum blocks (~12 real seconds); `blockDiff = 48`.
4. `allowedSq = (1e16)² × 49`; `allowedChange = 1e16 × 7 = 7%`.
5. Attacker pushes oracle mid-price 6% in those 12 seconds; `actualSq < allowedSq`, guard does not revert.
6. Swap executes at the manipulated price; LPs receive less than fair value.

A Foundry fork test on an Arbitrum fork can reproduce this by: (a) deploying the extension, (b) setting `maxChangePerBlockE18 = 1e16`, (c) rolling forward 48 blocks via `vm.roll`, (d) calling `beforeSwap` with a 6% mid-price shift, and (e) asserting no revert — confirming the guard is bypassed.

### Citations

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
