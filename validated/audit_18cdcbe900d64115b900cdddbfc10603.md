### Title
Oracle price-freshness check treats a future-dated feed timestamp as maximally fresh, permanently defeating the staleness gate - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
The M-9 report describes a signature-verification gate (`signedOnly`) that never expires because no expiration timestamp is enforced, letting a stale authorization remain valid forever. The analogous defect in Zest's oracle price-resolution logic is in `oracle-timestamp-fresh`, the function that gates whether a freshly-read Pyth/DIA price is "fresh enough" and monotonic before it is accepted into the market's per-feed `last-update` state. Instead of enforcing a bounded, expiring freshness window against real elapsed time, the function silently converts a future-dated feed timestamp into "zero staleness," which — like the un-expiring signature — makes the freshness gate pass unconditionally once a large timestamp gets recorded, and then permanently blocks any further legitimate price update for that feed.

### Finding Description
`oracle-timestamp-fresh` is defined as: [1](#0-0) 

```
(define-private (oracle-timestamp-fresh (ts uint) (prev uint) (max-staleness uint))
  (let ((delta (if (> ts stacks-block-time)
                   u0
                   (- stacks-block-time ts))))
    (and
      (<= delta max-staleness)
      (>= ts prev))))
```

If the incoming feed timestamp `ts` is greater than the current `stacks-block-time`, `delta` is forced to `u0`, so the `<= delta max-staleness` clause is unconditionally true regardless of how far in the future `ts` is. This is called from `price-resolve`: [2](#0-1) 

```
(define-private (price-resolve
  (data { type: (buff 1), ident: (buff 32), callcode: (optional (buff 1)), max-staleness: uint }))
  (let ((type (get type data))
        ...
        (last-update-time (oracle-last-update key))
        (timestamp (get timestamp resolution))
        (max-staleness (get max-staleness data)))
    (asserts! (and (oracle-price-legal final-price) (oracle-timestamp-fresh timestamp last-update-time max-staleness))
              ERR-ORACLE-INVARIANT)
    (if (> timestamp last-update-time)
        (map-set last-update key timestamp)
        false)
    (ok final-price)))
```

Once a feed reports (or is manipulated to report) a `publish-time` greater than `stacks-block-time`, two things happen simultaneously:
1. The staleness gate is bypassed for that call (`delta = 0` always satisfies `max-staleness`), exactly like a signature with no expiration always satisfying "still valid."
2. `last-update` for that `{type, ident}` key is advanced to this future value, because `timestamp > last-update-time`.

On every subsequent call, the monotonicity clause `(>= ts prev)` now compares real, correctly-timed feed updates against this artificially inflated `prev`. Any legitimate price whose timestamp is less than the previously stored future value will fail `(>= ts prev)`, causing `ERR-ORACLE-INVARIANT` to fire and `price-resolve`/`price-multi-resolve` to abort for that asset — until real chain time catches up to the future timestamp that was recorded, which can be made effectively unbounded.

### Impact Explanation
This matches the "permanent freezing of funds" impact class: once a single future-dated timestamp is accepted for an asset's price feed, all subsequent legitimate price reads for that asset revert via `ERR-ORACLE-INVARIANT` in `price-resolve`/`price-multi-resolve`. Since price resolution feeds directly into collateral valuation, borrowing power, and liquidation checks in the market contract, this stalls deposits, borrows, redemptions, and liquidations that depend on that asset's price — freezing user funds tied to that market until the frozen timestamp is reached (which could be set arbitrarily far out). The direction of the error benefits no single counterparty financially (no direct theft), but it produces a denial-of-service on price-dependent operations for the affected asset, matching the "temporary/permanent freezing of funds" impact category, analogous to how the un-expiring `signedOnly` signature in the referenced report permanently locks in a state (a lifetime license) that cannot be corrected without an unrelated administrative workaround (there, rotating `SIGNER_ROLE`; here, waiting out the injected future timestamp).

### Likelihood Explanation
The exploitability depends on whether a future-dated `publish-time`/timestamp can reach `price-resolve` for a given feed — either through a manipulated relay/keeper call path, unusual but valid upstream data, or clock-skew edge cases in the Pyth/DIA reads that feed `resolve-pyth`/`resolve-dia`. The relevant question of whether this is reachable purely through Zest's own logic (not requiring compromised third-party oracle infrastructure) could not be fully confirmed in this review; the freshness/monotonicity bug itself is unambiguously present in `oracle-timestamp-fresh` in Zest's own code, but tracing the exact call path that supplies `ts`/`timestamp` and whether any upstream bound already prevents future-dated values from ever reaching this function was not verified before the tool budget was exhausted.

### Recommendation
Reject timestamps that are ahead of `stacks-block-time` (or bound the allowed forward skew tightly) instead of collapsing the delta to zero, e.g.:
```
(define-private (oracle-timestamp-fresh (ts uint) (prev uint) (max-staleness uint))
  (and
    (<= ts stacks-block-time)
    (<= (- stacks-block-time ts) max-staleness)
    (>= ts prev)))
```
This ensures a future-dated feed value cannot bypass the staleness window nor poison `last-update` with a value that later blocks legitimate updates.

### Proof of Concept
1. A price update for asset `X` is processed through `price-resolve` with a `timestamp` value greater than the current `stacks-block-time` (e.g., due to a malformed/miscalibrated but validly-signed VAA, or any code path that can inject such a value).
2. `oracle-timestamp-fresh` computes `delta = u0`, so `(<= delta max-staleness)` is `true`, and (assuming `ts >= prev`) the check passes.
3. `map-set last-update key timestamp` stores this future value as the new `last-update-time` for `X`.
4. Any subsequent, correctly-timed price update for `X` (with `timestamp < prev-future-value`) now fails `(>= ts prev)`, causing `ERR-ORACLE-INVARIANT`.
5. `price-resolve`/`price-multi-resolve` for `X` reverts on every call until real chain time surpasses the previously injected future timestamp, freezing all price-dependent operations (borrow, deposit, liquidation checks) that require `X`'s price. [3](#0-2)

### Citations

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
