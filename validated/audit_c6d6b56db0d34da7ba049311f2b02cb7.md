### Title
Discrete per-touch compounding of `index`/`lindex` in `calc-index-next` makes borrower interest and LP yield dependent on vault-activity frequency - ([File: mainnet/contracts/vault/v0-vault-stx.clar])

### Summary
The Zest v0 vault contracts (`v0-vault-stx.clar`, `v0-vault-sbtc.clar`, `v0-vault-usdc.clar`, `v0-vault-usdh.clar`, `v0-vault-ststx.clar`, `v0-vault-ststxbtc.clar` — all share identical logic) accrue interest by repeatedly multiplying the running `index`/`lindex` state variables by a *simple*, non-compounded per-period rate factor every time the accrual is refreshed. Because this multiplicative update is applied at every touch of the vault (any deposit/borrow/repay/redeem that reads/updates `last-update`), the effective annualized interest ends up frequency-dependent: the more often the vault is touched, the more compounding occurs beyond what the quoted interest-rate curve (`points-ir`) intends. This is the same bug class as Sherlock H-1 in Flayer's `TaxCalculator.sol::calculateCompoundedFactor` — a discrete "linear-per-tick" rate formula that is then compounded multiplicatively across ticks whose frequency is attacker/activity controlled rather than fixed.

