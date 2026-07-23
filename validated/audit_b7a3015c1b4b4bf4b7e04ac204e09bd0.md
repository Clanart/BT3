Audit Report

## Title
Synthetic Two-Feed Ratio Combines Legs Without Cross-Timestamp Validation, Enabling Bad-Price Execution — (`smart-contracts-poc/contracts/AnchoredPriceProvider.sol`)

## Summary

`AnchoredPriceProvider._getBidAndAskPrice()` computes a synthetic mid by dividing `price(baseFeedId)` by `price(quoteFeedId)`. Each leg's `refTime` is individually gated by `MAX_REF_STALENESS`, but the two `refTime` values are never compared to each other. The two feeds can therefore be up to `MAX_REF_STALENESS` apart in time, producing a materially wrong synthetic mid. Because the bid/ask band is anchored to this wrong mid, the clamp does not correct the error, and swaps execute at corrupted prices.

## Finding Description

In `_getBidAndAskPrice()`, both calls to `_readLeg` discard the returned `refTime` via the `,` placeholder:

```solidity
(uint256 mid,  uint256 spreadBps,  , bool ok)  = _readLeg(baseFeedId);   // refTime discarded
...
(uint256 mid2, uint256 spreadBps2, , bool ok2) = _readLeg(_quote);        // refTime discarded
mid = Math.mulDiv(mid, ORACLE_DECIMALS, mid2);
``` [1](#0-0) 

`_readLeg` does return `refTime` as its third value and checks it individually against `MAX_REF_STALENESS`:

```solidity
if (_isStale(refTime, block.timestamp, MAX_REF_STALENESS)) return (mid, spreadBps, refTime, false);
``` [2](#0-1) 

But no code ever computes `|refTime_base − refTime_quote|`. The maximum inter-leg skew is therefore bounded only by `MAX_REF_STALENESS`, which is an immutable settable up to 7 days:

```solidity
if (_maxRefStaleness > 7 days) revert MaxRefStalenessOutOfBounds();
MAX_REF_STALENESS = _maxRefStaleness;
``` [3](#0-2) 

The band clamp in `_computeBidAsk` is derived from the already-corrupted `mid`:

```solidity
uint256 refBid = _bandEdge(mid, BPS_BASE_U - half, Math.Rounding.Floor);
uint256 refAsk = _bandEdge(mid, BPS_BASE_U + half, Math.Rounding.Ceil);
``` [4](#0-3) 

Because the band is centered on the wrong mid, clamping does not restore correctness — it only prevents the custom source from diverging further from the already-wrong anchor.

## Impact Explanation

The pool calls `getBidAndAskPrice()` at swap time and executes every bin step at the returned bid/ask. A corrupted synthetic ratio shifts the entire bid/ask band by the same percentage error. Traders who swap against the pool receive or pay tokens at the wrong rate, constituting direct loss of user principal. This matches the "bad-price execution" allowed impact gate: a stale-anchored bid/ask quote reaches a pool swap.

## Likelihood Explanation

The trigger is fully unprivileged: any user calling `exactInputSingle` or `exactOutputSingle` through the router while the two feeds have different `refTime` values is affected. This is the normal operating condition — independently-updating oracles (e.g., Pyth Lazer for BTC/USD and Chainlink Data Streams for ETH/USD) routinely have different update cadences. No special setup, no oracle manipulation, and no privileged access is required.

## Recommendation

After both legs pass their individual staleness checks, capture both `refTime` values and enforce a maximum inter-leg skew before computing the ratio:

```solidity
(uint256 mid,  uint256 spreadBps,  uint256 refTime1, bool ok)  = _readLeg(baseFeedId);
(uint256 mid2, uint256 spreadBps2, uint256 refTime2, bool ok2) = _readLeg(_quote);
if (!ok || !ok2 || mid2 == 0) return (0, type(uint128).max);

uint256 skew = refTime1 > refTime2 ? refTime1 - refTime2 : refTime2 - refTime1;
if (skew > MAX_LEG_SKEW) return (0, type(uint128).max);
```

`MAX_LEG_SKEW` should be set as an immutable at construction (e.g., 60 seconds), appropriate to the update cadence of the underlying feeds.

## Proof of Concept

**Setup**: `AnchoredPriceProvider` deployed with `baseFeedId = BTC/USD`, `quoteFeedId = ETH/USD`, `MAX_REF_STALENESS = 3600` (1 hour).

**State at swap time**:
- `price(BTC/USD)` → mid = 60 000e8, refTime = block.timestamp (fresh)
- `price(ETH/USD)` → mid = 2 000e8, refTime = block.timestamp − 3 599 (1 second before staleness cutoff; passes `_isStale`)

**True ETH price now**: 2 200e8 (ETH rose 10% in the past hour).

**Computed synthetic mid**: `60 000e8 × 1e8 / 2 000e8 = 30.0` (using stale ETH price)

**True mid**: `60 000 / 2 200 ≈ 27.27`

**Error**: ~10% overpricing of BTC/ETH. A trader buying 10 BTC pays 300 ETH instead of 272.7 ETH — a loss of ~27.3 ETH in a single swap, with no oracle manipulation required, only the normal lag between two independently-updating feeds. The band clamp (lines 309–310) is anchored to the corrupted mid of 30.0 and does not correct the error. [5](#0-4)

### Citations

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L150-151)
```text
        if (_maxRefStaleness > 7 days) revert MaxRefStalenessOutOfBounds(); // 0 allowed = same-block reference
        MAX_REF_STALENESS = _maxRefStaleness;
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L258-272)
```text
    function _getBidAndAskPrice() internal returns (uint128, uint128) {
        (uint256 mid, uint256 spreadBps, , bool ok) = _readLeg(baseFeedId);
        if (!ok) return (0, type(uint128).max);

        bytes32 _quote = quoteFeedId;
        if (_quote != bytes32(0)) {
            (uint256 mid2, uint256 spreadBps2, , bool ok2) = _readLeg(_quote);
            if (!ok2 || mid2 == 0) return (0, type(uint128).max);
            // Synthetic ratio (8-decimal): mid1 / mid2. Relative uncertainties of a ratio add.
            mid = Math.mulDiv(mid, ORACLE_DECIMALS, mid2);
            spreadBps += spreadBps2;
        }

        return _computeBidAsk(mid, spreadBps);
    }
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L283-283)
```text
        if (_isStale(refTime, block.timestamp, MAX_REF_STALENESS)) return (mid, spreadBps, refTime, false);
```

**File:** smart-contracts-poc/contracts/AnchoredPriceProvider.sol (L309-310)
```text
        uint256 refBid = _bandEdge(mid, BPS_BASE_U - half, Math.Rounding.Floor);
        uint256 refAsk = _bandEdge(mid, BPS_BASE_U + half, Math.Rounding.Ceil);
```
