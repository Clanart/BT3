### Title
Monotonic oracle timestamp protection resets to zero on market contract upgrade, allowing replay of a stale-but-favorable price - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
`price-resolve` in the market contract gates every Pyth/DIA price update with two checks: a staleness check against the current block time, and a monotonicity check against a `last-update` value stored in the market contract itself. That `last-update` map is contract-local state with no explicit initializer, so whenever a new market contract is deployed to replace/upgrade the current one (the codebase already tracks versioned market contracts, e.g. `v0-4-market.clar`, and has an established pattern of publishing new contract versions such as the `pyth-oracle-v3`/`v4` upgrade plans), every feed's `last-update` entry starts back at the default `u0`. This is the same bug class as the reported `LivenessGuard` issue: a freshness/anchor value that is only meaningful on first deployment gets silently reset whenever the contract is redeployed, defeating the very protection it was built to provide.

### Finding Description
The market contract tracks the last accepted timestamp per oracle feed: [1](#0-0) 

and exposes it via a getter that defaults to `u0` when no entry exists: [2](#0-1) 

The freshness/anti-replay gate combines a staleness check (against `stacks-block-time`) with a monotonicity check (`>= ts prev`) that relies entirely on this stored `last-update` value: [3](#0-2) 

`price-resolve` uses `oracle-last-update` as `prev` and only advances `last-update` when the new timestamp is strictly greater, i.e. the map is meant to permanently "remember" the most recent price timestamp ever accepted for that feed: [4](#0-3) 

Exactly like the `LivenessGuard` constructor that unconditionally resets `lastLive[owner] = block.timestamp` for every owner on redeploy (undermining the `LivenessModule`'s inactivity tracking), the market contract's `last-update` map has no migration/carry-over logic: a freshly deployed market contract (whether a routine upgrade or an emergency replacement) starts every feed's `prev` at `u0`. Since `oracle-timestamp-fresh` only rejects a submitted timestamp if it is older than `prev` (not "older than the true most-recent price"), resetting `prev` to `0` makes the monotonicity guard a no-op for the first update after redeployment — the only surviving constraint is the staleness window against current block time. This means any previously-valid (and still historically signed) VAA/price feed message for that asset — including one representing a price that had already been superseded by a lower, more accurate value before the redeploy — can be replayed and accepted as long as its timestamp is within `max-staleness` seconds of "now".

### Impact Explanation
If a market contract redeploy/upgrade occurs shortly after a legitimate price drop was recorded (well within realistic operational cadence, since `max-staleness` windows described in the docs are on the order of 60–300 seconds), an attacker holding an older, more favorable signed price update can resubmit it immediately after the new contract goes live. Because `resolve-callcode`/`price-resolve` feed directly into collateral/debt valuation used for health checks and liquidation eligibility, this stale-but-accepted price would overstate collateral value or understate debt value, allowing over-borrowing against real collateral or blocking a liquidation that should otherwise succeed — leading to bad debt (protocol insolvency) or temporary freezing of a position that should be liquidatable.

### Likelihood Explanation
This requires (a) a market contract redeploy/upgrade, which the repository's versioned contract naming and upgrade-plan tooling shows is an expected, recurring operational event, and (b) an old signed price update whose timestamp still falls inside the current `max-staleness` window at the moment of redeploy. This is a narrow but realistic timing window rather than a routine occurrence, so likelihood is Low-to-Medium, contingent on upgrade timing coinciding with recent price volatility.

### Recommendation
When deploying a new market contract version, migrate the `last-update` map from the outgoing contract (or otherwise seed it) instead of leaving it empty, mirroring the fix recommended for `LivenessGuard`: initialize `last-update` for each tracked feed from the previous market contract's `oracle-last-update` value rather than implicitly defaulting to `u0`.

### Proof of Concept
1. Market v0-4 is live; feed `USDC/USD` price drops and is recorded, setting `last-update[{type, ident}] = T_drop`.
2. Governance deploys market v0-5 (upgrade/replacement). The new contract's `last-update` map has no entry for `USDC/USD`, so `oracle-last-update` returns `u0`.
3. Within `max-staleness` seconds of "now" (but using a timestamp `T_old < T_drop` from a previously valid, still-verifiable Pyth/DIA update reflecting the pre-drop higher price), a user calls the market function that triggers `price-resolve` with that older feed data.
4. `oracle-timestamp-fresh(T_old, u0, max-staleness)` returns true because `T_old >= 0` and `T_old` is within the staleness window of current time, so `ERR-ORACLE-INVARIANT` is not raised.
5. The higher, stale price is accepted and used for collateral valuation, letting the user borrow more than their true collateral value supports or evade liquidation that should trigger under the correct (dropped) price.

### Citations

**File:** mainnet/contracts/market/v0-4-market.clar (L117-119)
```text
;; -- Oracle timestamp tracking
(define-map last-update
  { type: (buff 1), ident: (buff 32) }
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

**File:** mainnet/contracts/market/v0-4-market.clar (L939-940)
```text
(define-read-only (oracle-last-update (f {type: (buff 1), ident: (buff 32)}))
  (default-to u0 (map-get? last-update f)))
```
