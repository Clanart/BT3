### Title
Single stale/invalid oracle feed reverts price resolution for *all* assets in a position, denying withdraw/borrow/liquidation - ([File: mainnet/contracts/market/v0-4-market.clar])

### Summary
`price-multi-resolve` batches oracle-price lookups for every enabled collateral/debt asset in a user's position via a `fold` over `iter-price-multi`. If a single feed fails its freshness or positivity check, the whole batch is marked invalid and the caller reverts with `ERR-ORACLE-MULTI`, even though the other N-1 feeds resolved successfully. This mirrors the Hubble `AMM.settleFunding` finding, where one "not-ready" item in a loop blocks processing of all the "ready" items — except here the blocked operation is price/health evaluation for a lending position rather than AMM funding settlement.

### Finding Description
`price-multi-resolve` accumulates results via `iter-price-multi`: [1](#0-0) 

Each element is resolved independently through `price-resolve`, which enforces per-feed freshness/positivity: [2](#0-1) 

The moment any single asset's price fails `oracle-timestamp-fresh` or `oracle-price-legal`, `iter-price-multi` sets `valid: false` in the fold accumulator; subsequent fold iterations short-circuit via `(asserts! valid acc)`, and `price-multi-resolve` asserts `(get valid response)`, reverting the *entire* call with `ERR-ORACLE-MULTI` — losing the successfully resolved prices for all other assets: [3](#0-2) 

`get-assets`, which drives the notional/health evaluation for a position, calls this batched resolver for the full set of the account's enabled collateral (and, transitively, debt) assets: [4](#0-3) 

Because the health/notional evaluation (`get-notional-evaluation` / `is-healthy`) requires prices for *every* asset the account touches in one shot, a single asset with a stale or momentarily-unavailable feed (e.g., a Pyth feed that hasn't been pushed within `max-staleness`, or a monotonic-timestamp check rejecting an out-of-order update) makes the whole multi-asset lookup fail. This blocks any operation that needs a position health check — withdraw, borrow, or liquidation — for that user, even for assets whose prices are perfectly fresh and unaffected.

### Impact Explanation
This is the same class of bug as the referenced report: an "all must be ready or none proceed" aggregation causes healthy/ready items to be blocked by one straggler. Here, a stale feed for one collateral/debt asset in a multi-asset position (e.g., 5 collateral + 3 debt types) makes `ERR-ORACLE-MULTI` propagate up, preventing the account from withdrawing collateral, being liquidated, or having any position action processed — even though only one specific asset's feed is affected. For an account approaching liquidation threshold, this can delay or block liquidation entirely while the debt position continues to deteriorate, and it can prevent a user from withdrawing unaffected collateral. This lands on temporary freezing of funds (Impact: High), since legitimate operations on unaffected assets are denied until the unrelated feed becomes fresh again.

### Likelihood Explanation
Likelihood is moderate: any account with several enabled/held assets is subject to this all-or-nothing check whenever it has to be evaluated for health. A single feed missing its `max-staleness` window (feed provider downtime, no updater transaction in time, or a monotonic-timestamp rejection due to out-of-order relay of Pyth updates) is a routine occurrence in oracle systems, not an exotic edge case, and does not require any third-party oracle misbehavior — it's purely the aggregation code path in `price-multi-resolve`/`iter-price-multi` that turns one stale feed into a total failure for the position.

### Recommendation
Do not fail the entire multi-price resolution when a single feed's price is stale/invalid. Instead of aggregating a single `valid` boolean across the whole fold and reverting the caller with `ERR-ORACLE-MULTI`, consider: (1) only requiring freshness for assets actually relevant to the specific operation (e.g., skip checking price for a zero-balance collateral/debt entry), or (2) returning a partial result that lets health-check/liquidation logic proceed using the assets that did resolve successfully, surfacing the stale asset separately rather than as a global abort.

### Proof of Concept
1. Account holds collateral in assets A, B, C and debt in asset D, all enabled.
2. Asset C's Pyth feed misses an update and its last publish timestamp exceeds its configured `max-staleness` (`oracle-timestamp-fresh` returns `false` at `mainnet/contracts/market/v0-4-market.clar:365-371`).
3. Account calls withdraw (of asset A, unrelated to C) → `get-assets` → `price-multi-resolve` iterates A, B, C, D; when it hits C, `iter-price-multi` sets `valid: false` (line 413-418).
4. `price-multi-resolve`'s `(asserts! (get valid response) ERR-ORACLE-MULTI)` (line 402) reverts the whole call, blocking the withdrawal of asset A even though A, B, and D's prices resolved without issue.
5. The same path blocks a liquidator from liquidating this account's debt in D, even if D's own price feed is perfectly fresh, because C's stale price aborts the shared multi-resolve used for the position's notional evaluation.

### Citations

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

**File:** mainnet/contracts/market/v0-4-market.clar (L397-418)
```text
(define-private (price-multi-resolve
  (data (list 64 { type: (buff 1), ident: (buff 32), callcode: (optional (buff 1)), max-staleness: uint }))
  (aids (list 64 uint)))
  (let ((init { output: (list), valid: true, aids: aids, idx: u0 })
        (response (fold iter-price-multi data init)))
    (asserts! (get valid response) ERR-ORACLE-MULTI)
    (ok (get output response))))

(define-private (iter-price-multi
  (oracle-data { type: (buff 1), ident: (buff 32), callcode: (optional (buff 1)), max-staleness: uint })
  (acc { output: (list 64 uint), valid: bool, aids: (list 64 uint), idx: uint }))
  (let ((valid (get valid acc))
        (skip? (asserts! valid acc))
        (asset-ids (get aids acc))
        (idx (get idx acc))
        ;; resolve price - will use cache for ztokens
        (price (unwrap! (price-resolve oracle-data) (merge acc { valid: false })))
        (next (unwrap-panic (as-max-len? (append (get output acc) price) u64))))
    { output: next,
      valid: true,
      aids: asset-ids,
      idx: (+ idx u1) }))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L482-492)
```text
(define-private (get-assets (mask-user uint))
  (let ((mask-enabled (get-enabled-bitmap))
        (safe-mask (user-safe-mask mask-user mask-enabled))
        (iter (mask-to-list-collateral safe-mask))
        (assets-list (get-status-multi iter))
        (oracles-list (map get-oracle assets-list))
        ;; Extract asset-ids for price resolution
        (asset-ids (map get-asset-id assets-list))
        ;; Use internal price resolution
        (prices-list (unwrap-panic (price-multi-resolve oracles-list asset-ids))))
    (map merge-price assets-list prices-list)))
```
