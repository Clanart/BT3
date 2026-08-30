### Title
Same-block price arbitrage via optional feed updates and non-strict monotonic timestamp check - ([File: mainnet/contracts/market/v0-4-market.clar])

### Summary
Zest's market contract lets every state-changing entrypoint (deposit/borrow/repay/withdraw/liquidate) accept an *optional* list of Pyth price-feed updates, which the internal `write-feeds` helper simply skips when omitted [1](#0-0) . Price freshness is enforced only through a per-feed "monotonic" timestamp check, `oracle-timestamp-fresh`, that requires the new publish-time to be `>=` the last one seen and within `max-staleness` [2](#0-1) . This check does not force price consistency across separate calls within the same block — it only rejects timestamps older than the previous one already consumed. A user can therefore call an operation without pushing a fresh Pyth update (using the currently stored, still "fresh-enough" older price), and later in the same block push the real updated price and call the opposite operation, extracting value from the difference exactly as in the referenced PythAdapter finding.

### Finding Description
`price-resolve` fetches the current stored Pyth price via `call-pyth`/`resolve-pyth`, applies `check-confidence`, and validates it with:
```clarity
(asserts! (and (oracle-price-legal final-price) (oracle-timestamp-fresh timestamp last-update-time max-staleness)) ERR-ORACLE-INVARIANT)
(if (> timestamp last-update-time) (map-set last-update key timestamp) false)
``` [3](#0-2) 

`oracle-timestamp-fresh` only asserts `delta <= max-staleness` and `ts >= prev`:
```clarity
(define-private (oracle-timestamp-fresh (ts uint) (prev uint) (max-staleness uint))
  (let ((delta (if (> ts stacks-block-time) u0 (- stacks-block-time ts))))
    (and (<= delta max-staleness) (>= ts prev))))
``` [4](#0-3) 

Because real Pyth publish-times are naturally non-decreasing, both an "old-but-still-fresh" price and a subsequently pushed newer price will each individually satisfy `ts >= prev`. The `last-update` map is only a floor against replaying *older* data — it is not a per-block price snapshot. Combined with the fact that the price-feed-update list passed to market operations is optional (`write-feeds` is a no-op when `none` is supplied) [1](#0-0) , and `pyth-storage-v4`'s own staleness window (`read-price-with-staleness-check`/`write-batch-entry`) permits a range of valid publish-times rather than a single canonical block price [5](#0-4) , a user retains full freedom to choose which of two (or more) legitimately-valid prices for the same asset to use in each of several calls within one block.

### Impact Explanation
An attacker can sequence two calls in the same block against the market: one using the stale-but-not-yet-expired stored price, and a second (after pushing the real update) using the fresh price, choosing the ordering that is most favorable to them for collateral valuation vs. debt valuation. This creates a risk-free arbitrage against protocol solvency — e.g., collateral is valued favorably in one leg and debt/withdrawal is valued favorably in the other leg — extracting value that was not actually backed by real price movement risk. This lands on protocol insolvency / theft of protocol funds, i.e., **Critical/High** impact per the defined categories, since it is not limited to yield but affects principal accounting.

### Likelihood Explanation
Likelihood is **Medium**: it requires a genuine, moderately-sized Pyth price movement to occur within a single Stacks block/staleness window and requires the attacker to control the update timing (which they can, since price-feed submission is optional and permissionless via `write-feeds`/`verify-and-update-price-feeds`). No privileged access or governance compromise is needed.

### Recommendation
Do not rely purely on a monotonic-timestamp floor. Cache the price used for a given feed at the first use within a block (keyed by `stacks-block-height`, not just by "later than last seen"), and force all price resolutions for that feed within the same block to use that cached value, or require that every state-mutating call must supply a price update no older than the current block before resolving, rejecting any stale-but-within-threshold price when a newer on-chain price already exists for that block.

### Proof of Concept
1. Block `b`: Pyth on-chain stored price for `WBTC/USD` = `$50,000`, timestamp `T1`, within `max-staleness`.
2. Off-chain Pyth price moves to `$51,000` but is not yet pushed on-chain.
3. Block `b+1`, Tx1: Alice calls a market entrypoint (e.g. `borrow`) with `feeds: none`; `price-resolve` uses stored `$50,000`/`T1`, passes `oracle-timestamp-fresh` (delta within staleness, `T1 >= prev`), sets `last-update = T1`.
4. Same block `b+1`, Tx2: Alice supplies a fresh Pyth VAA to `write-feeds`, storage now holds `$51,000`/`T2` (`T2 > T1`); Alice calls the opposite entrypoint (e.g. `withdraw`/`repay`); `price-resolve` uses `$51,000`/`T2`, passes `oracle-timestamp-fresh` (`T2 >= T1`), succeeds.
5. Because the two calls used two different, both individually "valid," WBTC prices in the same block, Alice realizes the price delta as risk-free profit, at the protocol's expense.

### Citations

**File:** local-testing/contracts/market/market.clar (L154-160)
```text
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

**File:** local-testing/contracts/pyth/contracts/pyth-storage-v4.clar (L52-90)
```text
(define-read-only (read-price-with-staleness-check (price-identifier (buff 32)))
	(let ((entry (unwrap! (map-get? prices price-identifier) ERR_PRICE_FEED_NOT_FOUND))
			(stale-price-threshold (contract-call? .pyth-governance-v3 get-stale-price-threshold))
			(latest-stacks-timestamp (unwrap! (get-stacks-block-info? time (- stacks-block-height u1)) ERR_STALE_PRICE)))
		(asserts! (>= (get publish-time entry) (+ (- latest-stacks-timestamp stale-price-threshold) STACKS_BLOCK_TIME)) ERR_STALE_PRICE)
		(ok entry)))

(define-public (write (batch-updates (list 64 {
		price-identifier: (buff 32),
		price: int,
		conf: uint,
		expo: int,
		ema-price: int,
		ema-conf: uint,
		publish-time: uint,
		prev-publish-time: uint,
	})))
	(let ((successful-updates (map unwrapped-entry (filter only-ok-entry (map write-batch-entry batch-updates)))))
		;; Ensure that updates are always coming from the right contract
		(try! (contract-call? .pyth-governance-v3 check-execution-flow contract-caller none))
		(ok successful-updates)))

(define-private (write-batch-entry (entry {
		price-identifier: (buff 32),
		price: int,
		conf: uint,
		expo: int,
		ema-price: int,
		ema-conf: uint,
		publish-time: uint,
		prev-publish-time: uint,
	}))
	(let ((stale-price-threshold (contract-call? .pyth-governance-v3 get-stale-price-threshold))
			(latest-stacks-timestamp (unwrap! (get-stacks-block-info? time (- stacks-block-height u1)) ERR_STALE_PRICE))
			(publish-time (get publish-time entry)))
		;; Ensure that we have not processed a newer price
		(asserts! (is-price-update-more-recent (get price-identifier entry) publish-time) ERR_NEWER_PRICE_AVAILABLE)
		;; Ensure that price is not stale
		(asserts! (>= publish-time (+ (- latest-stacks-timestamp stale-price-threshold) STACKS_BLOCK_TIME)) ERR_STALE_PRICE)
```
