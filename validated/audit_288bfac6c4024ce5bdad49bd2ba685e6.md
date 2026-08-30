### Title
Complete vault loss driving `lindex` to zero causes zToken oracle price resolution to permanently revert, freezing all positions using that zToken - (File: mainnet/contracts/market/v0-4-market.clar)

### Summary
`socialize-debt` in each vault (e.g. `v0-vault-stx.clar`, `v0-vault-sbtc.clar`, etc.) writes the liquidity index `lindex` down proportionally to a loss, but explicitly sets it to `u0` when the loss consumes all `total-assets`:
```
(new-lindex (if (and (> old-total-assets u0) (> old-total-assets debt-reduction))
               (mul-div-down current-lindex (- old-total-assets debt-reduction) old-total-assets)
               u0))
``` [1](#0-0) 

This `lindex` is later read by the market's oracle callcode transform for zTokens, `resolve-ztoken`, which multiplies the underlying price by the cached `lindex` and divides by `INDEX-PRECISION`:
```
(define-private (resolve-ztoken (p uint) (aid uint))
  (let ((cached (unwrap! (get-cached-indexes aid) ERR-ORACLE-CALLCODE))
        (cached-lindex (get lindex cached))
        (scaled (* p cached-lindex)))
    (ok (div-down scaled INDEX-PRECISION))))
``` [2](#0-1) 

If `lindex` is `0`, `resolve-ztoken` returns a final price of `0`. `price-resolve` then rejects that result with `oracle-price-legal`:
```
(define-private (oracle-price-legal (p uint))
  (> p u0))
...
(asserts! (and (oracle-price-legal final-price) (oracle-timestamp-fresh timestamp last-update-time max-staleness))
          ERR-ORACLE-INVARIANT)
``` [3](#0-2) 

### Finding Description
This is structurally the same bug class as the InfiniFi report: an index/rate variable (`slashIndex` there, `lindex` here) that legitimately reaches `0` after a total-loss event, and that zero value is later fed into a downstream computation that cannot tolerate zero — there it was a division causing a panic, here it is an assertion (`oracle-price-legal`) that rejects a zero price and reverts the whole call with `ERR-ORACLE-INVARIANT`.

Once a vault's `lindex` is driven to `0` via `socialize-debt` (a designed bad-debt socialization path reachable when a vault's `total-assets` is fully wiped out by a loss), any subsequent call to `price-resolve`/`price-multi-resolve` for that vault's zToken (`zSTX`, `zsBTC`, `zstSTX`, `zUSDC`, `zUSDH`, `zstSTXbtc`) will always compute `final-price == 0` and always revert with `ERR-ORACLE-INVARIANT`. Because price fetching for enabled collateral/debt assets is a mandatory step in essentially every position-mutating market function (borrow, repay, withdraw, liquidate) via `price-multi-resolve`/`price-resolve`, and because disabled collateral is still resolved on-demand via the same `resolve-ztoken` path in `process-collateral-asset`:
```
(let ((oracle-data (get oracle coll-asset))
      (price (unwrap-panic (price-resolve oracle-data))))
  (merge coll-asset { price: price }))
``` [4](#0-3) 

there is no way to ever obtain a valid price for that zToken again — `lindex` never recovers on its own since there is no reset-to-`1e18` path (unlike the recommended InfiniFi fix). Any account holding that zToken as collateral or debt becomes permanently unable to have its position processed by any function that must price all of its assets, because the transaction reverts before reaching the state-changing logic.

### Impact Explanation
This is a temporary/permanent freezing-of-funds class issue: users whose positions include the affected zToken as collateral or debt lose the ability to withdraw, repay, or otherwise interact with the market through any path that resolves the zToken's price (and liquidators cannot liquidate them either, since `process-collateral-asset`/`process-debt-asset` also depend on `price-resolve`). Unlike a normal bad-debt socialization event (an intended design decision to write down `lindex`), this specific zero-value edge case has no recovery mechanism, so it results in a lock of user funds rather than merely socializing the loss.

### Likelihood Explanation
This requires a vault to suffer a loss event via `socialize-debt` large enough to wipe out `total-assets` entirely (`old-total-assets <= debt-reduction`), which is an extreme but explicitly coded branch in the contract rather than a third-party oracle failure or a DAO misconfiguration. It is a privileged/authorized-caller function (`check-caller-auth`), but the resulting zero `lindex` state and its cascading revert on all zToken price resolutions is a pure code-logic gap independent of who triggers the socialization.

### Recommendation
Mirror the fix pattern noted for InfiniFi: when a write-down would reduce `lindex` to `0` (i.e., `old-total-assets <= debt-reduction`), either (a) do not allow price resolution / user-facing operations on the fully-wiped vault to hard-revert — instead treat the zToken as worth `0` collateral value without erroring, or (b) provide an explicit re-initialization/reset path for `lindex` distinct from the socialization arithmetic, together with an audit of all `total-assets`/reward-style aggregates that assume a nonzero index, analogous to the caution raised in the original report about breaking `totalRewardWeight`-style invariants.

### Proof of Concept
1. A vault (e.g. `v0-vault-stx.clar`) experiences a loss large enough that an authorized caller invokes `socialize-debt` with `scaled-amount` such that `debt-reduction >= old-total-assets`.
2. `new-lindex` is set to `u0` and persisted via `(var-set lindex new-lindex)`. [5](#0-4) 
3. Any user or liquidator subsequently calls a market function that must price `zSTX` (borrow, repay, withdraw, liquidate, or health check), which internally calls `price-multi-resolve`/`price-resolve` → `resolve-callcode` → `resolve-ztoken`. [6](#0-5) 
4. `resolve-ztoken` computes `final-price = 0` because `cached-lindex == 0`.
5. `price-resolve`'s `asserts!` on `oracle-price-legal` fails, reverting the entire transaction with `ERR-ORACLE-INVARIANT`, for every future call touching that zToken — permanently freezing any position holding it as collateral or debt. [7](#0-6)

### Citations

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L955-967)
```text
        ;; Write down lindex proportionally to loss in total-assets
        (new-lindex (if (and (> old-total-assets u0) (> old-total-assets debt-reduction))
                       (mul-div-down current-lindex (- old-total-assets debt-reduction) old-total-assets)
                       u0)))

    (try! (check-caller-auth))
    (asserts! (> scaled-amount u0) ERR-AMOUNT-ZERO)

    (var-set lindex new-lindex)
    (var-set principal-scaled (if (> scaled-principal scaled-amount) (- scaled-principal scaled-amount) u0))
    (var-set total-borrowed (if (> borrowed principal-reduction) (- borrowed principal-reduction) u0))
    (var-set assets (if (> current-assets principal-reduction) (- current-assets principal-reduction) u0))

```

**File:** mainnet/contracts/market/v0-4-market.clar (L343-347)
```text
(define-private (resolve-ztoken (p uint) (aid uint))
  (let ((cached (unwrap! (get-cached-indexes aid) ERR-ORACLE-CALLCODE))
        (cached-lindex (get lindex cached))
        (scaled (* p cached-lindex)))
    (ok (div-down scaled INDEX-PRECISION))))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L349-358)
```text
(define-private (resolve-callcode (p uint) (callcode (optional (buff 1))))
  (let ((cc (unwrap! callcode (ok p))))
    (if (is-eq cc CALLCODE-STSTX) (resolve-ststx p)
    (if (is-eq cc CALLCODE-ZSTX) (resolve-ztoken p STX)
    (if (is-eq cc CALLCODE-ZSBTC) (resolve-ztoken p sBTC)
    (if (is-eq cc CALLCODE-ZSTSTX) (resolve-ztoken (try! (resolve-ststx p)) stSTX)
    (if (is-eq cc CALLCODE-ZUSDC) (resolve-ztoken p USDC)
    (if (is-eq cc CALLCODE-ZUSDH) (resolve-ztoken p USDH)
    (if (is-eq cc CALLCODE-ZSTSTXBTC) (resolve-ztoken p stSTXbtc)
    ERR-ORACLE-CALLCODE)))))))))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L362-395)
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

    ;; update timestamp if newer
    (if (> timestamp last-update-time)
        (map-set last-update key timestamp)
        false)

    (ok final-price)))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L812-815)
```text
                           ;; Not found (disabled): resolve price on demand
                           (let ((oracle-data (get oracle coll-asset))
                                 (price (unwrap-panic (price-resolve oracle-data))))
                             (merge coll-asset { price: price }))))
```
