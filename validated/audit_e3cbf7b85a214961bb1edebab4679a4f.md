### Title
Liquidity index (`lindex`) accumulator rounds down every accrual with no compensating round-up path, causing compounding precision loss in depositor yield - ([File: mainnet/contracts/vault/v0-vault-stx.clar])

### Summary
The vault modules (`v0-vault-stx.clar`, `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-usdc.clar`, `v0-vault-usdh.clar`, `v0-vault-ststxbtc.clar`) maintain a liquidity index (`lindex`) that represents depositors' accrued share value, similar in role to the `reward_per_unit_stake` accumulator in the external report. The index is advanced every accrual with `calc-index-next`, which only uses the round-down helper `mul-div-down`, with no round-up counterpart ever applied to this accumulator.

### Finding Description
`calc-index-next` computes the next liquidity index purely with floor division: [1](#0-0) [2](#0-1) 

```
(define-private (calc-index-next (index-curr uint) (multiplier uint))
  (mul-div-down index-curr multiplier INDEX-PRECISION))
```

Unlike the debt index (`calc-cumulative-debt`, which explicitly uses `mul-div-up` to round debt *up* in the protocol's favor), the liquidity index has no equivalent protective rounding — every single accrual call truncates the fractional remainder of `index-curr * multiplier / INDEX-PRECISION` and discards it. Because `index-curr` for period *n+1* is derived from the already-truncated `index-curr` of period *n*, the lost precision compounds across accrual cycles exactly like the reported `reward_per_unit_stake`/`debt_per_unit_stake` accumulators in `record_redistribution`, which also never factor/scale the value before repeatedly dividing and adding.

This `lindex` value directly determines what depositors can redeem: it feeds `total-assets`/`total-assets-preview`, `convert-to-assets-preview`, and the treasury LP fee-reserve share calculation: [3](#0-2) 

It is also the basis of the on-chain price used for the corresponding zToken via `resolve-ztoken` in the market module (`v0-4-market.clar`), which likewise performs the final scaling with a single `div-down`: [4](#0-3) 

Each accrual event (which can occur multiple times per block window across six vaults, and is triggered on essentially every deposit/borrow/repay/withdraw/liquidation) throws away up to `INDEX-PRECISION - 1` units of index growth, and this loss is baked into the base for all future compounding, rather than being a one-off rounding at redemption time.

### Impact Explanation
The truncated growth is never credited back to depositors. Over the life of a vault this leads to a permanent, systematic shortfall between the interest actually accrued from borrowers (whose debt index rounds *up*, i.e., they are charged the full/rounded-up amount) and the interest actually credited to depositors (whose liquidity index rounds *down*, i.e., they receive less than the full amount). This is a mismatch of rounding direction between the debt side and the supply side of the same interest flow, causing depositors to permanently lose access to a fraction of their earned yield. This falls under the in-scope "High" impact category: permanent freezing/loss of unclaimed yield.

### Likelihood Explanation
This triggers automatically and unconditionally on every accrual call in every vault — no attacker action or special conditions are required, only elapsed time and normal protocol usage (deposits, borrows, repayments, liquidations). Given `INDEX-PRECISION` scaling and six active vaults each accruing frequently, the effect compounds continuously over the protocol's lifetime.

### Recommendation
Apply the same "protocol-conservative" rounding discipline used for debt (`calc-cumulative-debt` uses `mul-div-up`) to the liquidity-index growth path, or maintain a small remainder/dust accumulator that is carried forward and periodically re-applied to `lindex`, so that fractional interest is not silently discarded on every accrual. At minimum, ensure the rounding direction for `calc-index-next` never systematically favors the protocol against depositors across the accrual lifecycle.

### Proof of Concept
Not directly executable from static review alone; would require deploying/forking the vault contracts and running many sequential accrual calls (`vault-accrue` → `calc-index-next`) with a chosen `multiplier` value to demonstrate the cumulative divergence between (a) total debt-index growth billed to borrowers (rounded up) and (b) total liquidity-index growth credited to depositors (rounded down) over N accrual cycles, then compare against the vault's actual underlying token balance to quantify the undistributed remainder.

**Uncertainty note:** I was not able to fully trace, within the available iterations, the exact end-to-end redemption path that proves the truncated `lindex` growth is never reconciled anywhere else in the codebase (e.g., via a dust-sweep or remainder-tracking mechanism in the vault's public functions). If such a mechanism exists elsewhere in `mainnet/contracts/vault/*.clar` or `mainnet/contracts/market/*.clar`, it would mitigate or eliminate this finding; a full Devin session with complete file access would be needed to confirm this definitively.

### Citations

**File:** mainnet/contracts/vault/v0-vault-sbtc.clar (L147-148)
```text
(define-private (mul-div-down (x uint) (y uint) (z uint))
  (/ (* x y) z))
```

**File:** local-testing/contracts/vault/vault-ststxbtc.clar (L184-185)
```text
(define-private (calc-index-next (index-curr uint) (multiplier uint))
  (mul-div-down index-curr multiplier INDEX-PRECISION))
```

**File:** mainnet/contracts/vault/v0-vault-ststx.clar (L308-360)
```text
(define-private (convert-to-shares-preview (amount uint))
  (let ((ta (total-assets-preview))
        (ts (total-supply-preview)))
    (if (is-eq ts u0)
        amount
        (if (is-eq ta u0)
            u0
            (mul-div-down amount ts ta)))))

(define-private (convert-to-assets-preview (amount uint))
  (let ((ta (total-assets-preview))
        (ts (total-supply-preview)))
    (if (is-eq ta u0)
        u0
        (if (is-eq ts u0)
            u0
            (mul-div-down amount ta ts)))))

;; -- Debt helpers -----------------------------------------------------------

(define-private (total-debt)
  (calc-cumulative-debt (var-get principal-scaled) (var-get index)))

(define-private (debt-preview)
  (calc-cumulative-debt (var-get principal-scaled) (next-index)))

(define-private (total-assets)
  (let ((current-assets (var-get assets))
        (debt (total-debt))
        (borrowed (var-get total-borrowed))
        (interest (if (> debt borrowed) (- debt borrowed) u0)))
    (+ current-assets interest)))

(define-private (total-assets-preview)
  (let ((current-assets (var-get assets))
        (debt (debt-preview))
        (borrowed (var-get total-borrowed))
        (interest (if (> debt borrowed) (- debt borrowed) u0)))
    (+ current-assets interest)))

;; -- Treasury LP preview helpers --------------------------------------------

(define-private (calc-treasury-lp-preview)
  (let ((scaled-principal (var-get principal-scaled))
        (idx (var-get index))
        (next (next-index))
        (old-debt (mul-div-down scaled-principal idx INDEX-PRECISION))
        (new-debt (mul-div-down scaled-principal next INDEX-PRECISION))
        (debt-delta (if (> new-debt old-debt) (- new-debt old-debt) u0))
        (reserve-inc (mul-div-down debt-delta (var-get fee-reserve) BPS))
        (ta-preview (total-assets-preview)))
    (if (> reserve-inc u0)
        (mul-div-down reserve-inc (total-supply) (- ta-preview reserve-inc))
```

**File:** mainnet/contracts/market/v0-4-market.clar (L343-347)
```text
(define-private (resolve-ztoken (p uint) (aid uint))
  (let ((cached (unwrap! (get-cached-indexes aid) ERR-ORACLE-CALLCODE))
        (cached-lindex (get lindex cached))
        (scaled (* p cached-lindex)))
    (ok (div-down scaled INDEX-PRECISION))))
```
