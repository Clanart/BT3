<br>

Based on the investigation, I found a valid analog that fits the required bug class (exponent/sign conversion) and impact category (temporary freezing of funds).

### Title
Unvalidated negative Pyth price causes uncatchable runtime panic in price normalization, freezing all market operations - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
`normalize-pyth` in `v0-4-market.clar` converts the raw Pyth `price` (typed as signed `int`, per `pyth-traits-v2.clar`) directly into a `uint` via `to-uint`, both on the "no-adjustment" fast path and on the general path, without ever checking the sign first. In Clarity, `to-uint` on a negative `int` triggers a runtime error (an un-catchable abort, not a `(err ...)` `Response`), exactly analogous to the CVE-2020-35909 multihash bug where a decode function whose signature promised a `Result` instead panicked on malformed/unsanitized input.

### Finding Description
`pyth-storage-v4.clar`'s `write-batch-entry` stores whatever signed `price: int` value the relayer submits, only validating recency (`is-price-update-more-recent`) and staleness (`ERR_STALE_PRICE`) — never that `price > 0`: [1](#0-0) 

The market's oracle-resolution path then feeds that raw signed `price` straight into `normalize-pyth`: [2](#0-1) 

Both branches of this function call `to-uint` on a value that can be negative: the early-return branch calls `(to-uint p)` directly when `expo == -8` (adj == 0), and the general branch calls `(to-uint res)` where `res = p * 10^adj` or `p / 10^(-adj)`, which stays negative whenever `p` is negative. `to-uint` on a negative `int` is a Clarity runtime error that aborts the entire transaction — it cannot be caught by `try!`/`unwrap!`/`asserts!` because it never reaches the Response layer.

Crucially, the only positivity check in the codebase, `oracle-price-legal`, runs only *after* `resolve-pyth` (and therefore `normalize-pyth`) has already executed: [3](#0-2) [4](#0-3) 

So the intended guard (`(> p u0)`) can never actually fire for a negative price — the panic in `to-uint` happens first, unconditionally, for any negative Pyth price on any feed used by the protocol (STX, BTC, USDC per `PYTH-STX`/`PYTH-BTC`/`PYTH-USDC`). Since `price-resolve` is the single chokepoint used by every collateral/debt valuation, borrow, withdraw, repay, and liquidation flow, a negative price for *any* configured feed halts all of those operations across the entire market until a new, positive price is published.

### Impact Explanation
This is a temporary freezing-of-funds condition: while the affected feed carries a negative `price` value, every user operation that touches `price-resolve` (borrow, withdraw, repay, liquidation, health checks) reverts with an uncontrolled runtime abort rather than a graceful error, for as long as the stale/negative value remains the latest stored entry (bounded by staleness thresholds and by when a corrective update arrives). This blocks liquidations too, which can compound bad-debt risk during the outage window.

### Likelihood Explanation
Triggering this requires only that a value with `price < 0` be written into `pyth-storage-v4`'s `prices` map for a feed the market consumes — nothing in `write-batch-entry` or the market's `price-resolve` validates sign before the unguarded `to-uint` conversion runs. The `price` field's type is explicitly signed (`price: int`) in `pyth-traits-v2.clar`'s trait definitions, so the code path is reachable by design, not merely hypothetical, and the defensive check meant to catch it (`oracle-price-legal`) is provably too late to help.

### Recommendation
In `normalize-pyth`, validate `(>= p 0)` (and `(>= res 0)` after scaling) before any `to-uint` call, returning a proper `(err ...)` via `asserts!`/`unwrap!` instead of allowing an unchecked signed-to-unsigned conversion, mirroring the fix pattern used for the multihash advisory (validate untrusted input length/shape before the panics-on-bad-input operation, and return a `Result`/`Response` error instead).

### Proof of Concept
1. A relayer submits a `write` batch to `pyth-storage-v4` for the STX/USD feed (`PYTH-STX`) with `price: -1`, `expo: -8`, and a `publish-time` that passes the recency/staleness checks in `write-batch-entry` — nothing rejects a negative `price`.
2. Any user calls a market function that resolves the STX price (e.g., borrow, withdraw, repay, or a liquidation attempt) which internally calls `price-resolve` → `resolve-pyth` → `normalize-pyth(-1, -8)`.
3. `adj = expo + 8 = 0`, so the fast-path `(to-uint p)` runs with `p = -1`; Clarity aborts the transaction with a runtime error instead of returning `ERR-*`.
4. All operations touching the STX feed (or any other configured feed under the same condition) revert this way until a subsequent positive price update overwrites the map entry, freezing user funds tied to that market for the duration.

### Citations

**File:** local-testing/contracts/pyth/contracts/pyth-storage-v4.clar (L74-102)
```text
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
		;; Update storage
		(map-set prices 
			(get price-identifier entry) 
			{
				price: (get price entry),
				conf: (get conf entry),
				expo: (get expo entry),
				ema-price: (get ema-price entry),
				ema-conf: (get ema-conf entry),
				publish-time: publish-time,
				prev-publish-time: (get prev-publish-time entry)
			})
```

**File:** mainnet/contracts/market/v0-4-market.clar (L297-303)
```text
(define-private (normalize-pyth (p int) (expo int))
  (let ((adj (+ expo 8))
        (inkind? (asserts! (not (is-eq adj 0)) (to-uint p)))
        (res (if (> adj 0)
                (* p (pow 10 adj))
                (/ p (pow 10 (- adj))))))
    (to-uint res)))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L312-320)
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
```

**File:** mainnet/contracts/market/v0-4-market.clar (L362-388)
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
```
