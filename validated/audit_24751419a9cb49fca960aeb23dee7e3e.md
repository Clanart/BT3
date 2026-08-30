### Title
Monotonic oracle timestamp protection resets to zero on market contract redeployment, enabling stale price replay - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
`v0-4-market.clar`'s `last-update` map, which enforces monotonicity of per-feed oracle timestamps, is a fresh, empty map local to each deployed market contract. The file name itself (`v0-4-market.clar`) and the historical `v0-1-data.clar` asset-iteration helpers confirm that the market contract has already gone through multiple versions/redeployments (v0-1 → v0-4). Every time the market contract is replaced, `last-update` starts empty, so `oracle-last-update` returns `u0` for every feed until fresh writes occur, silently discarding the anti-replay history the previous market instance had built up — the same class of bug reported against `LivenessGuard`'s constructor resetting `lastLive` on every redeploy.

### Finding Description
`price-resolve` gates every accepted price on two checks combined in `oracle-timestamp-fresh`: [1](#0-0) 

```clarity
(define-private (oracle-timestamp-fresh (ts uint) (prev uint) (max-staleness uint))
  (let ((delta (if (> ts stacks-block-time) u0 (- stacks-block-time ts))))
    (and
      (<= delta max-staleness)   ;; not too old relative to now
      (>= ts prev))))            ;; not older than a previously-seen update
```

`prev` is read via `oracle-last-update`, which simply defaults to `u0` when the map has no entry for that feed: [2](#0-1) 

and the map itself, `last-update`, is a contract-local map declared fresh in this file: [3](#0-2) 

`price-resolve` uses these together to reject stale/out-of-order oracle updates and only advances `last-update` when a strictly newer timestamp is observed: [4](#0-3) 

This is precisely the pattern flagged in the `LivenessGuard` report: a security-critical piece of state (`lastLive` there, `last-update` here) is meant to reflect the *history* of prior activity, but is re-initialized to a "no history" default (`block.timestamp` for all owners there; `u0` for all feeds here) every time the contract holding it is redeployed. When market.clar is replaced — evidenced by the contract already being on its 4th version (`v0-4-market.clar`) — the new instance's `last-update` map has no entries, so `(>= ts prev)` is satisfied by *any* timestamp `ts >= 0`. The monotonicity guard, whose stated purpose is to "reject prices with timestamps older than previously seen values," provides zero protection until the new contract has independently observed at least one update per feed, and even then, an attacker's first call for a given feed on the new contract can supply any historical publish-time that still satisfies the unrelated freshness delta check (`delta <= max-staleness`).

### Impact Explanation
An attacker (or a permissionless caller relaying feed updates, since Pyth updates are pushed via `verify-and-update-price-feeds` and DIA reads on demand) can, immediately after a market redeployment, submit the most favorable historical price update within the `max-staleness` window instead of the current one, because the anti-replay ordering check has been reset. Depending on direction:
- Supplying an artificially high stale collateral price lets a borrower over-borrow against collateral, directly creating bad debt / protocol insolvency.
- Supplying an artificially low stale debt-asset price lets a borrower under-collateralize a new debt position.
- Supplying a favorable stale price versus the price a liquidator/legit caller expects can distort liquidation outcomes.

Because this can result in borrowing beyond real collateral value (protocol insolvency) or manipulated liquidation proceeds, the impact falls into the Critical/High bands (protocol insolvency, theft) defined in scope.

### Likelihood Explanation
This occurs deterministically every time the market contract is redeployed/upgraded (already demonstrated to have happened at least 4 times per the `v0-4` naming), for every registered oracle feed, until each feed independently accumulates a fresh update on the new contract. No privileged access is required to exploit it — any account can call the public borrow/liquidate paths that trigger `price-resolve` with a stale-but-still-fresh-enough Pyth/DIA update.

### Recommendation
When deploying a new market contract version, migrate the prior contract's `last-update` values into the new contract's map (e.g., via a one-time DAO-authorized initialization call that reads each feed's last known timestamp from the outgoing market contract, analogous to the LivenessGuard fix of seeding `lastLive` from `prevGuard`), rather than letting the map start empty. Alternatively, persist `last-update` in a separate, non-redeployed storage contract that market.clar calls into, so monotonicity history survives market contract upgrades.

### Proof of Concept
1. Market contract `v0-3-market` has processed feed `STX-FEED-ID` up to `publish-time = T2` (the highest seen), and rejects anything with `publish-time < T2`.
2. DAO deploys `v0-4-market.clar` to fix an unrelated bug. Its `last-update` map is empty; `oracle-last-update` returns `u0` for `STX-FEED-ID`.
3. Pyth still holds/relays an older but not-yet-`max-staleness`-expired update with `publish-time = T1 < T2` and a more favorable STX price for a borrower.
4. Attacker calls `verify-and-update-price-feeds` (or relies on an already-stored older Pyth entry) then triggers a borrow, and `price-resolve` computes `prev = oracle-last-update(...) = u0`, so `(>= T1 u0)` passes, and `(<= (stacks-block-time - T1) max-staleness)` also passes since `T1` is still within the staleness window — the stale, more favorable price is accepted where `v0-3-market` would have rejected it as older than `T2`. [4](#0-3)

### Citations

**File:** mainnet/contracts/market/v0-4-market.clar (L117-120)
```text
;; -- Oracle timestamp tracking
(define-map last-update
  { type: (buff 1), ident: (buff 32) }
  uint)
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
