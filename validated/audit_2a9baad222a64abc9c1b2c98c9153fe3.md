### Title
Future-dated oracle timestamp bypasses staleness gating and can permanently freeze price updates for an asset - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
The staleness-gating helper `oracle-timestamp-fresh`, used by `price-resolve` to validate every oracle price before it is used for collateral/debt valuation and health checks, treats any oracle timestamp that is greater than the current `stacks-block-time` as automatically fresh (`delta = u0`), with no upper bound on how far in the future that timestamp can be. Once such a timestamp is accepted, it is written into the monotonic `last-update` map, and every subsequent legitimate (correctly-timed) price update will be rejected because its real timestamp is smaller than the injected future value, permanently freezing further price updates for that asset until real time catches up.

### Finding Description
`price-resolve` is the central price-valuation entry point used for every collateral/debt calculation and liquidation health check: [1](#0-0) 

It gates every incoming price/timestamp pair with `oracle-timestamp-fresh`: [2](#0-1) 

```clarity
(define-private (oracle-timestamp-fresh (ts uint) (prev uint) (max-staleness uint))
  (let ((delta (if (> ts stacks-block-time)
                   u0
                   (- stacks-block-time ts))))
    (and
      (<= delta max-staleness)
      (>= ts prev))))
```

When `ts` (the price feed's reported timestamp) is greater than `stacks-block-time` (the current chain time), `delta` is forced to `u0`, so the `<= delta max-staleness` check always passes **regardless of how far in the future `ts` is**. There is no upper-bound / sanity check rejecting timestamps that are unreasonably ahead of the current block time. This is analogous to the reported bug class: a time-boundary meant to gate an action (here, freshness of a price used for solvency/liquidation math) has an unguarded escape path (any future-dated value), exactly like the missing restriction on `processCommitment` that let a supposedly time-limited action execute outside its intended window.

The consequence compounds because the accepted future timestamp is persisted as the new monotonic floor: [3](#0-2) 

```clarity
    (if (> timestamp last-update-time)
        (map-set last-update key timestamp)
        false)
```

Once `last-update` for a `{type, ident}` key is set to a future value, every subsequent legitimate price update with a correct, real-time timestamp will fail the `(>= ts prev)` monotonic check in `oracle-timestamp-fresh`, because the real timestamp will always be smaller than the previously stored future one — the price for that asset can never be refreshed until real `stacks-block-time` catches up to the erroneously stored future value.

### Impact Explanation
This lands in the "temporary freezing of funds" category. Once a single out-of-bounds future timestamp is accepted for a feed (whether via a misbehaving/compromised upstream oracle relay, clock skew, or a bug in the feed's transport), `price-resolve` for that asset will revert with `ERR-ORACLE-INVARIANT` for all subsequent, correctly-timed calls until real chain time passes the erroneous future value. Because `price-resolve`/`price-multi-resolve` underlie collateral valuation, debt valuation, borrowing, repaying, and liquidation health checks, this can freeze all lending operations that require pricing that asset (deposits, withdrawals, borrows, and liquidations) for the duration of the injected future offset, which is attacker/oracle controllable and can be arbitrarily large.

### Likelihood Explanation
The likelihood depends on how much the upstream feed transport (Pyth `pyth-storage-v4`/DIA) constrains publish times before they reach `resolve-pyth`/`resolve-dia`. `resolve-dia` converts a millisecond timestamp to seconds without any check that it isn't wildly out of range, and Pyth's storage `write` path only bounds staleness on the "old" side, not a maximum-future bound, so a single anomalous update (misconfiguration, relay bug, or bridge compromise) is sufficient to trigger the freeze — there is no defense-in-depth cap in `oracle-timestamp-fresh` itself.

### Recommendation
Bound the future-dated case: cap `ts` at `stacks-block-time` (or reject it outright) rather than silently accepting it as `delta = u0`, e.g.:
```clarity
(asserts! (<= ts (+ stacks-block-time SOME_SMALL_CLOCK_SKEW_TOLERANCE)) ERR-ORACLE-INVARIANT)
```
so that only a small, bounded clock-skew tolerance is allowed and the monotonic `last-update` map can never be poisoned with an arbitrarily large future value.

### Proof of Concept
1. A price feed (Pyth relay or DIA) reports (or is coerced into reporting) a timestamp `ts` far in the future relative to `stacks-block-time` for asset `X`.
2. `price-resolve` calls `oracle-timestamp-fresh(ts, prev, max-staleness)`; since `ts > stacks-block-time`, `delta` is forced to `u0`, so the staleness check passes trivially, and `(>= ts prev)` also passes since `ts` is large.
3. `price-resolve` writes `map-set last-update {type, ident} ts` with the future value.
4. On the next legitimate call, the real oracle timestamp `ts'` (correctly close to current `stacks-block-time`) is now less than the stored `prev = ts`, so `(>= ts' prev)` fails, and `price-resolve` reverts with `ERR-ORACLE-INVARIANT` for every call referencing asset `X` until real chain time surpasses the injected `ts`, freezing borrowing, repayment, and liquidation flows dependent on `X`'s price.

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
