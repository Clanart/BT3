## Analysis [1](#0-0) 

The Sherlock report concerns a windowed time check (`isInCreationWindow`) that silently defaults to a "pass" value for edge cases (missing records return `0`, causing the boundary check to succeed when it shouldn't) instead of properly rejecting the anomalous input. The closest analog in Zest's pricing path is the staleness/freshness gate used on every oracle price resolution.

### Root cause

`oracle-timestamp-fresh` in `v0-4-market.clar` (identical logic in `local-testing/contracts/market/market.clar`) computes the staleness delta as: [1](#0-0) 

```clarity
(define-private (oracle-timestamp-fresh (ts uint) (prev uint) (max-staleness uint))
  (let ((delta (if (> ts stacks-block-time)
                   u0
                   (- stacks-block-time ts))))
    (and
      (<= delta max-staleness)
      (>= ts prev))))
```

When the oracle-reported `publish-time` (`ts`) is greater than the current `stacks-block-time`, `delta` is unconditionally clamped to `u0` rather than being computed, rejected, or bounded. Since `u0 <= max-staleness` is always true, the price is treated as **maximally fresh no matter how far in the future `ts` claims to be** — the staleness gate becomes a no-op in that branch. This mirrors the "for non-existent/edge-case timestamps the check silently defaults to pass without validation" root cause from the report, except here the edge case is "reported timestamp ahead of local block time" rather than "unconfigured market-times slot".

This function is invoked directly inside the price-resolution path used for every collateral/debt valuation: [2](#0-1) 

```clarity
(asserts! (and (oracle-price-legal final-price) (oracle-timestamp-fresh timestamp last-update-time max-staleness))
          ERR-ORACLE-INVARIANT)
;; update timestamp if newer
(if (> timestamp last-update-time)
    (map-set last-update key timestamp)
    false)
```

Once a future-dated `timestamp` is accepted and written to `last-update`, subsequent price resolutions for that feed keep comparing against `stacks-block-time`, which lags behind the stored future `last-update`/`ts`. As long as chain time has not caught up to that future-labeled `ts`, **every subsequent freshness check for that feed hits the `delta = u0` branch and passes unconditionally**, regardless of how outdated the underlying market price actually is in real terms. This can persist for as long as the gap between the reported timestamp and real chain time remains.

### Direction of error / who profits

The price accepted after this point is stale-but-treated-as-fresh, and can diverge in either direction from the true market price:
- If the frozen price is higher than the real price for a collateral asset, borrowers can over-borrow / avoid liquidation against overvalued collateral.
- If the frozen price is lower than the real price for a debt asset, similar mispriced-health-factor outcomes occur, letting under-collateralized positions look healthy and skip liquidation, or letting liquidators seize more/less collateral than warranted.

Either way, the health-factor and liquidation logic downstream (`price-resolve` → position health checks) operates on a price that is no longer tied to real time, which is the same "wrong calculation from a validation gap" class as the original finding.

### Note on root-cause classification

This does depend on the oracle publishing a `publish-time` slightly or significantly ahead of `stacks-block-time`. This is not a hypothetical/malicious-only scenario — cross-chain oracle publish times (Pyth/DIA) are set by the source publisher's clock, and this is exactly the type of natural clock-skew edge case the rules ask to be routed through the "confidence or staleness gating" analog. The defect is squarely in Zest's own `oracle-timestamp-fresh` comparison logic (the fallback-to-`u0` branch), not in oracle data content — the same bug exists even for a perfectly legitimate feed if its publish-time is momentarily ahead of the Stacks block clock.

### Title
Staleness gate degrades to a no-op when oracle timestamp is ahead of `stacks-block-time` — `oracle-timestamp-fresh` (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
`oracle-timestamp-fresh` clamps the staleness delta to `u0` whenever the reported price timestamp exceeds the current `stacks-block-time`, instead of rejecting or safely handling that case, which lets an effectively stale price be treated as perpetually fresh for as long as chain time has not caught up.

### Finding Description
In `price-resolve`, freshness is enforced solely via `oracle-timestamp-fresh` [1](#0-0) . The `(if (> ts stacks-block-time) u0 ...)` branch means any timestamp greater than the local block time is treated as zero delta — always within `max-staleness`. Once such a timestamp is recorded via `map-set last-update key timestamp` [3](#0-2) , all subsequent calls for that feed keep hitting the same always-pass branch until chain time catches up, meaning the staleness protection is bypassed for an unbounded window rather than the intended `max-staleness` seconds.

### Impact Explanation
Since every collateral/debt valuation and liquidation health check flows through `price-resolve`, an unvalidated stale-but-"fresh" price can allow under-collateralized positions to be seen as healthy (skipping liquidation) or allow borrowing against a stale favorable price, leading to temporary freezing/mispricing of funds and potential bad debt exposure until the price catches up — falling under "temporary freezing of funds" / protocol insolvency risk categories.

### Likelihood Explanation
Requires only a normal clock-skew condition between the oracle publisher's clock and Stacks block time (not attacker control of the price value), which is a plausible, recurring operational condition rather than an exotic edge case.

### Recommendation
Reject timestamps greater than `stacks-block-time` (or clamp/bound the allowable future drift explicitly, e.g. compare against a small tolerance) instead of silently mapping them to `delta = u0`; ensure the monotonic/staleness check cannot be satisfied by a value that has not actually been re-verified against current time.

### Proof of Concept
1. Oracle (Pyth/DIA) reports `publish-time = T` where `T` is slightly greater than current `stacks-block-time` (a normal, non-malicious clock-skew condition).
2. `price-resolve` accepts it: `oracle-timestamp-fresh` computes `delta = u0` (future branch), passes `<= max-staleness`, and `last-update` is set to `T`.
3. In subsequent blocks, while actual `stacks-block-time` remains `< T`, any call to `price-resolve` for that feed again lands in the `ts > stacks-block-time` branch (since stored `last-update`/`ts` still exceeds current block time), so `delta` is again `u0` and the check trivially passes — even though real elapsed time since the price was genuinely current may exceed `max-staleness`.
4. Health-factor/liquidation logic downstream in the market contract consumes this "fresh" but effectively stale price, producing an incorrect health verdict.

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
