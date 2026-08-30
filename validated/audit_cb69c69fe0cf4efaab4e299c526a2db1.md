I've identified a strong analog in `oracle-timestamp-fresh`, found identically in `mainnet/contracts/market/v0-4-market.clar` and duplicated in each vault-consuming path. This directly matches the report's "gate that silently neutralizes a bound check at a specific edge value" pattern — but instead of `previousDebt == 0` skipping a profit bound, here a future-dated feed timestamp forces `delta = u0`, which always satisfies the freshness bound `(<= delta max-staleness)` regardless of how stale/manipulated the actual price is.

### Title
Oracle Freshness Check Bypassed via Future-Dated Timestamp Forces `delta = u0` - ([File: mainnet/contracts/market/v0-4-market.clar])

### Summary
`oracle-timestamp-fresh` computes the staleness `delta` as `stacks-block-time - ts`, but when the feed's `ts` is (or appears to be) greater than `stacks-block-time`, the function short-circuits to `delta = u0` instead of rejecting the timestamp as invalid. This mirrors the reported bug class exactly: a bound-check guard is neutralized at a specific edge condition (there: `previousDebt == 0`; here: `ts > stacks-block-time`), causing the check to always pass instead of enforcing the intended deviation/freshness bound.

### Finding Description
`price-resolve` enforces price freshness via:
```
(define-private (oracle-timestamp-fresh (ts uint) (prev uint) (max-staleness uint))
  (let ((delta (if (> ts stacks-block-time)
                   u0
                   (- stacks-block-time ts))))
    (and
      (<= delta max-staleness)
      (>= ts prev))))
``` [1](#0-0) 

This is invoked from `price-resolve`, which gates every collateral/debt price used for LTV, borrow, withdraw, and liquidation health calculations:
```
(asserts! (and (oracle-price-legal final-price) (oracle-timestamp-fresh timestamp last-update-time max-staleness))
          ERR-ORACLE-INVARIANT)
``` [2](#0-1) 

Rather than treating `ts > stacks-block-time` as an anomaly to reject, the code coerces `delta` to `u0` — the "freshest possible" value — so the staleness bound `(<= delta max-staleness)` is guaranteed to pass no matter how the actual data was produced or relayed. The only remaining guard is the monotonic check `(>= ts prev)`, which a forward-dated timestamp trivially satisfies (it's larger than any prior `prev`). The `last-update` map is then advanced to this forward timestamp:
```
(if (> timestamp last-update-time)
    (map-set last-update key timestamp)
    false)
``` [3](#0-2) 

This permanently raises the "last seen" watermark for that feed identifier to a future value, meaning every subsequent legitimate price update — even one with an entirely correct, current publish-time — will now be rejected by the monotonicity check `(>= ts prev)`, since real-time `ts` values can never exceed the poisoned future watermark until real time catches up to it. This is a self-inflicted logic defect in `oracle-timestamp-fresh`/`price-resolve` within this repo, independent of whether the upstream Pyth/DIA data is otherwise "malicious" — it's this contract's handling of the edge case that is wrong.

### Impact Explanation
Any price resolution reachable from `collateral-add`, `borrow`, `collateral-remove`, and `liquidate` depends on `price-resolve` for USD notional valuation and health verdicts. A staleness bound that is unconditionally satisfied whenever a feed timestamp is (even marginally) ahead of `stacks-block-time` allows a stale or otherwise out-of-bound price update to be accepted as "freshest," directly corrupting the collateral/debt USD values feeding `is-healthy` and liquidation LTV comparisons — producing a wrong health verdict in the direction that benefits whichever position (borrower avoiding liquidation, or liquidator extracting excess collateral) the stale price favors. Because it also poisons the monotonic watermark, it can additionally cause legitimate subsequent price updates for that feed to be denied, freezing price updates for that asset until real time advances past the injected timestamp — a temporary freezing-of-funds condition for anyone relying on that asset's fresh pricing (deposits/borrows/liquidations against it effectively halt or use frozen/incorrect valuations).

### Likelihood Explanation
Triggering requires only that a price feed's `publish-time` exceed the Stacks chain's block-time — achievable via oracle clock skew, a manipulated/compromised relayer submitting a forward-dated update, or even benign timestamp jitter between the price source and `stacks-block-time`. There is no upper-bound ("future timestamp") rejection anywhere in `price-resolve`/`oracle-timestamp-fresh`, nor in the upstream `pyth-storage-v4` write path, which only checks a lower staleness bound, not an upper one. This makes the condition plausible without any DAO or key compromise.

### Recommendation
Reject timestamps ahead of `stacks-block-time` outright instead of clamping `delta` to `u0`:
```clarity
(define-private (oracle-timestamp-fresh (ts uint) (prev uint) (max-staleness uint))
  (and
    (<= ts stacks-block-time)
    (<= (- stacks-block-time ts) max-staleness)
    (>= ts prev)))
```
This ensures a future-dated timestamp fails the freshness check rather than being treated as maximally fresh, and prevents the monotonic watermark from being poisoned by an out-of-bounds value.

### Proof of Concept
1. A relayer submits (or an oracle clock-skew produces) a price update for feed `F` with `publish-time = T_future`, where `T_future > stacks-block-time` at submission.
2. `price-resolve` computes `delta = u0` via the `(> ts stacks-block-time)` branch in `oracle-timestamp-fresh`, so `(<= delta max-staleness)` passes trivially regardless of the actual configured `max-staleness`.
3. The monotonic check `(>= ts prev)` passes since `T_future` exceeds any previously recorded `prev`.
4. `map-set last-update key T_future` permanently raises the watermark for `F` to `T_future`.
5. The corrupted/stale price is used to compute collateral/debt USD notional in `collateral-add`/`borrow`/`liquidate`, producing an incorrect health verdict.
6. Until real Stacks block time surpasses `T_future`, every subsequent legitimate price update for `F` fails `(>= ts prev)` and is rejected with `ERR-ORACLE-INVARIANT`, freezing fresh pricing for that asset.

### Citations

**File:** mainnet/contracts/market/v0-4-market.clar (L365-371)
```text
(define-private (oracle-timestamp-fresh (ts uint) (prev uint) (max-staleness uint))
  (let ((delta (if (> ts stacks-block-time)
                   u0
                   (- stacks-block-time ts))))
    (and
      (<= delta max-staleness)
      (>= ts prev))))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L373-395)
```text
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
