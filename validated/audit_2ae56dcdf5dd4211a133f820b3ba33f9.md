### Title
Future oracle timestamp permanently poisons the monotonic `last-update` tracker, causing denial-of-service on price resolution - (File: mainnet/contracts/market/v0-4-market.clar)

### Summary
`v0-4-market.clar` tracks a per-feed monotonic timestamp (`last-update`) to reject replayed/out-of-order oracle prices, similar in spirit to the reported bug class where an unvalidated `ExecutionPayload.Timestamp` could be set arbitrarily far in the future and permanently break the chain's ability to progress. In `oracle-timestamp-fresh`, a price timestamp that is greater than the current `stacks-block-time` (i.e., a future timestamp) is never rejected — instead the staleness delta is forced to `0`, which always passes the `<= delta max-staleness` check, and that future timestamp is then persisted into `last-update` as the new monotonic floor.

### Finding Description
`oracle-timestamp-fresh` is defined as: [1](#0-0) 

```clarity
(define-private (oracle-timestamp-fresh (ts uint) (prev uint) (max-staleness uint))
  (let ((delta (if (> ts stacks-block-time)
                   u0
                   (- stacks-block-time ts))))
    (and
      (<= delta max-staleness)
      (>= ts prev))))
```

When the feed's reported timestamp `ts` is *ahead* of `stacks-block-time`, the function sets `delta` to `u0` rather than rejecting or clamping the value. `0 <= max-staleness` is always true, so any future-dated timestamp is unconditionally treated as "fresh" — there is no upper bound check (no analog of "less than cometBFT time plus minimum slot time" from the reported fix). This mirrors the reported flaw where `ExecutionPayload.Timestamp` was validated only against the past (parent block) and never against an upper bound.

That accepted future timestamp then flows into `price-resolve`: [2](#0-1) 

```clarity
(define-private (price-resolve
  (data { type: (buff 1), ident: (buff 32), callcode: (optional (buff 1)), max-staleness: uint }))
  (let (...
        (last-update-time (oracle-last-update key))
        (timestamp (get timestamp resolution))
        ...)
    (asserts! (and (oracle-price-legal final-price) (oracle-timestamp-fresh timestamp last-update-time max-staleness))
              ERR-ORACLE-INVARIANT)
    (if (> timestamp last-update-time)
        (map-set last-update key timestamp)
        false)
    (ok final-price)))
```

Once a future timestamp passes the check, it is written into the `last-update` map (line 391-393) as the new floor for that `{type, ident}` feed. From that point on, `oracle-timestamp-fresh` also requires `(>= ts prev)` — any subsequent, entirely legitimate price update whose `publish-time` is a normal, non-future value will now be `< prev` (the poisoned future value) and fail this check, permanently reverting with `ERR-ORACLE-INVARIANT` for that asset/feed until real chain time catches up to the erroneously stored future timestamp — which for a sufficiently large value can take a very long time (e.g. `MaxUint64`-style extreme values would never be caught up to).

The upstream oracle storage (`pyth-storage-v4.clar`) only enforces a *lower* bound on `publish-time` (must not be older than `stale-price-threshold`) via `write-batch-entry`, with no upper bound either: [3](#0-2) 

so a timestamp that is ahead of the current `stacks-block-time` (which can legitimately drift from wall-clock/Pyth publish time, or be pushed further via malicious/faulty relaying) is not filtered out before reaching the market contract's own (flawed) freshness gate.

### Impact Explanation
Because `price-resolve`/`price-multi-resolve` back every price-dependent operation in the market (deposits requiring health checks, borrows, withdrawals, liquidations, bad-debt socialization), poisoning `last-update` for a given feed effectively bricks price resolution for that asset. Any user action needing that asset's price — supplying/withdrawing collateral in that asset, borrowing/repaying debt denominated in it, or liquidating positions holding it — will revert with `ERR-ORACLE-INVARIANT`, freezing users' ability to interact with their positions in that asset until the on-chain `stacks-block-time` naturally advances past the poisoned value (which could be made arbitrarily large). This is a temporary freezing-of-funds impact on collateral/debt tied to the affected feed.

### Likelihood Explanation
The value only needs to exceed `stacks-block-time` once for a given feed's price update to be accepted and stored; there is no upper-bound validation anywhere in the chain from Pyth/DIA ingestion (`pyth-storage-v4.clar`) through to `oracle-timestamp-fresh` in the market contract. Given real-world clock drift between off-chain publish times and on-chain block time, or a relayer/oracle glitch producing a timestamp ahead of the chain, this condition is plausible without requiring any privileged access or DAO misconfiguration — it is a logic gap in this contract's own freshness/monotonicity check.

### Recommendation
`oracle-timestamp-fresh` should reject (not silently zero) timestamps greater than `stacks-block-time` plus some small allowed clock-skew tolerance, instead of collapsing `delta` to `0`. Concretely:
```clarity
(define-private (oracle-timestamp-fresh (ts uint) (prev uint) (max-staleness uint))
  (and
    (<= ts (+ stacks-block-time ALLOWED-SKEW))
    (<= (if (> ts stacks-block-time) u0 (- stacks-block-time ts)) max-staleness)
    (>= ts prev)))
```
This ensures a future-dated timestamp is rejected outright rather than accepted as maximally fresh and persisted as the new monotonic floor.

### Proof of Concept
1. An oracle price update (Pyth or DIA) is relayed with `publish-time`/`timestamp` set slightly (or arbitrarily) ahead of the current `stacks-block-time` for a given feed.
2. `price-resolve` calls `oracle-timestamp-fresh timestamp last-update-time max-staleness`; since `ts > stacks-block-time`, `delta` becomes `u0`, which is `<= max-staleness`, and `ts >= last-update-time` (assuming it's the first/newest seen), so the check passes.
3. `map-set last-update key timestamp` stores the future timestamp as the new floor for that `{type, ident}` key.
4. On the next legitimate price update for the same feed with a normal (non-future) `publish-time`, `(>= ts prev)` fails because `prev` is now the previously injected future value, causing `price-resolve` to return `ERR-ORACLE-INVARIANT`.
5. Every subsequent call to `price-resolve`/`price-multi-resolve` for that asset fails until real `stacks-block-time` surpasses the poisoned future value, blocking borrows, withdrawals, and liquidations involving that asset.

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

**File:** local-testing/contracts/pyth/contracts/pyth-storage-v4.clar (L84-90)
```text
	(let ((stale-price-threshold (contract-call? .pyth-governance-v3 get-stale-price-threshold))
			(latest-stacks-timestamp (unwrap! (get-stacks-block-info? time (- stacks-block-height u1)) ERR_STALE_PRICE))
			(publish-time (get publish-time entry)))
		;; Ensure that we have not processed a newer price
		(asserts! (is-price-update-more-recent (get price-identifier entry) publish-time) ERR_NEWER_PRICE_AVAILABLE)
		;; Ensure that price is not stale
		(asserts! (>= publish-time (+ (- latest-stacks-timestamp stale-price-threshold) STACKS_BLOCK_TIME)) ERR_STALE_PRICE)
```
