Found the analog. In `mainnet/contracts/registry/v0-assets.clar` line 194, the asset-count check is:

```clarity
(asserts! (<= (var-get nonce) MAX-ASSETS) ERR-LIMIT-REACHED)
``` [1](#0-0) 

`MAX-ASSETS` is `u64` [2](#0-1)  and `DEBT-OFFSET` is also `u64`, meaning asset IDs 0-63 are meant to be the only valid collateral-bit positions before the debt-bit range begins at bit 64 [3](#0-2) . `nonce` is read *before* being incremented for the new asset (`increment` returns the pre-increment value as the new `id`, then bumps the var) [4](#0-3) , so the check `(<= (var-get nonce) MAX-ASSETS)` is evaluated against the *old* nonce value that will become the *new* asset's `id`.

Trace: for the 65th asset insertion, `(var-get nonce)` is `u64` at the time of the check, and `u64 <= u64` is true, so the insert is allowed. `increment` then returns `id = u64` for this new asset and sets `nonce` to `u65` [5](#0-4) . This is exactly the off-by-one described in the reference report (`<=` where `<` was intended, letting one extra element in). This 65th asset is thus registered with `id = 64` — but bit 64 of the shared 128-bit position bitmap is reserved as the *debt* bit for asset ID 0, per `mask-pos`: `(+ DEBT-OFFSET pos)` for the debt-side flag, i.e., collateral bit `pos` and debt bit `pos+64` [6](#0-5) .

**Direction of the error / who is affected:** once asset ID 64 exists, calling `enable(asset64, collateral=true)` sets bit `64` in the shared `bitmap` var via `(pow u2 position)` where `position = mask-pos(64, true) = 64` [7](#0-6) . That is the identical bit that `mask-pos(0, false)` computes for asset ID 0's *debt* flag (`DEBT-OFFSET + 0 = 64`) [6](#0-5) . Any read of `status(0, enabled-mask)` will now see `is-debt = true` for asset 0 purely because asset 64's collateral got enabled, and any `status(64, enabled-mask)` will see `is-collateral = true` whenever asset 0's debt bit happens to be set — the two independent flags (asset-0-debt-enabled, asset-64-collateral-enabled) become entangled in one bit. This bitmap is exactly the input consumed downstream for pricing/health decisions: `market.clar`'s `get-assets`/`user-safe-mask` masks user collateral against `get-enabled-bitmap` before resolving prices and building the notional evaluation used for LTV/health checks [8](#0-7) , and `v0-egroup.resolve`/`v0-1-data.get-user-position` uses the same collateral mask to pick the LTV egroup and compute the health factor [9](#0-8) .

**Impact:** With the collision, the collateral/debt status bit for an unrelated asset can be forced on/off by enabling/disabling a different asset, causing the collateral mask fed into `get-assets`, egroup resolution, and the health-factor computation to be wrong. This can make `is-liquidatable` return false for an actually undercollateralized position (temporary/permanent freezing of the ability to liquidate bad debt, or an attacker crafting a mask that lets them borrow beyond the LTV limits the protocol intends) — landing in **High: temporary freezing of funds / protocol insolvency risk**, or **Critical: protocol insolvency**, depending on how it's exploited, since it corrupts the core collateral/debt bitmap that all pricing and LTV/health verdicts are derived from.

**Root cause:** the off-by-one in `(asserts! (<= (var-get nonce) MAX-ASSETS) ERR-LIMIT-REACHED)` at `mainnet/contracts/registry/v0-assets.clar:194`, which should be `(< (var-get nonce) MAX-ASSETS)` (or `(<= (var-get nonce) (- MAX-ASSETS u1))`), since `nonce` is the pre-increment value that becomes the new asset's `id`, and IDs must stay within `0..63` to avoid colliding with the debt-bit range starting at bit `64`.

### Title
Off-by-one asset count check allows a 65th asset (`id = 64`) whose collateral bit collides with asset 0's debt bit, corrupting the shared collateral/debt bitmap used for pricing and health checks - (File: `mainnet/contracts/registry/v0-assets.clar`)

### Summary
The asset registration limit check `(<= (var-get nonce) MAX-ASSETS)` in `insert` at line 194 allows a 65th asset to be registered with `id = 64`, one past the intended `0..63` range reserved for the 64-bit collateral segment of the shared bitmap.

### Finding Description
`MAX-ASSETS` is `u64`, and the bitmap layout reserves bits `0..63` for collateral flags and `64..127` for debt flags (`DEBT-OFFSET = u64`), via `mask-pos`. The `increment` function returns the current `nonce` value as the new asset's `id` and only afterwards increments the stored `nonce`. Because the guard compares `nonce` (the pre-increment, soon-to-be `id`) with `<= MAX-ASSETS` instead of `< MAX-ASSETS`, a 65th `insert` call succeeds when `nonce == 64`, producing an asset with `id = 64`. This ID's collateral bit (position 64, from `mask-pos(64, true)`) is numerically identical to asset ID 0's debt bit (`mask-pos(0, false) = 64`), so `enable`/`disable` operations on this 65th asset directly toggle the debt-enabled flag of asset 0 in the shared `bitmap` variable, and vice versa. [10](#0-9) [1](#0-0) [11](#0-10) 

### Impact Explanation
The corrupted bitmap is the sole input to `market.clar`'s collateral/debt resolution (`user-safe-mask`, `get-assets`) and to `v0-egroup.resolve`/health-factor computation in `v0-1-data.clar`. A bit collision lets enabling/disabling one asset silently flip the debt/collateral status bit of an unrelated asset, producing a wrong collateral mask that is used to select the LTV egroup and compute `is-liquidatable`. This can suppress liquidation of an undercollateralized position or let a position borrow against an egroup with a more favorable LTV than intended — falling under High (temporary freezing of funds through blocked liquidation) up to Critical (protocol insolvency) if exploited at scale.

### Likelihood Explanation
Requires the DAO to register a 65th asset (an intended, permitted DAO action, not a compromise), which is plausible over the protocol's lifetime as more assets are onboarded; the bug is silently latent until that 65th `insert` call, after which every subsequent bitmap read is corrupted for the colliding IDs.

### Recommendation
Change the check in `insert` to `(asserts! (< (var-get nonce) MAX-ASSETS) ERR-LIMIT-REACHED)` (or equivalently compare against `MAX-ASSETS - u1`), ensuring `nonce`/`id` never reaches `64` and stays strictly within `0..63`.

### Proof of Concept
1. DAO calls `insert` 64 times, filling asset IDs `0..63`, `nonce` becomes `u64`.
2. DAO calls `insert` a 65th time: check `(<= u64 u64)` passes, `id = 64` is assigned via `increment`.
3. DAO calls `enable(asset64, collateral=true)`: `mask-pos(64, true) = 64`, sets bit 64 of `bitmap`.
4. `status(0, bitmap)` now reports `is-debt = true` for asset 0, even though asset 0's debt was never explicitly enabled — because `mask-pos(0, false) = DEBT-OFFSET + 0 = 64`, the same bit.
5. Any position holding asset 0 collateral will have its collateral mask, egroup resolution, and health factor computed using this corrupted debt/collateral bit, producing an incorrect LTV/health verdict.

### Citations

**File:** mainnet/contracts/registry/v0-assets.clar (L15-19)
```text
(define-constant DEBT-OFFSET u64)
(define-constant U128-BUFF-LEN u17)
(define-constant U8-BUFF-OFFSET u16)
(define-constant U32-BUFF-OFFSET u13)
(define-constant ITER-UINT-64 (list u0 u1 u2 u3 u4 u5 u6 u7 u8 u9 u10 u11 u12 u13 u14 u15 u16 u17 u18 u19 u20 u21 u22 u23 u24 u25 u26 u27 u28 u29 u30 u31 u32 u33 u34 u35 u36 u37 u38 u39 u40 u41 u42 u43 u44 u45 u46 u47 u48 u49 u50 u51 u52 u53 u54 u55 u56 u57 u58 u59 u60 u61 u62 u63))
```

**File:** mainnet/contracts/registry/v0-assets.clar (L22-22)
```text
(define-constant MAX-ASSETS u64)
```

**File:** mainnet/contracts/registry/v0-assets.clar (L75-78)
```text
(define-private (mask-pos (pos uint) (collateral bool))
  (if (is-eq collateral true)
      pos
      (+ DEBT-OFFSET pos)))
```

**File:** mainnet/contracts/registry/v0-assets.clar (L103-120)
```text
(define-private (increment)
  (let ((curr (var-get nonce))
        (next (+ curr u1)))
    (var-set nonce next)
    curr))

;; -- Status helpers ---------------------------------------------------------

(define-private (unwrap-status (id uint) (enabled-mask uint))
  (unwrap-panic (status id enabled-mask))
)

(define-private (status (id uint) (enabled-mask uint))
  (let ((entry (try! (lookup id)))
        (debt-position (mask-pos id false))
        (is-collateral (> (bit-and enabled-mask (pow u2 id)) u0)) ;; 0 offset
        (is-debt (> (bit-and enabled-mask (pow u2 debt-position)) u0)))
    (ok (merge entry { id: id, collateral: is-collateral, debt: is-debt }))))
```

**File:** mainnet/contracts/registry/v0-assets.clar (L182-200)
```text
  (let ((id (increment))
        (asset-address (contract-of ft))
        (final-id (uint-to-buff1 id))
        (staleness (get max-staleness oracle-data))
        (entry {
          id: final-id,
          addr: asset-address,
          decimals: (call-get-decimals ft),
          oracle: oracle-data,
        }))

      (try! (check-dao-auth))
      (asserts! (<= (var-get nonce) MAX-ASSETS) ERR-LIMIT-REACHED)
      (asserts! (> staleness u0) ERR-INVALID-STALENESS)

      (asserts! (and
          (map-insert registry final-id entry)
          (map-insert reverse asset-address final-id)
        ) ERR-ALREADY-REGISTERED)
```

**File:** mainnet/contracts/registry/v0-assets.clar (L254-278)
```text
(define-public (enable (asset principal) (collateral bool))
  (let ((id (try! (get-reverse asset)))
        (final-id (buff-to-uint-be id))
        (enabled-mask (get-bitmap))
        (position (mask-pos final-id collateral))
        (updated-bitmap (bit-or enabled-mask (pow u2 position))))

      (try! (check-dao-auth))
      (asserts! (not (is-eq enabled-mask updated-bitmap)) ERR-ALREADY-ENABLED)
      (var-set bitmap updated-bitmap)
      
      (print {
        action: "asset-enable",
        caller: tx-sender,
        data: {
          asset-address: asset,
          asset-id: final-id,
          is-collateral: collateral,
          bitmap-before: enabled-mask,
          bitmap-after: updated-bitmap
        }
      })
      
      (ok true)
    ))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L428-484)
```text
(define-private (user-safe-mask (mask-user uint) (mask-enabled uint))
  (let ((enabled-collateral (bit-and mask-enabled MAX-U64))
        (user-collateral (bit-and mask-user MAX-U64))
        (user-debt (/ (bit-and mask-user DEBT-MASK) (pow u2 DEBT-OFFSET)))
        (collateral-match (bit-and user-collateral enabled-collateral)))
    (bit-or collateral-match user-debt)))

(define-private (mask-to-list-internal (mask uint) (offset uint) (iter-list (list 64 uint)))
  (let ((init { mask: mask, offset: offset, result: (list) })
        (out (fold mask-to-list-iter iter-list init)))
    (get result out)))

(define-private (mask-to-list-iter (p uint) (acc {mask: uint, offset: uint, result: (list 64 uint)}))
  (let ((mask (get mask acc))
        (offset (get offset acc))
        (has? (asserts! (> (bit-and mask (pow u2 p)) u0) acc))
        (result (get result acc))
        (value (if (is-eq offset u0) p (- p offset)))
        (new (as-max-len? (append result value) u64)))
    (merge acc { result: (unwrap-panic new) })))

(define-private (mask-to-list-collateral (mask uint))
  (mask-to-list-internal mask u0 ITER-UINT-64))

;; -- Registry wrappers ------------------------------------------------------

(define-private (get-enabled-bitmap)
  (contract-call? .v0-assets get-bitmap))

(define-private (get-status-multi (ids (list 64 uint)))
  (contract-call? .v0-assets status-multi ids))

(define-private (get-egroup (mask uint))
  (contract-call? .v0-egroup resolve mask))

(define-private (get-account-scaled-debt (account principal) (asset-id uint))
  (contract-call? .v0-market-vault get-account-scaled-debt account asset-id))

(define-private (get-position (account principal)) ;; enabled only
  (let ((mask (get-enabled-bitmap)))
    (contract-call? .v0-market-vault get-position account mask)))

(define-private (get-full-position (account principal)) ;; all collaterals
  (contract-call? .v0-market-vault get-position account MAX-U64))

(define-private (get-liquidation-position (account principal)) ;; liquidation specific (enabled collateral + all debt)
  (let ((mask (get-enabled-bitmap)))
    (contract-call? .v0-market-vault get-position account mask)))

;; -- Context & asset helpers ------------------------------------------------

(define-private (get-asset (asset principal))
  (contract-call? .v0-assets get-asset-status asset))

(define-private (get-assets (mask-user uint))
  (let ((mask-enabled (get-enabled-bitmap))
        (safe-mask (user-safe-mask mask-user mask-enabled))
```

**File:** mainnet/contracts/utility/v0-1-data.clar (L441-480)
```text
              ;; Map each debt entry to enriched format with actual balances
              (enriched-debts (map build-debt-entry debt-list))
              ;; Calculate notional values
              (coll-usd (fold sum-collateral-usd collateral-list u0))
              (debt-usd (fold sum-debt-usd debt-list u0))
              ;; Calculate LTV
              (current-ltv (if (is-eq coll-usd u0)
                              (if (is-eq debt-usd u0) u0 BPS)
                              (mul-div-down debt-usd BPS coll-usd)))
              ;; Get egroup for health calculation
              (egroup-result (contract-call? .v0-egroup resolve mask)))
          (match egroup-result
            egroup
              (let ((ltv-borrow (buff-to-uint-be (get LTV-BORROW egroup)))
                    (ltv-liq-partial (buff-to-uint-be (get LTV-LIQ-PARTIAL egroup)))
                    ;; Health factor: (coll x ltv-borrow) / debt, scaled to BPS
                    ;; >10000 = healthy, <10000 = unhealthy
                    (health-factor (if (is-eq debt-usd u0)
                                      u100000000  ;; Infinite health if no debt
                                      (mul-div-down (mul-bps-down coll-usd ltv-borrow) BPS debt-usd))))
                (ok {
                  account: account,
                  mask: mask,
                  collateral: collateral-list,
                  debt: enriched-debts,
                  total-collateral-usd: coll-usd,
                  total-debt-usd: debt-usd,
                  current-ltv: current-ltv,
                  ltv-borrow: ltv-borrow,
                  ltv-liq-partial: ltv-liq-partial,
                  health-factor: health-factor,
                  is-liquidatable: (>= current-ltv ltv-liq-partial)
                }))
            egroup-err (ok {
              account: account,
              mask: mask,
              collateral: collateral-list,
              debt: enriched-debts,
              total-collateral-usd: coll-usd,
              total-debt-usd: debt-usd,
```
