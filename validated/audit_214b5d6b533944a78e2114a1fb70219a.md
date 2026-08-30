## Analysis

I found a rounding-direction analog to the Connext `_handleExecuteLiquidity()`/`_reconcile()` finding, in the zToken price callcode transformation in `resolve-ztoken`, which is used identically for both collateral and debt valuation but always rounds in the direction that favors the borrower (never conservative for debt), unlike the deliberate, explicit rounding asymmetry seen elsewhere in the same contract (e.g. `calculate-asset-notional-value` rounding collateral down and debt up).

### Title
Single-direction rounding in `resolve-ztoken` price callcode understates zToken debt valuation, producing a wrong health verdict - (File: `mainnet/contracts/market/v0-4-market.clar`)

### Summary
`resolve-ztoken` (used for all zToken price feeds: zSTX, zsBTC, zstSTX, zUSDC, zUSDH, zstSTXbtc) always rounds the price **down** via `div-down`, regardless of whether the resulting price is subsequently used to value collateral or debt. [1](#0-0)  Elsewhere in the exact same contract, the health-check evaluation function `calculate-asset-notional-value` deliberately rounds collateral USD value **down** and debt USD value **up** (via `mul-div-up` on the scaled-debt-to-actual conversion, then `normalize(... true)`), to keep the health/LTV computation conservative. [2](#0-1)  When the debt asset is itself a zToken (e.g. a user borrows `zUSDC`/`zSTX` etc., which is valid since zTokens are registered `debt: true/false` assets with `oracle.callcode` set to the zToken callcode), the *price* component of that conservative debt-USD calculation is silently rounded down by `resolve-ztoken`, undermining the intended round-up conservatism for debt.

### Finding Description
The oracle pipeline is: `price-resolve` → `resolve-price-feed` (raw price) → `resolve-callcode` → `resolve-ztoken` for zToken assets. [3](#0-2)  `resolve-ztoken` computes `price * lindex / INDEX_PRECISION` and always truncates (rounds down) via `div-down`:
```
(define-private (resolve-ztoken (p uint) (aid uint))
  (let ((cached (unwrap! (get-cached-indexes aid) ERR-ORACLE-CALLCODE))
        (cached-lindex (get lindex cached))
        (scaled (* p cached-lindex)))
    (ok (div-down scaled INDEX-PRECISION))))
``` [1](#0-0) 

This resolved price is a single `uint` that is fed into `assets` list (with field `price`), and consumed identically by both collateral and debt legs of `calculate-asset-notional-value`:
```
(coll-notional (if (> coll-amount u0) (normalize (* coll-amount price) decimals false) u0))
...
(debt-notional (if (> debt-scaled u0)
                   (let ((actual (mul-div-up debt-scaled ib INDEX-PRECISION)))
                     (normalize (* actual price) decimals true))
                   u0))
``` [2](#0-1) 

The contract's own convention is that **debt** valuation should always round up (conservative — never understate what a borrower owes) while **collateral** valuation rounds down (conservative — never overstate what backs the loan). This convention is respected for the *amount* leg (`mul-div-up`, `normalize(... true)`) but is broken for the *price* leg whenever the underlying oracle price passes through `resolve-ztoken`'s `div-down`, because the same rounded-down price is reused for both legs. The debt-USD total therefore combines a round-up amount with a round-down price — a hybrid that understates true debt-USD value whenever `price*lindex` is not an exact multiple of `INDEX_PRECISION` (which is essentially every price/lindex pair, since `lindex` continuously accrues at a per-second rate and is virtually never a round multiple).

The identical single `price` value is also used in `find-and-resolve-asset-value` and `get-asset-value` used in liquidation flows (`process-debt-asset`, `process-collateral-asset`), meaning the same understated debt price propagates into `debt-usd`, `max-debt-usd`, `debt-actual-usd`, and ultimately `current-ltv`. [4](#0-3)  `current-ltv = mul-div-down(total-debt-usd, BPS, total-collateral-usd)`. [5](#0-4) 

### Impact Explanation
Direction of error: `total-debt-usd` is understated whenever the debt asset (or, transitively, any collateral/debt notional using a zToken oracle price) truncates via `div-down` in `resolve-ztoken`. Since `current-ltv` is directly proportional to `total-debt-usd`, a borrower's true LTV is always computed slightly lower than reality. The borrower (and any zToken debt holder) benefits, at the expense of the protocol/lenders, because:
- `health-check` gating in `liquidate()` (`asserts! (>= current-ltv ltv-liq-partial))` [6](#0-5)  may return "healthy" for a position that is actually at or past the liquidation threshold, delaying or blocking liquidation.
- This is a wrong-health-verdict outcome, falling under the "rounding direction" category explicitly named in scope. The systemic effect (positions escaping timely liquidation) is a form of temporary/permanent bad-debt exposure for the protocol — the same "protocol takes on temporary bad debt" impact class cited in the analogous Connext finding.

Per-call magnitude is small (bounded by `INDEX_PRECISION` granularity), matching the Connext judges' observation that the individual rounding error is tiny but systemic exposure (positions that should be liquidated are not) is the real risk, especially since `lindex` grows continuously and this rounding-down happens on every single price read for every zToken-denominated debt/collateral leg, and errors here are not correction/rebalanced anywhere (unlike Connext where `_reconcile()` corrects errors from `_handleExecuteLiquidity()` — here there is no corresponding "round up" step to net out the loss).

### Likelihood Explanation
This triggers on every position whose debt (or collateral) is denominated in one of the six zTokens (zSTX, zsBTC, zstSTX, zUSDC, zUSDH, zstSTXbtc) any time `resolve-ztoken` executes — i.e., essentially every liquidation/health check involving a zToken, since `lindex` is virtually never an exact multiple of `INDEX_PRECISION`. No attacker action beyond simply holding a zToken-denominated position is required, making likelihood high; only the per-instance magnitude is small.

### Recommendation
Thread a `round-up` flag through `resolve-callcode`/`resolve-ztoken` (and `resolve-ststx` similarly) so that when a price is being resolved for a debt-side valuation, `div-up` is used instead of `div-down`, mirroring the existing round-up/round-down discipline already applied to the amount leg in `calculate-asset-notional-value`. Alternatively, only round the final normalized USD value (as is already done) but recompute `debt-notional` using a full round-up multiplication chain (`price` resolved unrounded/at higher intermediate precision, single final round-up at the `normalize` step) rather than truncating twice (once inside `resolve-ztoken`, once implicit in the amount leg).

### Proof of Concept
1. Borrower opens a position with `zUSDC` (or any zToken) as the debt asset, at an `lindex` value where `price * lindex` is not an exact multiple of `INDEX_PRECISION` (true for virtually all values since `lindex` accrues continuously per second).
2. `price-resolve` → `resolve-callcode` → `resolve-ztoken` truncates `price*lindex/INDEX_PRECISION`, losing up to `INDEX_PRECISION-1` units of precision in the returned price (this can be up to just under 1 raw price unit, i.e., understating price by a fraction of a cent per unit of `INDEX_PRECISION` granularity).
3. This truncated price feeds into `calculate-asset-notional-value`'s `debt-notional` computation and into `process-debt-asset`'s `debt-usd`, producing an understated `total-debt-usd`/`debt-usd`.
4. `current-ltv = mul-div-down(total-debt-usd, BPS, total-collateral-usd)` is computed slightly lower than the true LTV. [5](#0-4) 
5. If the true LTV is at or just above `ltv-liq-partial` but the understated LTV falls below it, `liquidate()`'s `health-check` assertion (`ERR-HEALTHY`) incorrectly blocks the liquidation call, leaving an actually-unhealthy position unliquidated. [6](#0-5) 

**Caveat**: I could not verify the exact numeric bound of the truncation error (i.e., whether `INDEX_PRECISION`'s granularity makes this negligible in practice) without running the Clarity test suite, so the severity here should be validated experimentally against realistic `lindex`/price magnitudes before treating this as more than a low/informational-leaning medium finding.

### Citations

**File:** mainnet/contracts/market/v0-4-market.clar (L343-347)
```text
(define-private (resolve-ztoken (p uint) (aid uint))
  (let ((cached (unwrap! (get-cached-indexes aid) ERR-ORACLE-CALLCODE))
        (cached-lindex (get lindex cached))
        (scaled (* p cached-lindex)))
    (ok (div-down scaled INDEX-PRECISION))))
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

**File:** mainnet/contracts/market/v0-4-market.clar (L544-574)
```text
(define-private (calculate-asset-notional-value
          (asset-entry {
              id: uint, addr: principal, decimals: uint,
              oracle: { type: (buff 1), ident: (buff 32), callcode: (optional (buff 1)), max-staleness: uint },
              collateral: bool, debt: bool, price: uint })
          (acc { clist: (list 64 { aid: uint, amount: uint }),
                  dlist: (list 64 { aid: uint, scaled: uint }),
                  coll-total: uint,
                  debt-total: uint }))
  (let ((asset-id (get id asset-entry))
        (price (get price asset-entry))
        (decimals (get decimals asset-entry))
        (collateral-list (get clist acc))
        (debt-list (get dlist acc))
        (coll-amount (find-collateral-amount collateral-list asset-id))
        (coll-notional (if (> coll-amount u0)
                           (normalize (* coll-amount price) decimals false)
                           u0))

        (debt-scaled   (find-debt-scaled debt-list asset-id))
        (debt-notional (if (> debt-scaled u0) ;; use cache instead here
                           (let ((cached (unwrap-panic (accrue-and-cache asset-id)))
                                 (ib (get index cached))
                                 (actual (mul-div-up debt-scaled ib INDEX-PRECISION)))
                             (normalize (* actual price) decimals true))
                           u0)))

    { clist: collateral-list,
      dlist: debt-list,
      coll-total: (+ (get coll-total acc) coll-notional),
      debt-total: (+ (get debt-total acc) debt-notional) }))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L668-687)
```text
(define-private (find-and-resolve-asset-value
                  (assets (list 64 
                    { id: uint, addr: principal, decimals: uint,
                    oracle: { type: (buff 1), ident: (buff 32), callcode: (optional (buff 1)), max-staleness: uint },
                    collateral: bool, debt: bool, price: uint }))
                  (asset-id uint) (amount uint) (round-up bool))
  (match (find-asset asset-id assets)
    asset (normalize (* amount (get price asset)) (get decimals asset) round-up)
    u0))

;; find-and-resolve-asset-value has "price" already pre-calculated, get-asset-value does not
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

**File:** mainnet/contracts/market/v0-4-market.clar (L1422-1426)
```text
    ;; LTV = (debt x 10,000) / collateral
    ;; handle edge case: If collateral = 0, return max LTV (BPS) or 0 if debt also 0
    (current-ltv   (if (is-eq total-collateral-usd u0)
                       (if (is-eq total-debt-usd u0) u0 BPS)
                       (mul-div-down total-debt-usd BPS total-collateral-usd)))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L1433-1435)
```text
    ;; health check (FAIL-FAST) 
    ;; Check position is liquidatable BEFORE calling calc-liq-factor
    (health-check  (asserts! (>= current-ltv ltv-liq-partial) ERR-HEALTHY))
```