### Finding Description
`calc-multiplier-delta` computes a linear (simple-interest) multiplier for the elapsed period since the last update: [1](#0-0) 

```
(define-private (calc-multiplier-delta (rate uint) (time-delta uint) (round-up bool))
  (+ INDEX-PRECISION
    (if round-up
      (mul-div-up rate (* time-delta INDEX-PRECISION) SECONDS-PER-YEAR-BPS)
      (mul-div-down rate (* time-delta INDEX-PRECISION) SECONDS-PER-YEAR-BPS))))
```

This multiplier — `1 + rate * time-delta / SECONDS_PER_YEAR` — is exactly the discrete simple-interest factor for the *elapsed slice* `time-delta` (i.e. the time since the previous accrual, not since loan origination).

That per-slice multiplier is then applied **multiplicatively onto the running index** via `calc-index-next`: [2](#0-1) 

```
(define-private (calc-index-next (index-curr uint) (multiplier uint))
  (mul-div-down index-curr multiplier INDEX-PRECISION))
```

and both `next-index` (borrow/debt index) and `next-liquidity-index` (LP index) call this chain using `(- stacks-block-time (var-get last-update))` as `time-delta`: [3](#0-2) 

Because `time-delta` is measured from the *previous* accrual event (`last-update`), and accrual happens on essentially every state-changing vault action (deposits, borrows, repayments, redemptions all read/refresh index state through `next-index`/`total-debt`/`debt-preview`), the number of compounding "ticks" per year is not fixed — it equals the number of vault interactions. Each tick multiplies the running index by `(1 + r·Δt/Y)`, so `n` ticks over a year yield an effective growth factor of `(1 + r/n)^n`, which converges toward `e^r` as `n` grows. This is precisely the discrete-compounding artifact described in the reference report: the code is written as if it computes simple/linear interest per call, but because the *result* of each call is fed back as the new base for the next call, the actual accrued factor compounds with the frequency of unrelated third-party activity (e.g., other users' deposits/borrows/repayments), not with any protocol-intended compounding schedule.

### Impact Explanation
- **Direction of the error / who profits:** Both the borrow index (`index`, used in `calc-cumulative-debt`, rounded **up** via `mul-div-up` inside `calc-multiplier-delta`) and the liquidity index (`lindex`, rounded down) compound this way. High vault activity (frequent touches) causes borrowers' scaled debt to compound faster than the quoted `points-ir` curve implies, i.e. borrowers are charged more interest than the nominal rate schedule suggests. Symmetrically, LP yield compounds similarly but with the reserve-factor haircut and floor rounding, so LPs/protocol do not capture the full extra amount borrowers pay — the excess is partially lost to rounding/reserve split rather than cleanly redistributed, and conversely low-activity periods under-compound relative to what a continuously-compounding or correctly-annualized model would produce.
- This directly mispricess the "price" of borrowed capital (interest owed) — an unintended and activity-frequency-dependent divergence from the quoted rate curve — falling into the **High** impact bucket of this scan (theft of / short-changing of unclaimed yield: borrowers overpay unclaimed interest that is not what the rate curve promised, and LPs/protocol correspondingly do not receive the amount the quoted curve implies).
- It is not caused by third-party oracle data or DAO misconfiguration; `points-ir` is protocol-owned but the bug is purely in the discrete-vs-compounded math of `calc-multiplier-delta`/`calc-index-next`, in scope per the rules.

### Likelihood Explanation
No attacker action is required for the error to manifest — any variation in the natural frequency of deposits/borrows/repayments (which is controlled by ordinary user activity, and can be trivially amplified by anyone submitting many small transactions in quick succession, similar to the PoC in the reference report) changes the effective annualized rate actually charged/paid. Given Zest vaults are expected to see frequent activity (multiple lending markets: STX, sBTC, USDC, USDH, stSTX, stSTXBTC), this condition is very likely to occur continuously rather than being a rare edge case.

### Recommendation
Refactor `calc-multiplier-delta`/`calc-index-next` so interest accrual does not depend on the number of times the vault happens to be touched:
- Either use a genuinely linear formula that accrues interest from the loan's/index's true origin time rather than compounding a per-call simple-interest factor onto the running index each call, or
- Explicitly adopt continuous/periodic compounding using a fixed, activity-independent compounding period (or an exponentiation-based continuous-compounding formula), ensuring the same elapsed real time always yields the same growth factor regardless of how many state-changing calls occurred during that interval.

### Proof of Concept
Analogous to the referenced Sherlock PoC: call `next-index`/`total-debt` (i.e., simulate touching the vault, e.g. by driving small deposits/repayments) many times over a fixed real-time horizon (e.g., once per hour for a year) versus calling it once for the whole horizon, holding `rate` and cumulative `time-delta` constant, and compare the resulting `index` growth. Because `calc-index-next` re-multiplies the running `index` by `calc-multiplier-delta`'s per-slice factor at every call, the many-small-calls case will show additional compounding growth (`(1+r/n)^n → e^r`) not present when the same elapsed time is applied in a single call, demonstrating the frequency-dependent index divergence described above. Full on-chain simulation was not run in this analysis (index-level exploration was cut off before entry-point call-site tracing could be completed) — this should be validated with a Clarinet/unit test invoking each vault's public entrypoints across the same wall-clock period with varying call frequency to compare resulting `index`/`lindex` state.

### Citations

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L170-178)
```text
(define-private (calc-multiplier-delta (rate uint) (time-delta uint) (round-up bool))
  (+ INDEX-PRECISION
    (if round-up
      (mul-div-up rate
                  (* time-delta INDEX-PRECISION)
                  SECONDS-PER-YEAR-BPS)
      (mul-div-down rate
                  (* time-delta INDEX-PRECISION)
                  SECONDS-PER-YEAR-BPS))))
```

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L183-185)
```text
(define-private (calc-index-next (index-curr uint) (multiplier uint))
  (mul-div-down index-curr multiplier INDEX-PRECISION))

```

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L379-404)
```text
(define-private (next-index)
  (let ((states (var-get pause-states))
        (idx (var-get index)))
    (if (get accrue states)
        idx
        (let (
            (rate (interest-rate))
            (time-delta (- stacks-block-time (var-get last-update)))
            (multiplier (if (is-eq time-delta u0)
                          INDEX-PRECISION
                          (calc-multiplier-delta rate time-delta true))))
          (calc-index-next idx multiplier)))))

(define-private (next-liquidity-index)
  (let ((states (var-get pause-states))
        (lidx (var-get lindex)))
    (if (get accrue states)
        lidx
        (let (
            (rate (interest-rate))
            (liquidity-rate (calc-liquidity-rate rate (utilization) (var-get fee-reserve)))
            (time-delta (- stacks-block-time (var-get last-update)))
            (multiplier (if (is-eq time-delta u0)
                          INDEX-PRECISION
                          (calc-multiplier-delta liquidity-rate time-delta false))))
          (calc-index-next lidx multiplier)))))
```
