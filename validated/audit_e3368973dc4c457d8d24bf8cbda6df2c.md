### Title
Future-dated oracle publish-time permanently locks the per-feed monotonic timestamp, freezing price updates - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
`oracle-timestamp-fresh` mis-handles the case where a price feed's `publish-time` is ahead of `stacks-block-time`: instead of rejecting or properly bounding a suspiciously future timestamp, it coerces the staleness `delta` to `u0` ("perfectly fresh"). Combined with the per-feed monotonicity requirement `(>= ts prev)`, a single future-dated update permanently raises the stored `last-update` watermark for that feed, after which every subsequent, correctly-timed price update fails the monotonic check and reverts with `ERR-ORACLE-INVARIANT` until real chain time catches up to the erroneous future value.

### Finding Description
`price-resolve` gates every price read behind `oracle-timestamp-fresh`: [1](#0-0) 

```
(define-private (oracle-timestamp-fresh (ts uint) (prev uint) (max-staleness uint))
  (let ((delta (if (> ts stacks-block-time)
                   u0
                   (- stacks-block-time ts))))
    (and
      (<= delta max-staleness)
      (>= ts prev))))
```

When `ts > stacks-block-time`, `delta` is forced to `u0`, so the staleness bound `(<= delta max-staleness)` is trivially satisfied no matter how far in the future `ts` is. This result feeds directly into the caller: [2](#0-1) 

```
(define-private (price-resolve ...)
  (let (... (last-update-time (oracle-last-update key)) (timestamp (get timestamp resolution)) ...)
    (asserts! (and (oracle-price-legal final-price) (oracle-timestamp-fresh timestamp last-update-time max-staleness))
              ERR-ORACLE-INVARIANT)
    (if (> timestamp last-update-time)
        (map-set last-update key timestamp)
        false)
    (ok final-price)))
```

Because the future `ts` passes `oracle-timestamp-fresh` and is greater than the stored `last-update-time`, it is written into the `last-update` map for that `{type, ident}` key. Every later call for the same feed must satisfy `(>= ts prev)` where `prev` is now this artificially future value. Any subsequent price update with a genuine, current `publish-time` (which will be less than the erroneous future watermark) fails the monotonic check and the whole call reverts with `ERR-ORACLE-INVARIANT`, blocking `borrow`, `repay`, `collateral-add`, and `liquidate` paths that need to price that asset — this is the same file/function pattern flagged in `docs/oracle.md`'s "monotonic per-feed timestamp" freshness design.

The same logic exists identically in the local-testing mirror: [3](#0-2) .

### Impact Explanation
Once the watermark is poisoned, the affected feed's price cannot be refreshed by legitimate updates until wall-clock/chain time passes the erroneous future timestamp. During that window:
- Any position using that asset as collateral or debt cannot be borrowed against, repaid, or liquidated cleanly, because price resolution for that asset reverts — a temporary freeze of funds/operations tied to that asset.
- If the freeze coincides with a market downturn, unhealthy positions collateralized in the frozen asset become unliquidatable while the market is unable to react to falling asset value, which can escalate the temporary freeze into bad debt / insolvency risk for the vault backing that asset.

This satisfies the in-scope "temporary freezing of funds" impact class, with potential escalation toward protocol insolvency depending on how long the watermark stays poisoned and market conditions during that window.

### Likelihood Explanation
The trigger only requires one price update to be observed with a `publish-time` greater than the contract's view of `stacks-block-time` — achievable through normal clock skew between the Pyth attestation network and the Stacks chain's block-time oracle, or via a validly-signed Pyth update relayed with a timestamp slightly ahead of the local chain clock. No compromise of Pyth's signing keys or DAO registry is required; the root cause is this contract's own faulty handling of the future-timestamp branch, not bad data itself, so it isn't excluded as third-party oracle fault.

### Recommendation
Do not silently clamp `delta` to `0` when `ts > stacks-block-time`. Instead, either reject timestamps that exceed `stacks-block-time` by more than a small explicit tolerance (treating them as invalid/stale rather than automatically fresh), or bound how far the `last-update` watermark can be advanced beyond the current block time so a single anomalous future timestamp cannot permanently outrun subsequent legitimate updates.

### Proof of Concept
1. A Pyth price update for feed `X` is relayed with `publish-time = stacks-block-time + N` (any `N > 0`), which is accepted by `pyth-storage-v4` as a validly signed attestation.
2. `resolve-pyth` returns this `timestamp`; `price-resolve` calls `oracle-timestamp-fresh(ts, prev, max-staleness)`.
3. Since `ts > stacks-block-time`, `delta = u0`, so `(<= delta max-staleness)` is true; `(>= ts prev)` is true because `ts` is new/greater. The check passes.
4. `last-update` for `{type, ident}` is set to `ts` (the future value).
5. A later, correctly-timed price update with `publish-time = current stacks-block-time (< ts)` now fails `(>= ts prev)` inside `oracle-timestamp-fresh`, causing `price-resolve` to hit `ERR-ORACLE-INVARIANT`.
6. All market operations that call `price-resolve`/`price-multi-resolve` for that asset (borrow, repay, collateral-add, liquidate) revert until `stacks-block-time` naturally advances past the poisoned `ts`.

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

**File:** local-testing/contracts/market/market.clar (L304-320)
```text
;; -- Oracle: external price feeds -------------------------------------------

(define-private (normalize-pyth (p int) (expo int))
  (let ((adj (+ expo 8))
        (inkind? (asserts! (not (is-eq adj 0)) (to-uint p)))
        (res (if (> adj 0)
                (* p (pow 10 adj))
                (/ p (pow 10 (- adj))))))
    (to-uint res)))

(define-private (check-confidence (price int) (confidence uint))
  (ok (asserts! (<= confidence (/ (* (to-uint price) (var-get max-confidence-ratio)) BPS)) ERR-PRICE-CONFIDENCE-LOW)))

(define-private (call-pyth (ident (buff 32)))
  ;; @mainnet: (let ((res (unwrap! (contract-call? 'SP1CGXWEAMG6P6FT04W66NVGJ7PQWMDAC19R7PJ0Y.pyth-storage-v4 get-price ident) ERR-ORACLE-PYTH)))
  (let ((res (unwrap! (contract-call? .pyth-storage-v4 get-price ident) ERR-ORACLE-PYTH)))
    (ok res)))
```
