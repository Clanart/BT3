### Title
Single stale/broken oracle feed reverts health checks for a user's entire position, freezing withdraw/repay/borrow across all other healthy collateral - (File: mainnet/contracts/market/v0-4-market.clar)

### Summary
`get-assets` resolves prices for every enabled collateral/debt asset a user holds by calling `price-multi-resolve`, which internally `fold`s over `iter-price-multi` and marks the whole batch invalid if a single asset's `price-resolve` call fails (stale timestamp, zero/negative price, oracle call revert, confidence check failure). The caller then does `unwrap-panic` on the result, so any single broken/paused/stale oracle feed aborts price resolution for the *entire* position, not just the affected asset. [1](#0-0) [2](#0-1) 

### Finding Description
`get-assets` (used by any operation that needs to evaluate a user's collateral/debt notional value - borrow, withdraw, repay, liquidation, `collateral-remove`) builds the list of the user's enabled asset ids and calls `price-multi-resolve`: [1](#0-0) 

`price-multi-resolve` folds `iter-price-multi` over every asset's oracle config. `iter-price-multi` short-circuits (`asserts! valid acc`) once any single element fails, and the aggregate result is only unwrapped, otherwise `ERR-ORACLE-MULTI` is raised: [2](#0-1) 

Each per-asset resolution (`price-resolve`) enforces `oracle-price-legal` (price > 0) and `oracle-timestamp-fresh` (delta ≤ per-asset `max-staleness`, and monotonic vs. last seen timestamp) via a single `asserts!`: [3](#0-2) 

Because `get-assets` calls `unwrap-panic` on the multi-resolve result, if any one of the position's assets has a feed that is stale (Pyth/DIA hasn't been pushed within `max-staleness`), returns a non-positive price, or the external oracle call itself reverts (`ERR-ORACLE-PYTH`/`ERR-ORACLE-DIA`), the entire notional-value/health computation for the account reverts - even for the assets whose own prices are perfectly fresh and healthy. This directly mirrors the reported bug class: one unrelated dependency (adapter/oracle feed) being "broken or paused" blocks *all* other legitimate operations that would otherwise succeed.

### Impact Explanation
Any user who holds a multi-asset position (e.g., collateral in STX + sBTC + a debt in USDC) becomes unable to withdraw, repay, or top up collateral on their healthy assets the moment any single asset's price feed in their position mask goes stale or the oracle contract reverts. Since `is-healthy`/`is-healthy-with-mask` and all notional evaluation depend on `get-assets` succeeding for the full asset set, this is a protocol-wide DoS vector tied directly to the pricing/staleness-gating path (not to third-party price *correctness*, but to the code's all-or-nothing aggregation design). This results in temporary freezing of user funds (unable to withdraw/repay) until every feed in the mask is refreshed, which lands in the in-scope High impact category ("temporary freezing of funds").

### Likelihood Explanation
Likely to occur under normal operating conditions: Pyth/DIA feeds can lag publishing beyond `max-staleness` for less-liquid assets (e.g., stSTX, sBTC) even without any attack, and any transient revert from the external oracle contract call (`call-pyth`/`call-dia`) has the same fold/`unwrap-panic` effect. No privileged action or DAO misconfiguration is required - a single third-party feed's normal staleness is enough to freeze operations for users with unrelated healthy positions in other assets.

### Recommendation
Change the aggregation so a stale/failing feed only affects the specific asset it belongs to, not the whole batch:
- Have `iter-price-multi`/`price-multi-resolve` return per-asset success/failure (e.g., `optional uint` per entry) instead of aborting the whole fold on the first failure.
- In `get-assets`/notional evaluation, only require fresh prices for assets that are actually part of the user's non-zero collateral/debt list for the operation being performed (skip zero-balance assets), and/or allow withdraw/repay of an asset whose own price is fresh even if a different asset in the enabled bitmap is stale.
- Alternatively, wrap the failure in a `try`/graceful-degradation path so operations that don't need the broken asset's price (e.g., repaying debt, removing collateral that keeps the position over-collateralized) can still proceed.

### Proof of Concept
1. User has collateral in STX (fresh Pyth feed) and sBTC (feed goes stale past `max-staleness`, or the Pyth/DIA storage contract call reverts).
2. User calls any function that needs `get-assets` (e.g., `withdraw`/`collateral-remove`) to remove STX collateral while remaining healthy.
3. `get-assets` calls `price-multi-resolve` over `[STX-oracle, sBTC-oracle]`; `iter-price-multi` hits the sBTC entry, `price-resolve` fails `oracle-timestamp-fresh` and returns `ERR-ORACLE-INVARIANT`, marking the fold `valid: false`.
4. `price-multi-resolve` returns `ERR-ORACLE-MULTI`; `get-assets`'s `unwrap-panic` panics, reverting the entire transaction.
5. The user cannot withdraw their fresh, healthy STX collateral solely because the unrelated sBTC feed is stale - funds are frozen until the sBTC oracle is refreshed.

### Citations

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
