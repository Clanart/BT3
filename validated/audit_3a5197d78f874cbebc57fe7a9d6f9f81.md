### Title
Oracle staleness check is silently bypassed for feeds whose reported timestamp is ahead of `stacks-block-time` - ([File: mainnet/contracts/market/v0-4-market.clar])

### Summary
`oracle-timestamp-fresh` clamps the staleness delta to `u0` whenever the feed's reported timestamp is greater than the current `stacks-block-time`. This means the lower-bound "not too old" check is enforced, but there is no corresponding upper-bound check to reject or otherwise correctly handle timestamps that are ahead of the chain's own clock. Any feed update whose `timestamp` is (even slightly) greater than `stacks-block-time` is treated as maximally fresh regardless of how long it subsequently persists, and it is then durably recorded as the new `last-update` baseline that all future updates are compared against.

### Finding Description
`price-resolve` computes freshness via: [1](#0-0) 

```clarity
(define-private (oracle-timestamp-fresh (ts uint) (prev uint) (max-staleness uint))
  (let ((delta (if (> ts stacks-block-time)
                   u0
                   (- stacks-block-time ts))))
    (and
      (<= delta max-staleness)
      (>= ts prev))))
```

When `ts > stacks-block-time` (the oracle-reported timestamp is ahead of the chain's block time — a routine occurrence given normal clock drift between the Pyth/DIA off-chain infrastructure and the Stacks chain's block timestamp), `delta` is forced to `u0` instead of the actual (negative) difference. `<= delta max-staleness` is then trivially true, so the staleness gate for that feed becomes a no-op for as long as the feed's timestamp remains ≥ `stacks-block-time`.

`price-resolve` then persists this timestamp as the new low-water mark: [2](#0-1) 

The monotonic check `(>= ts prev)` compounds the issue: once a feed has recorded a `last-update` value that is ahead of `stacks-block-time`, any legitimately-timed subsequent update (whose real-world timestamp is naturally below the previously stored, artificially-advanced value) is rejected outright with `ERR-ORACLE-INVARIANT`, because it fails `(>= ts prev)`. This is the exact class of bug described in the report: a check exists to reject values that are "too early" (too stale/too old), but nothing bounds or correctly handles values on the other side of the window (timestamps ahead of the chain clock), so the gate is either silently defeated (freshness never re-validated) or, once real time catches up, the price for that asset becomes permanently unresolvable.

### Impact Explanation
- While the discrepancy persists (`ts > stacks-block-time`), `price-resolve` for that asset never actually re-checks staleness — a genuinely stale price can be served for as long as the timestamp stays ahead of the chain clock, and this feeds directly into collateral valuation and LTV/health-factor checks used for borrow, withdraw, and liquidation decisions elsewhere in `v0-4-market.clar`.
- If a feed's reported timestamp ever exceeds `stacks-block-time` by a meaningful margin (network latency, off-chain clock skew, or an oracle publishing ahead of chain time), every subsequent legitimate (correctly-timed) update for that asset will fail the monotonic `(>= ts prev)` check and revert with `ERR-ORACLE-INVARIANT`, freezing price resolution — and therefore all operations gated on that asset's price (borrow, withdraw, liquidate) — until real time on-chain passes the erroneous stored value.
- This lands on **Critical** (protocol insolvency / stale-price based over-borrowing while the gate is bypassed) or, at minimum, **High** (temporary freezing of funds for the affected asset once the monotonic check starts rejecting legitimate updates).

### Likelihood Explanation
No attacker action or oracle compromise is required — normal clock skew between the off-chain oracle publisher and the Stacks chain's `stacks-block-time` is sufficient to trigger the clamp-to-zero branch. The bug is entirely in Zest's own freshness-gating logic (`oracle-timestamp-fresh` inside `v0-4-market.clar`), not in third-party oracle correctness, so it is in-scope per the rules governing this class of issue.

### Recommendation
Do not clamp `delta` to `u0` when `ts > stacks-block-time`. Either compute the absolute difference (`(if (> ts stacks-block-time) (- ts stacks-block-time) (- stacks-block-time ts))`) and bound it by `max-staleness` in both directions, or explicitly reject any `ts` that exceeds `stacks-block-time` (plus a small allowed clock-skew tolerance) with a dedicated error, mirroring the recommendation in the source report to add the missing boundary check rather than allowing the gate to be silently satisfied.

### Proof of Concept
1. A Pyth/DIA update is written whose `publish-time` (converted to the feed's timestamp) is, for example, 30 seconds ahead of the current `stacks-block-time` (plausible under normal operating conditions given oracle/chain clock differences).
2. `price-resolve` calls `oracle-timestamp-fresh timestamp last-update-time max-staleness`; since `ts > stacks-block-time`, `delta` is `u0`, so `(<= delta max-staleness)` passes unconditionally and `(>= ts prev)` also passes (assuming it's the newest seen).
3. `map-set last-update key timestamp` stores this ahead-of-time value as the new baseline.
4. Any further real-time price update from the oracle whose actual timestamp is still less than the previously stored future value fails `(>= ts prev)` and reverts with `ERR-ORACLE-INVARIANT`, blocking price resolution for that asset until on-chain time naturally advances past the erroneous timestamp — during which time the stale, already-fetched price for that asset continues to be treated as valid wherever it was cached/used, and once real time passes it, the asset's price feed is unusable until a fresh update with a timestamp ≥ the stored future value arrives.

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

**File:** mainnet/contracts/market/v0-4-market.clar (L382-395)
```text
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
