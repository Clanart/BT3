### Title
Future-dated price timestamp bypasses staleness gating and permanently poisons the monotonic per-feed timestamp - ([File: mainnet/contracts/market/v0-4-market.clar])

### Summary
`oracle-timestamp-fresh` in `mainnet/contracts/market/v0-4-market.clar` collapses the staleness delta to `u0` whenever a feed's `publish-time` is greater than `stacks-block-time`, instead of rejecting or capping such an update. Combined with the per-feed monotonic timestamp tracked in `last-update`, a single future-dated price permanently raises the floor that all subsequent legitimate prices must clear, freezing that asset's oracle price forever — directly analogous to the amplifier bug where a boundary comparison flips permanently false and traps value.

### Finding Description
`price-resolve` gates every price on `oracle-timestamp-fresh`: [1](#0-0) 

```clarity
(define-private (oracle-price-legal (p uint))
  (> p u0))

(define-private (oracle-timestamp-fresh (ts uint) (prev uint) (max-staleness uint))
  (let ((delta (if (> ts stacks-block-time)
                   u0
                   (- stacks-block-time ts))))
    (and
      (<= delta max-staleness)
      (>= ts prev))))
```

`price-resolve` then persists whatever `ts` was validated as the new monotonic floor for that feed: [2](#0-1) 

If a feed update ever carries a `publish-time` greater than the current `stacks-block-time`, `delta` is forced to `u0`, so the `<= delta max-staleness` staleness check trivially passes no matter how far in the future the timestamp is — the staleness gate degenerates into a no-op for that update. Because `ts > last-update-time` also holds, `map-set last-update key timestamp` stores that inflated (future) timestamp as the new floor: [3](#0-2) 

From that point on, every subsequent legitimate update (with `ts` = real current time) must satisfy `>= ts prev`, but `prev` is now a timestamp from the future relative to actual chain time — so the monotonic check permanently fails and `price-resolve` reverts with `ERR-ORACLE-INVARIANT` for that feed on every call, exactly as the amplifier's `_mostRecentValueCalcTime` boundary check permanently flips to false once crossed and never recovers.

This mirrors the bug-class hint categories directly: "confidence or staleness gating" and "a monotonic per-feed timestamp." The root cause is in this contract's own comparison logic (`if (> ts stacks-block-time) u0 ...`), not in bad third-party data content — the flaw is that the protocol chose to *neutralize* the staleness check instead of rejecting an out-of-range timestamp, and the same value gets latched into the monotonic tracker with no upper bound or reasonableness check.

### Impact Explanation
Once a single future-dated update is accepted for a feed (whether from clock skew at the data source, a decoder/relay bug, or a compromised/misbehaving publisher relayed through `write-feed`), that asset's price resolution path (`price-resolve` / `price-multi-resolve`) permanently reverts with `ERR-ORACLE-INVARIANT`. Any market operation that requires pricing that asset (borrow, withdraw of collateral, liquidation, health checks) becomes permanently unusable, freezing users' ability to access or manage their positions in that asset — a freezing-of-funds impact.

### Likelihood Explanation
Requires a single anomalous `publish-time` greater than the current `stacks-block-time` to be accepted through `write-feed`/`resolve-pyth`/`resolve-dia` and stored via `map-set last-update`. Given normal clock-skew tolerances in oracle networks and no upper-bound / future-timestamp rejection anywhere in the reviewed path, this is plausible without requiring any privileged access or governance compromise.

### Recommendation
Do not zero out the staleness delta for `ts > stacks-block-time`. Either reject the update outright (`asserts! (<= ts stacks-block-time) ERR-ORACLE-INVARIANT`) or compute delta as an absolute difference and bound it symmetrically, and never persist a `last-update` timestamp that exceeds `stacks-block-time`, so a single anomalous future timestamp cannot become an unrecoverable floor for the monotonic check.

### Proof of Concept
1. A price feed update for asset X is submitted through `write-feed`/`resolve-pyth` with `publish-time = stacks-block-time + N` (future timestamp), passing through to `price-resolve`.
2. In `oracle-timestamp-fresh`, `ts > stacks-block-time` is true, so `delta = u0`, making `(<= delta max-staleness)` trivially true; `(>= ts prev)` is also true since `ts` is larger than any prior value. The update is accepted.
3. `price-resolve` executes `(if (> timestamp last-update-time) (map-set last-update key timestamp) false)`, storing the future timestamp as the new floor for that `{type, ident}` key.
4. On the next real update for the same feed, `ts` = actual current `publish-time` (≤ real `stacks-block-time` < the previously stored future floor), so `(>= ts prev)` fails.
5. `price-resolve` now asserts `ERR-ORACLE-INVARIANT` on every subsequent call for that asset, permanently blocking price resolution (and therefore borrow/withdraw/liquidation flows) for that asset.

### Citations

**File:** mainnet/contracts/market/v0-4-market.clar (L362-371)
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
