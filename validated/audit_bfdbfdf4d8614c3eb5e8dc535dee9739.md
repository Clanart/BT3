### Title
Single stale/invalid price feed among a user's enabled collateral/debt assets panics price resolution for the entire position, freezing withdrawals of otherwise-healthy collateral - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
`get-assets`, the function that resolves USD prices for every asset in a user's position mask, propagates a single failed price resolution into an `unwrap-panic` that aborts the *entire* call — including for all of the user's other, perfectly healthy collateral assets. This mirrors the BakerFi `MultiStrategyVault` finding: one paused/unavailable component (there, a strategy; here, one asset's oracle feed) blocks withdrawal/collateral-removal for everything else in the same batched operation, with no mechanism to exclude the failing asset.

### Finding Description
`get-assets` builds the price-resolution batch for every collateral asset enabled in a user's mask and calls `price-multi-resolve`, whose result is unwrapped with `unwrap-panic`: [1](#0-0) 

`price-multi-resolve` folds `iter-price-multi` over the asset list; as soon as one `price-resolve` call fails (stale timestamp, illegal price, oracle-call error, callcode/index-cache failure), `valid` flips to `false` and stays `false` for the rest of the fold, then the function returns `ERR-ORACLE-MULTI`: [2](#0-1) 

`price-resolve` itself asserts freshness/legality per feed via `oracle-price-legal` and `oracle-timestamp-fresh`, reverting the whole resolution with `ERR-ORACLE-INVARIANT` on failure of just that one feed: [3](#0-2) 

Because `get-assets` wraps `price-multi-resolve` in `unwrap-panic` rather than propagating the error gracefully or excluding the offending asset, any downstream caller of `get-assets` — `collateral-remove` (has-debt branch), `borrow`, and any other health-check path — panics for the entire transaction the moment one enabled collateral/debt asset's price feed is stale or otherwise invalid: [4](#0-3) 

There is no per-asset exclusion mechanism analogous to the "temporarily set weight to 0" idea from the external report: the health-check batch always includes every enabled asset in the user's mask, and a single failure is fatal to the whole computation.

### Impact Explanation
If any one asset in a user's mask (e.g. a Pyth feed that goes stale due to sequencer/publisher lag, or a paused/misbehaving DIA feed) fails freshness or legality checks, the user cannot call `collateral-remove` to withdraw *any* of their collateral, nor `borrow`, as long as that asset remains in their enabled mask and its price stays unresolvable — even though their other collateral assets have perfectly healthy, live prices. This is a temporary freezing of funds (the user's other, healthy collateral becomes inaccessible) that persists until the stuck feed resolves. It does not steal funds or misprice a position; it lands in the temporary-freezing-of-funds impact class, directly parallel to the referenced BakerFi report where withdrawal is blocked across the whole vault by one paused strategy.

### Likelihood Explanation
Price feed staleness/failure is a normal operational occurrence (oracle publisher delays, network congestion, or an oracle being paused/misconfigured upstream) rather than a rare edge case, and any user holding more than one enabled collateral/debt asset type is affected as soon as one of them stalls — no attacker action is required, only an external oracle hiccup on any single supported asset.

### Recommendation
Do not let a single asset's price failure abort resolution for the whole position. Either (a) allow `get-assets`/`price-multi-resolve` to skip/exclude an asset with a failing price from the health computation when it isn't the asset being withdrawn (analogous to temporarily zero-weighting a paused strategy), or (b) allow users to remove/disable a specific problematic collateral asset from their mask without requiring a full price-resolution pass over all other assets, so operations on remaining, healthy collateral are not blocked by one bad feed.

### Proof of Concept
1. User has two enabled collateral assets, A (fresh price) and B (whose Pyth/DIA feed becomes stale beyond `max-staleness`, or the upstream oracle is paused/errors).
2. User calls `collateral-remove` on asset A while having debt; the has-debt branch calls `get-assets position-mask` which includes both A and B in the price-resolution batch.
3. `price-resolve` for B fails `oracle-timestamp-fresh`/`oracle-price-legal`, `iter-price-multi` marks `valid: false`, `price-multi-resolve` returns `ERR-ORACLE-MULTI`, and `get-assets`'s `unwrap-panic` aborts the whole transaction.
4. The user cannot withdraw asset A, whose price is fine, purely because unrelated asset B's feed is stalled — funds in A are frozen until B's oracle recovers.

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

**File:** mainnet/contracts/market/v0-4-market.clar (L1118-1134)
```text
    (if has-debt
        ;; HAS DEBT: Full flow with price resolution and health checks
        (let ((is-collateral-enabled (get collateral asset))
              (feeds-check (try! (write-feeds price-feeds)))
              (position-mask (get mask position))
              (pos-full (if is-collateral-enabled position (try! (get-full-position account))))
              (u-debt (accrue-user-debts (get debt pos-full)))
              (u-coll (accrue-user-collateral (get collateral pos-full)))
              (assets (get-assets position-mask))
              (curr-coll-aid (find-collateral-amount (get collateral position) asset-id))
              (removing-all (is-eq amount curr-coll-aid))
              (current-group (try! (get-egroup position-mask)))
              (current-ltvb (buff-to-uint-be (get LTV-BORROW current-group)))
              (notional-valued-assets (get-notional-evaluation { position: position, assets: assets }))
              (collateral-value (get collateral notional-valued-assets))
              (debt-value (get debt notional-valued-assets))
              (removed-asset-value (find-and-resolve-asset-value assets asset-id amount true)))
```
