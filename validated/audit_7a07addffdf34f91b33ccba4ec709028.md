### Title
Optional, caller-controlled `price-feeds` update lets borrowers/liquidators selectively choose a stale on-chain Pyth price over the true current price to over-borrow or wrongfully liquidate - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
`borrow`, `collateral-add`, `collateral-remove`, `liquidate`, and `liquidate-redeem` all accept an *optional* `price-feeds` parameter that, if provided, pushes a fresh Pyth update before the action executes, and if `none`, silently skips the update and reuses whatever price is already cached on-chain (as long as it is not older than the asset's `max-staleness`). Because the caller of these functions is the same party who decides whether to submit `price-feeds`, they can deliberately choose to *not* update the price when the already-cached (stale but still "fresh enough") price is more favorable to them, and choose to update it when the fresh price is more favorable — exactly the "keeper decides whether to update the oracle" pattern from Flatmoney H-6.

### Finding Description
The staleness/legality checks in `price-resolve` only assert that the price is not stale relative to `max-staleness` and not older than the previously seen timestamp: [1](#0-0) 

This means any price already stored in `pyth-storage-v4` up to `max-staleness` seconds old is considered fully valid for use in `borrow`/`collateral-add`/`collateral-remove`/`liquidate`. The decision to push a newer price before consuming it is entirely optional and controlled by whoever calls the market function: [2](#0-1) 

Every hot-path entrypoint calls `write-feeds` with the caller-supplied `price-feeds` argument at the very start, before any collateral/debt valuation happens: [3](#0-2) [4](#0-3) 

Because `write-feeds` treats `none` as a no-op ("If list is none, does nothing"), the same account performing `borrow` or `liquidate` decides, transaction-by-transaction, whether to force the true current price on-chain or to rely on the previous, possibly stale-but-still-passing, cached price. This mirrors the Flatmoney H-6 root cause: the mandatory oracle update ("keeper must update the Pyth price") can be bypassed by supplying an empty/`none` update, letting the same actor selectively pick whichever of the two available prices (old cached vs. fresh) benefits them, subject only to a staleness window rather than to true freshness enforcement.

### Impact Explanation
- **Over-borrowing (protocol insolvency / bad debt to LPs):** If collateral's true price has just fallen but the on-chain cached Pyth price is still the higher pre-drop value and is within `max-staleness`, a borrower can call `borrow` with `price-feeds: none` to borrow against an inflated collateral valuation, extracting more debt than the position can safely support. This directly creates bad debt that LPs absorb — protocol insolvency.
- **Wrongful/opportunistic liquidation (theft of borrower's collateral):** Conversely, a liquidator can call `liquidate` with `price-feeds: none`, relying on a stale, unfavorable-to-the-borrower cached price (still within `max-staleness`) to push a healthy position over the liquidation threshold or to maximize the liquidation penalty tier, even though the true current price would show the position as healthy or less severely underwater. This results in unwarranted seizure of the borrower's collateral plus penalty — theft of user funds.

Both scenarios fall within scope: "direct theft of user funds at rest... or protocol insolvency" (Critical), since the profit/loss direction is determined purely by which of two valid-but-different prices the same actor chooses to make effective.

### Likelihood Explanation
`price-feeds` is a normal user-facing optional parameter on every hot-path function — no special permission is required to omit it. Any borrower or liquidator can trivially decide, at the moment of calling `borrow`/`liquidate`/etc., whether or not to include a fresh Pyth update, based on which cached vs. fresh price benefits them, as long as the older cached price is still inside its asset's `max-staleness` window. Because Pyth prices are only pushed on-chain when someone calls `verify-and-update-price-feeds` (a pull-oracle model), a stale-but-accepted price is very likely to exist in the `max-staleness` window during normal, non-adversarial market activity, making this a persistently exploitable condition, not a rare edge case.

### Recommendation
- Do not allow `price-feeds: none` to bypass a mandatory freshness requirement for value-affecting actions (`borrow`, `collateral-remove`, `liquidate`). Require that the price used for LTV/health/liquidation calculations be updated to (or validated against) the latest available off-chain price at execution time, similar to the flatmoney fix.
- Alternatively, tighten `max-staleness` to a window small enough that price divergence within it cannot be economically exploited, and/or require the update transaction's on-chain price timestamp to be within a small delta of `stacks-block-time` for these specific state-changing calls (not just "not older than previously seen").
- Consider disallowing the caller of `borrow`/`liquidate` from being the one who decides whether the price gets refreshed — e.g., always require submission of a feed for the involved assets, falling back to failure (`ERR-PRICE-FEED-UPDATE-FAILED`) rather than silently reusing a cached value.

### Proof of Concept
1. Alice supplies sBTC collateral when BTC's true and on-chain-cached Pyth price are both $60,000; her position is at 70% LTV (healthy) after borrowing $42,000 USDC, matching the existing test scenario in [5](#0-4) .
2. Off-chain, BTC's true price drops to $50,000, but nobody has yet called `verify-and-update-price-feeds` for BTC on-chain, so `pyth-storage-v4` still returns the old $60,000 price, and `(- stacks-block-time ts)` is still `<= max-staleness` for BTC.
3. Alice calls `borrow` again with `price-feeds: none` (as permitted by `write-feeds` at [2](#0-1) ). The market resolves BTC's price via `price-resolve`, which accepts the stale $60,000 price because it still passes `oracle-timestamp-fresh` [6](#0-5) .
4. Alice successfully borrows additional USDC that would have been rejected (`ERR-UNHEALTHY`) had the true $50,000 price been used — exactly the failure mode demonstrated (in reverse, using a fresh price) by the existing test at lines 156-174 of `borrow-basic.test.ts`. By choosing not to submit `price-feeds`, Alice avoided triggering that same `ERR-UNHEALTHY` check, extracting excess, under-collateralized debt at the LPs' expense.

### Citations

**File:** mainnet/contracts/market/v0-4-market.clar (L146-152)
```text
;; Process optional list of price feed updates
;; If list is provided, folds over it and updates all feeds
;; If list is none, does nothing (allows for backward compatibility)
(define-private (write-feeds (feeds (optional (list 3 (buff 8192)))))
  (match feeds
    entries (fold write-feed entries (ok true))
    (ok true)))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L362-395)
```text
(define-private (oracle-price-legal (p uint))
  (> p u0))

(define-private (oracle-timestamp-fresh (ts uint) (prev uint) (max-staleness uint))
  (let ((delta (if (> ts stacks-block-time)
                   u0
                   (- stacks-block-time ts))))
    (and
      (<= delta max-staleness)
      (>= ts prev))))

(define-private (price-resolve
  (data { type: (buff 1), ident: (buff 32), callcode: (optional (buff 1)), max-staleness: uint }))
  (let ((type (get type data))
        (ident (get ident data))
        (key { type: type, ident: ident })
        (resolution (try! (resolve-price-feed type ident)))
        (price (get value resolution))
        (callcode (get callcode data))
        (final-price (try! (resolve-callcode price callcode)))
        (last-update-time (oracle-last-update key))
        (timestamp (get timestamp resolution))
        (max-staleness (get max-staleness data)))

    ;; validate price and timestamp using max-staleness from oracle data
    (asserts! (and (oracle-price-legal final-price) (oracle-timestamp-fresh timestamp last-update-time max-staleness))
              ERR-ORACLE-INVARIANT)

    ;; update timestamp if newer
    (if (> timestamp last-update-time)
        (map-set last-update key timestamp)
        false)

    (ok final-price)))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L1238-1244)
```text
(define-public (borrow (ft <ft-trait>) (amount uint) (receiver (optional principal)) (price-feeds (optional (list 3 (buff 8192)))))
  (let ((address (contract-of ft))
        (asset (try! (get-asset address)))
        (asset-id (get id asset))
        (account contract-caller)
        (funds-receiver (match receiver recv recv contract-caller))
        (feeds-check (try! (write-feeds price-feeds)))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L1382-1391)
```text
(define-public (liquidate
                (borrower principal)
                (collateral-ft <ft-trait>)
                (debt-ft <ft-trait>)
                (debt-amount uint)
                (min-collateral-expected uint)
                (collateral-receiver (optional principal))
                (price-feeds (optional (list 3 (buff 8192)))))
  (let (
    (feeds-check (try! (write-feeds price-feeds)))
```

**File:** local-testing/tests/flows/borrow/borrow-basic.test.ts (L139-153)
```typescript
  it('should handle price updates correctly', async () => {
    // Scenario:
    // - Alice supplies 1 sBTC at $60,000
    // - Borrows $42,000 USDC (at 70% LTV limit)
    // - BTC price drops to $50,000
    // - Alice's position becomes unhealthy (now 84% LTV, exceeds 70%)
    // - Alice cannot borrow more
    
    // 1. Setup: Alice has 1 BTC collateral and borrows $42,000
    const sbtcAmount = 100000000n; // 1 BTC
    txOk(sbtcToken.mint(sbtcAmount, alice), deployer);
    txOk(market.collateralAdd(sbtcToken.identifier, sbtcAmount, null), alice);
    
    const initialBorrowAmount = 42000000000n; // $42,000 (70% of $60k)
    txOk(market.borrow(usdcToken.identifier, initialBorrowAmount, alice, null), alice);
```
