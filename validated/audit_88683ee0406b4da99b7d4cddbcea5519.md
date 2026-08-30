### Title
Future-dated oracle timestamp bypasses staleness gating in `oracle-timestamp-fresh` - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
`market.clar` implements its own freshness gate on top of the Pyth/DIA feeds it reads. That gate silently treats any oracle timestamp greater than the current `stacks-block-time` as having zero age, which removes the staleness bound entirely for such prices, mirroring the reported class of bug: a temporal-ordering check that is supposed to reject "invalid/expired" state but instead lets it through unconditionally.

### Finding Description
`oracle-timestamp-fresh` computes the staleness delta like this: [1](#0-0) 

```clarity
(define-private (oracle-timestamp-fresh (ts uint) (prev uint) (max-staleness uint))
  (let ((delta (if (> ts stacks-block-time)
                   u0
                   (- stacks-block-time ts))))
    (and
      (<= delta max-staleness)
      (>= ts prev))))
```

When the feed-reported `ts` (Pyth `publish-time`, or DIA timestamp divided into seconds) is ahead of `stacks-block-time`, `delta` is forced to `u0`, which always satisfies `(<= delta max-staleness)`. This is the same failure mode reported in the analog: rather than reverting when the temporal state is invalid (here: a price timestamp that is inconsistent with chain time), the code special-cases it into an always-passing branch instead of rejecting it, exactly as `increaseQuestDuration()` allowed operating on an already-expired quest instead of reverting.

This check is invoked from `price-resolve`, which every collateral/debt valuation and liquidation path depends on: [2](#0-1) 

The upstream `pyth-storage-v4.write-batch-entry` only bounds `publish-time` from below (rejecting values that are too old relative to `latest-stacks-timestamp - stale-price-threshold`); it never bounds `publish-time` from above, so a `publish-time` set arbitrarily far in the future is accepted into storage: [3](#0-2) 

Once such a value is stored, `market.clar`'s own `oracle-timestamp-fresh` will treat that price as fresh with zero staleness for as long as `ts > stacks-block-time` holds — i.e., until chain time catches up to the (attacker- or bug-influenced) future timestamp — completely defeating the `max-staleness` bound that is supposed to gate every price used in collateral/debt valuation, liquidation math, and health checks.

### Impact Explanation
Because `price-resolve` feeds directly into `calculate-asset-notional-value`, `get-asset-value`, and the liquidation flow (`calc-final-liquidation-amounts`, `scale-debt-for-liquidation`), a price that should have been rejected as stale/invalid can instead be accepted as perpetually "fresh," allowing borrowing, withdrawal, or liquidation avoidance against a frozen/incorrect valuation. This can result in the protocol carrying insolvent positions valued at a stale price it believed was current, i.e., protocol insolvency / bad debt — a Critical-class impact per the rules, since it stems from a logic flaw in this code's own staleness-gating implementation, not simply from bad third-party data being trusted at face value (the code has a dedicated in-contract validation branch that is itself broken).

### Likelihood Explanation
Triggering it does not require compromising governance or the DAO registries: it only requires one Pyth (or DIA) update whose reported timestamp is ahead of the Stacks block's `stacks-block-time` — plausible under normal clock skew between the Pyth network and Stacks block timestamps, and not prevented anywhere in the write path (`pyth-storage-v4` has no upper-bound check on `publish-time`). Because `stacks-block-time` on Stacks can lag behind real-world/off-chain publish times, this condition is realistically reachable without any malicious actor, and once it occurs the resulting "always fresh" state persists for the entire skew window.

### Recommendation
In `oracle-timestamp-fresh`, do not special-case `ts > stacks-block-time` into `delta = 0`. Either revert immediately when `ts > stacks-block-time` (future timestamps are invalid) or clamp using `abs(ts - stacks-block-time)` so staleness is still enforced symmetrically, matching how Paladin's fix for the analog rejected operating on an out-of-bounds temporal state rather than papering over it.

### Proof of Concept
1. A Pyth (or DIA) price update is written into `pyth-storage-v4`/`dia-oracle` with `publish-time` slightly ahead of the current `stacks-block-time` (permitted because `write-batch-entry` only checks a lower staleness bound, not an upper one). [3](#0-2) 
2. `market.clar` calls `price-resolve`, which calls `resolve-pyth`/`resolve-dia` and then `oracle-timestamp-fresh(ts, last-update-time, max-staleness)`. [4](#0-3) 
3. Since `ts > stacks-block-time`, `delta` is forced to `u0`, so `(<= delta max-staleness)` is always true regardless of how large `max-staleness` actually needed to be, and the price is accepted as fresh. [1](#0-0) 
4. Any position valuation, borrow, or liquidation call using this asset's price during the skew window uses this artificially "fresh" price, bypassing the intended staleness protection.

### Citations

**File:** mainnet/contracts/market/v0-4-market.clar (L312-330)
```text
(define-private (resolve-pyth (ident (buff 32)))
  (let ((response (try! (call-pyth ident)))
        (price (get price response))
        (expo (get expo response))
        (conf (get conf response))
        (final-price (normalize-pyth price expo))
        (timestamp (get publish-time response)))
    (try! (check-confidence price conf))
    (ok { value: final-price, timestamp: timestamp })))

(define-private (call-dia (key (string-ascii 32)))
  (let ((res (unwrap! (contract-call? 'SP1G48FZ4Y7JY8G2Z0N51QTCYGBQ6F4J43J77BQC0.dia-oracle get-value key) ERR-ORACLE-DIA)))
    (ok res)))

(define-private (resolve-dia (ident (buff 32)))
  (let ((key (unwrap-panic (from-consensus-buff? (string-ascii 32) ident)))
        (res (try! (call-dia key))))
    ;; DIA returns timestamp in milliseconds, convert to seconds for staleness check
    (ok { value: (get value res), timestamp: (/ (get timestamp res) u1000) })))
```

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

**File:** local-testing/contracts/pyth/contracts/pyth-storage-v4.clar (L84-91)
```text
	(let ((stale-price-threshold (contract-call? .pyth-governance-v3 get-stale-price-threshold))
			(latest-stacks-timestamp (unwrap! (get-stacks-block-info? time (- stacks-block-height u1)) ERR_STALE_PRICE))
			(publish-time (get publish-time entry)))
		;; Ensure that we have not processed a newer price
		(asserts! (is-price-update-more-recent (get price-identifier entry) publish-time) ERR_NEWER_PRICE_AVAILABLE)
		;; Ensure that price is not stale
		(asserts! (>= publish-time (+ (- latest-stacks-timestamp stale-price-threshold) STACKS_BLOCK_TIME)) ERR_STALE_PRICE)
		;; Update storage
```
