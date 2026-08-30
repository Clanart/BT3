### Title
Future-dated oracle timestamp permanently poisons monotonic freshness check, causing irrecoverable price-feed DOS - (File: mainnet/contracts/market/v0-4-market.clar)

### Summary
`oracle-timestamp-fresh` treats any oracle timestamp that is ahead of `stacks-block-time` as automatically "fresh" (`delta = u0`), and `price-resolve` unconditionally persists that timestamp into the per-feed `last-update` map whenever it is greater than the previously stored value. A single future-dated `publish-time` from Pyth/DIA therefore permanently poisons the monotonic per-feed timestamp state for that asset: every subsequent legitimate price resolution will have `ts < prev` and revert with `ERR-ORACLE-INVARIANT`, with no admin function to reset `last-update`. This mirrors the GasThrottle report's pattern — an oracle-adjacent value can be pushed into a state that makes a required gating check fail forever, with no on-chain recovery path.

### Finding Description
`oracle-timestamp-fresh` in `market.clar` / `v0-4-market.clar` computes staleness as: [1](#0-0) 

```
(define-private (oracle-timestamp-fresh (ts uint) (prev uint) (max-staleness uint))
  (let ((delta (if (> ts stacks-block-time)
                   u0
                   (- stacks-block-time ts))))
    (and
      (<= delta max-staleness)
      (>= ts prev))))
```

When the reported timestamp `ts` is greater than the current `stacks-block-time` (a future timestamp), `delta` is forced to `u0`, so the `(<= delta max-staleness)` clause trivially passes regardless of how far in the future `ts` is. There is no explicit rejection of `ts > stacks-block-time`.

`price-resolve` then unconditionally advances the monotonic `last-update` map whenever the (unbounded, unchecked-for-future) timestamp is larger than the stored value: [2](#0-1) 

```
(asserts! (and (oracle-price-legal final-price) (oracle-timestamp-fresh timestamp last-update-time max-staleness))
          ERR-ORACLE-INVARIANT)

(if (> timestamp last-update-time)
    (map-set last-update key timestamp)
    false)
```

Once a far-future timestamp is stored as `last-update-time` for a `{type, ident}` key, every future legitimate call to `price-resolve` for that feed will have `ts < prev`, causing `(>= ts prev)` to fail permanently and `ERR-ORACLE-INVARIANT` to be thrown until `stacks-block-time` naturally advances past the poisoned value (which, for a sufficiently future-dated value, is effectively forever from a practical/operational standpoint). The same code path is duplicated identically in `mainnet/contracts/market/v0-4-market.clar`: [3](#0-2) 

There is no DAO/admin function found anywhere in the market or registry contracts that can reset the `last-update` map or bypass this monotonic check, which mirrors the original report's "no way to recover" characteristic of `GasThrottle`.

### Impact Explanation
`price-resolve` is the sole entry point used by `get-asset-value`/`find-and-resolve-asset-value` to price every collateral and debt asset for borrow, repay, withdraw, and liquidation flows: [4](#0-3) 

If the feed backing any asset (STX, sBTC, USDC, etc.) is poisoned this way, all market operations depending on that asset's price permanently revert. Positions using that asset as collateral or debt can no longer be opened, adjusted, repaid, or — critically — liquidated, since liquidation also depends on `get-asset-value`/`price-resolve`. This satisfies the "permanent freezing of funds" impact class: user collateral and debt become permanently unmanageable, and undercollateralized positions cannot be liquidated, risking protocol insolvency if prices move against frozen positions.

### Likelihood Explanation
Triggering this requires only one abnormal `publish-time` value (ahead of `stacks-block-time`) to be returned from the configured price source for a given feed once — this can result from any oracle-side timestamp anomaly, clock drift, or a similar bug/attack against the third-party oracle affecting the `publish-time` field alone (the flaw is in how Zest's own code handles that timestamp, not in the price value itself). Given oracle infrastructure incidents are a known real-world occurrence and the code path performs no explicit future-timestamp rejection, likelihood is non-trivial and the consequence (permanent, code-level, non-recoverable poisoning) is severe and irreversible.

### Recommendation
1. Explicitly reject timestamps greater than `stacks-block-time` (or clamp them, but do not treat them as automatically "fresh") in `oracle-timestamp-fresh`.
2. Do not persist a `last-update` value that exceeds the current `stacks-block-time` under any circumstance.
3. Add a DAO-gated function to reset/override a poisoned `last-update` entry for a given `{type, ident}` key so the protocol can recover if a bad timestamp is ever admitted despite the fix above.

### Proof of Concept
1. Configure asset X with oracle feed `{type: TYPE-PYTH, ident: FEED_X, max-staleness: 120}`.
2. Have `pyth-storage-v4.get-price` (or the DIA client) return, for `FEED_X`, a `publish-time` far in the future relative to `stacks-block-time` (e.g., due to a misconfigured/compromised publisher or oracle bridge bug affecting only the timestamp field).
3. Call any market function that resolves X's price (e.g., a deposit or borrow touching X). `oracle-timestamp-fresh` computes `delta = u0` since `ts > stacks-block-time`, passes the freshness assertion, and `price-resolve` executes `(map-set last-update key timestamp)`, storing the future timestamp as `last-update-time` for X's feed key.
4. On any subsequent call, the oracle correctly returns the real current `publish-time`, but `(>= ts prev)` now fails because `prev` (the poisoned future value) exceeds the real `ts`. `price-resolve` reverts with `ERR-ORACLE-INVARIANT` for every future call touching asset X, with no available on-chain remediation.

### Citations

**File:** local-testing/contracts/market/market.clar (L387-393)
```text
(define-private (oracle-timestamp-fresh (ts uint) (prev uint) (max-staleness uint))
  (let ((delta (if (> ts stacks-block-time)
                   u0
                   (- stacks-block-time ts))))
    (and
      (<= delta max-staleness)
      (>= ts prev))))
```

**File:** local-testing/contracts/market/market.clar (L395-417)
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

**File:** mainnet/contracts/market/v0-4-market.clar (L679-687)
```text
(define-private (get-asset-value
                  (asset { id: uint, addr: principal, decimals: uint,
                          oracle: { type: (buff 1), ident: (buff 32), callcode: (optional (buff 1)), max-staleness: uint },
                          collateral: bool, debt: bool})
                  (amount uint) (round-up bool))
    (let ((oracle-data (get oracle asset))
          (price (try! (price-resolve oracle-data)))
          (decimals (get decimals asset)))
      (ok (normalize (* amount price) decimals round-up))))
```
