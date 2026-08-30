### Title
Any position operation reverts if a single unrelated collateral/debt asset's oracle price is stale or has low confidence - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
`get-assets` resolves the price of **every** enabled asset in a user's collateral/debt mask via `price-multi-resolve`, even when the operation the user is performing (borrow, repay, withdraw a specific asset) only concerns one of those assets. If price resolution fails for **any single asset** in the position — due to staleness gating, confidence gating, or an oracle call error — the entire batch resolution aborts and the whole user transaction reverts, blocking access to funds that have nothing to do with the failing feed. This mirrors the Canto `PostTxProcessing` bug class: an unrelated sub-operation's failure (there: CSR fee distribution; here: one stale/low-confidence price feed) improperly reverts a transaction that did not depend on it.

### Finding Description
`price-resolve` enforces staleness and confidence gating per asset and returns `ERR-ORACLE-INVARIANT` (or a feed error) if either check fails: [1](#0-0) 

`price-multi-resolve`/`iter-price-multi` fold over the **entire list** of assets relevant to a user's mask, and if resolution fails for even one entry, `valid` becomes `false` and the whole batch call fails with `ERR-ORACLE-MULTI`: [2](#0-1) 

`get-assets` — the function used to build asset/price context for health checks, borrow/withdraw eligibility, and liquidation math — calls `price-multi-resolve` for **all** enabled collateral/debt assets in the user's `user-safe-mask`, not just the asset being acted on, and uses `unwrap-panic`, which aborts the whole transaction on any error: [3](#0-2) 

So, analogous to the CSR `PostTxProcessing` hook that could revert transactions unrelated to the Turnstile contract because of a zero-fee edge case, here a user's operation on Asset A (e.g. `repay` USDC) can be reverted entirely because Asset B's price feed (e.g. an unrelated STX or sBTC collateral position enabled in the mask) is stale (staleness gating) or has low Pyth confidence (confidence gating) — conditions completely outside the user's control and unrelated to the asset they are interacting with.

### Impact Explanation
Because health-factor/position evaluation is a mandatory step for borrow, withdraw, and repay flows, a single degraded price feed for one asset in a user's multi-collateral position blocks all operations on the position, including withdrawing or repaying assets that have fresh, valid prices. This is a temporary freezing of user funds — users cannot access or manage their collateral/debt until the unrelated feed recovers, satisfying the "temporary freezing of funds" High-impact class.

### Likelihood Explanation
Staleness/confidence failures on third-party feeds (Pyth/DIA) are a routine operational occurrence (network delays, feed provider downtime, high volatility triggering wide confidence intervals), so any user holding more than one enabled collateral/debt type is exposed whenever one of their several feeds temporarily degrades, independent of which asset they intend to act on.

### Recommendation
Only resolve/require fresh prices for assets that are actually relevant to the specific operation being performed (or, for health checks, treat a stale/low-confidence individual feed as a soft failure that pauses only that asset's usability rather than aborting resolution for the whole position). Alternatively, catch per-asset resolution failures in `iter-price-multi` and propagate a structured per-asset error so callers can distinguish "asset irrelevant to this op failed" from "asset relevant to this op failed," instead of unconditionally failing the whole batch and panicking the transaction.

### Proof of Concept
1. Alice has collateral positions in both sBTC and STX, and has borrowed USDC.
2. Alice wants to `repay` her USDC debt — an operation whose economic validity depends only on the USDC price/oracle.
3. The market still calls `get-assets`, which calls `price-multi-resolve` over Alice's full enabled mask (sBTC, STX, USDC). [3](#0-2) 
4. If the STX Pyth feed happens to be stale beyond its `max-staleness` at that moment, `price-resolve` for STX returns `ERR-ORACLE-INVARIANT`. [4](#0-3) 
5. `iter-price-multi` marks the fold `valid: false`, `price-multi-resolve` asserts and returns `ERR-ORACLE-MULTI`. [2](#0-1) 
6. `get-assets`'s `unwrap-panic` on this error aborts Alice's entire `repay` transaction, even though it has nothing to do with STX pricing. [5](#0-4)

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
