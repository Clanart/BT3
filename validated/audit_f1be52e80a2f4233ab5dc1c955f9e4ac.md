### Title
Front-running the permissionless embedded `write-feeds` path lets a malicious user lock a stale monotonic oracle timestamp and block or manipulate another user's price-dependent operation - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
`price-resolve` gates every oracle read behind a **global, per-feed** monotonic timestamp stored in `last-update` (keyed only by `{type, ident}`, not by user or position) and enforces `>= ts prev`. This map is written by `write-feeds`/`write-feed`, which is invoked automatically, with attacker-supplied `price-feeds` bytes, from *every* public hot-path function (`collateral-add`, `collateral-remove`, `borrow`, `supply-collateral-add`, `liquidate`) — none of which restrict who may supply feed updates. Because the check/state is shared across all callers and all positions that reference the same feed, any unrelated user can front-run another user's transaction with a different (but individually valid) Pyth update for the same feed, advancing the global `last-update` value to a point that makes the victim's already-signed/broadcast update fail the `>= ts prev` monotonic check, reverting the victim's legitimate transaction (`ERR-ORACLE-INVARIANT`).

### Finding Description
`resolve-pyth`/`price-resolve` in `mainnet/contracts/market/v0-4-market.clar:312-395` computes freshness/monotonicity purely from the shared map: [1](#0-0) 

`last-update` is defined per-feed only, not per account/position: [2](#0-1) 

The write path that mutates this shared map is not itself access-controlled; `write-feeds`/`write-feed` simply fold over whatever `(buff 8192)` bundle the caller supplies and forward it to the Pyth verifier: [3](#0-2) 

Every state-changing, user-facing entrypoint that can affect health/price accepts this same optional `price-feeds` argument and calls `write-feeds` unconditionally before any per-caller authorization of the feed content: `collateral-add`, `collateral-remove`, `borrow`, `supply-collateral-add`, and `liquidate` all do this, e.g.: [4](#0-3) [5](#0-4) 

There is no check tying the `price-feeds` payload to the caller's own position, own asset, or transaction intent — any account can submit a valid VAA for *any* feed ID as a side effect of an otherwise unrelated call (e.g. calling `collateral-add` on a stablecoin asset while smuggling in a BTC feed update). This is structurally the same class of bug as the reported issue: a function meant only as internal support for the caller's own operation (there: `removeUserFromOrderbook`; here: `write-feeds`) is reachable/triggerable by *any* address and mutates shared state (`last-update`) that gates another user's pending, price-dependent transaction.

### Impact Explanation
Because `oracle-timestamp-fresh` requires `>= ts prev` against the *global* `last-update[type,ident]` value, a malicious actor Eve can:
1. Observe Alice's pending mempool transaction (e.g. `collateral-add`/`repay` carrying a Pyth update with publish-time `T_alice` for the BTC feed, submitted to keep her position healthy or out of a liquidation range).
2. Front-run with her own call to any hot-path function (`collateral-add`, `borrow`, etc.) carrying a different, independently valid Pyth VAA for the same feed with publish-time `T_eve > T_alice` (readily obtainable since Pyth issues continuously updated signed VAAs), which is accepted and advances `last-update[BTC]` to `T_eve`.
3. Alice's transaction then executes `price-resolve` with `T_alice < last-update[BTC]`, failing `(>= ts prev)`, causing `ERR-ORACLE-INVARIANT` and reverting Alice's entire transaction (collateral top-up, repay-and-avoid-liquidation, or borrow), while she loses the gas and, more importantly, the timing window to act.
4. This can be leveraged to force a wrong health verdict downstream: by controlling exactly which valid price snapshot becomes "the" last-update at a critical moment, Eve can also selectively let through a price snapshot that is favorable for herself (e.g. to satisfy her own liquidation call against Alice) while ensuring Alice's own attempt to refresh the price to a healthier reading is rejected as "stale relative to prev". The victim is left unable to save her position before a legitimate `liquidate` call executes against her, matching the temporary-freezing-of-funds impact class (inability to act on unclaimed/at-risk collateral during the contested window), and enabling an unjust liquidation driven by a manipulated ordering of otherwise-valid price updates.

### Likelihood Explanation
Any address can call `collateral-add`, `collateral-remove`, `borrow`, `supply-collateral-add`, or `liquidate` with an arbitrary `price-feeds` bundle at any time — there is no allow-list or binding between the submitted feed and the caller's own asset/position. Pyth continuously emits fresh signed VAAs for major feeds (BTC/STX/USDC), so an attacker has a steady supply of valid-but-different-timestamp updates to use for front-running. Mempool visibility on Stacks is sufficient to detect a victim's pending price-carrying transaction. This requires no oracle compromise, no DAO action, and no flashloan — just an unrelated, always-available public call.

### Recommendation
- Scope the monotonic freshness check and `last-update` state per-transaction/per-call rather than as global mutable contract state that any unrelated caller can advance, or
- Only allow the `price-feeds` parameter to update feeds that are actually required for the caller's own operation, and require the submitted publish-time to be at least the price actually used for that call's own health computation (not compared against a separately-controllable global "prev"), or
- Decouple health/liquidation decisions from the shared monotonically-increasing cache by re-deriving the price used for both accrual and eligibility checks in the same atomic step from the same feed read, so the ordering of unrelated third-party writes cannot invalidate a legitimate caller's own signed update.

### Proof of Concept
1. Alice holds a borrow position near the liquidation threshold and prepares `repay` (or `collateral-add`) with a `price-feeds` list containing a valid Pyth VAA with `publish-time = T1` for the BTC feed that reflects a price keeping her healthy.
2. Eve observes Alice's transaction in the mempool and immediately submits her own `collateral-add` (on any asset she owns, unrelated to Alice's position) with `price-feeds` containing a different, independently valid Pyth VAA for the same BTC feed with `publish-time = T2 > T1`.
3. Eve's transaction is mined first; `price-resolve` executes `(map-set last-update {type: TYPE-PYTH, ident: BTC_FEED} T2)` — see `mainnet/contracts/market/v0-4-market.clar:390-393`.
4. Alice's transaction is then mined; `price-resolve` calls `oracle-timestamp-fresh(T1, T2, max-staleness)`, and `(>= T1 T2)` is false, so the `asserts!` at `mainnet/contracts/market/v0-4-market.clar:387-388` fails with `ERR-ORACLE-INVARIANT`, reverting Alice's entire repay/collateral-add call.
5. Eve (or a colluding liquidator) then calls `liquidate` against Alice's still-unhealthy position, supplying whichever valid price snapshot is most favorable for triggering/maximizing the liquidation, since Alice's ability to refresh her own favorable reading has been blocked by the monotonic-timestamp race.

### Citations

**File:** mainnet/contracts/market/v0-4-market.clar (L117-120)
```text
;; -- Oracle timestamp tracking
(define-map last-update
  { type: (buff 1), ident: (buff 32) }
  uint)
```

**File:** mainnet/contracts/market/v0-4-market.clar (L126-152)
```text
;; -- Price feed update helpers ----------------------------------------------

;; Write a single Pyth price feed update using fold accumulator pattern
(define-private (write-feed (feed (buff 8192)) (status (response bool uint)))
  (match status
    success-status
      (match (contract-call? 'SP1CGXWEAMG6P6FT04W66NVGJ7PQWMDAC19R7PJ0Y.pyth-oracle-v4 verify-and-update-price-feeds
          feed
          {
            pyth-storage-contract: 'SP1CGXWEAMG6P6FT04W66NVGJ7PQWMDAC19R7PJ0Y.pyth-storage-v4,
            pyth-decoder-contract: 'SP1CGXWEAMG6P6FT04W66NVGJ7PQWMDAC19R7PJ0Y.pyth-pnau-decoder-v3,
            wormhole-core-contract: 'SP1CGXWEAMG6P6FT04W66NVGJ7PQWMDAC19R7PJ0Y.wormhole-core-v4,
          }
        )
        update-success (ok true)
        update-failed ERR-PRICE-FEED-UPDATE-FAILED)
    error-status status
  )
)

;; Process optional list of price feed updates
;; If list is provided, folds over it and updates all feeds
;; If list is none, does nothing (allows for backward compatibility)
(define-private (write-feeds (feeds (optional (list 3 (buff 8192)))))
  (match feeds
    entries (fold write-feed entries (ok true))
    (ok true)))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L365-395)
```text
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

**File:** mainnet/contracts/market/v0-4-market.clar (L1119-1122)
```text
        ;; HAS DEBT: Full flow with price resolution and health checks
        (let ((is-collateral-enabled (get collateral asset))
              (feeds-check (try! (write-feeds price-feeds)))
              (position-mask (get mask position))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L1389-1391)
```text
                (price-feeds (optional (list 3 (buff 8192)))))
  (let (
    (feeds-check (try! (write-feeds price-feeds)))
```
