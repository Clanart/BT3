### Title
Future oracle timestamp bypasses staleness gate and permanently poisons the monotonic per-feed cache - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary

### Finding Description
The reported bug class is about an unbounded, attacker-poisonable cache/timestamp mechanism whose pruning logic can be permanently defeated by supplying an out-of-range (future) value. The closest reachable analog in the Zest pricing path is the monotonic per-feed timestamp cache implemented in `market.clar`'s oracle resolution logic.

`price-resolve` reads a price feed, then gates it with `oracle-timestamp-fresh`: [1](#0-0) 

```clarity
(define-private (oracle-timestamp-fresh (ts uint) (prev uint) (max-staleness uint))
  (let ((delta (if (> ts stacks-block-time)
                   u0
                   (- stacks-block-time ts))))
    (and
      (<= delta max-staleness)
      (>= ts prev))))
```

If the feed-reported timestamp `ts` is greater than the current `stacks-block-time`, `delta` is forced to `u0`, so the staleness check `(<= delta max-staleness)` is trivially satisfied regardless of how far in the future `ts` is. The function then also passes the monotonic check `(>= ts prev)` because a future timestamp is by definition greater than any prior recorded value.

`price-resolve` then persists this timestamp into the per-feed monotonic cache (`last-update`), and only updates it forward — never backward and never pruned: [2](#0-1) 

```clarity
(define-private (price-resolve ...)
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

Once a single future-dated `ts` is accepted and written into `last-update` for a `{type, ident}` feed key, that value becomes the new floor (`prev`) for all subsequent price resolutions of that feed, via `oracle-last-update`: [3](#0-2) 

Every future legitimate update carries a real-world `ts` that is less than this poisoned floor, so `(>= ts prev)` fails forever, and `price-resolve` permanently reverts with `ERR-ORACLE-INVARIANT` for that asset's feed — exactly mirroring the reported bug's core defect: a cache/monotonic-timestamp gate whose "future value" edge case is mishandled and can never self-heal or be pruned.

### Impact Explanation
Once poisoned, every operation that needs a price for that feed (borrow, repay, withdraw, health checks, liquidation checks via `price-multi-resolve`/`iter-price-multi`) reverts on `ERR-ORACLE-INVARIANT`, permanently freezing all collateral/debt operations tied to that asset. This lands squarely in the in-scope **High** impact category: temporary/permanent freezing of funds (in this case, permanent freezing of the ability to interact with the affected asset's collateral/debt positions, since the monotonic gate can never be reset by governance without a contract upgrade).

### Likelihood Explanation
This requires only a single feed update whose reported `publish-time` is momentarily ahead of `stacks-block-time` — a realistic edge case given that Pyth/DIA publish times originate from an external network clock that is not guaranteed to be synchronized with the Stacks block clock, rather than a deliberate malicious payload. The contract's own handling of the `ts > stacks-block-time` branch (forcing `delta = u0`) is the defect, independent of whether the upstream data is otherwise valid — it is a logic bug in `oracle-timestamp-fresh`, not a case of "bad third-party data."

### Recommendation
Reject timestamps that are ahead of `stacks-block-time` outright (treat `ts > stacks-block-time` as stale/invalid, not automatically fresh), instead of forcing `delta` to `u0`. Additionally, consider bounding how far `last-update` can advance per call (e.g., cap `ts` at `stacks-block-time`) so a single anomalous reading cannot permanently raise the monotonic floor beyond what future genuine updates can satisfy.

### Proof of Concept
1. A price feed update is submitted (via `write-feed`/`verify-and-update-price-feeds`) whose `publish-time` is even one second ahead of the current `stacks-block-time` (achievable through normal clock drift between the oracle network and the Stacks chain, or a relayer submitting slightly early).
2. `price-resolve` calls `oracle-timestamp-fresh` with this `ts`; since `ts > stacks-block-time`, `delta = u0`, so the staleness check passes trivially, and `(>= ts prev)` passes since `ts` is new. The price is accepted and `map-set last-update key ts` persists the inflated timestamp.
3. All subsequent real-world price updates for this feed carry `ts` values less than the poisoned `last-update` entry, causing `(>= ts prev)` to fail and `price-resolve` to always return `ERR-ORACLE-INVARIANT`.
4. Any market operation for the affected asset (borrow, repay, withdraw, liquidation, health check) that calls `price-resolve`/`price-multi-resolve` now permanently reverts, freezing the asset's collateral/debt functionality until a contract upgrade.

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

**File:** local-testing/contracts/market/market.clar (L961-962)
```text
(define-read-only (oracle-last-update (f {type: (buff 1), ident: (buff 32)}))
  (default-to u0 (map-get? last-update f)))
```
