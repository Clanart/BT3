### Title
Liquidity-index compounding is manipulable by frequent small-time-delta calls, permanently under-accruing depositor yield used in zToken oracle pricing - ([File: mainnet/contracts/vault/v0-vault-usdc.clar])

### Summary
The vaults (`v0-vault-usdc.clar`, `v0-vault-sbtc.clar`, `v0-vault-stx.clar`, `v0-vault-ststx.clar`, `v0-vault-usdh.clar`, `v0-vault-ststxbtc.clar`) accrue the liquidity index (`lindex`) by repeatedly multiplying it with a per-period multiplier computed from `time-delta = stacks-block-time - last-update`, floored via `mul-div-down`. Because `last-update` is reset to the current block time on every state-changing vault call, an attacker (or any frequent caller) can force many consecutive tiny-`time-delta` accrual steps instead of a few larger ones. Each step's fractional interest is floored away by `mul-div-down`, and floor-rounding losses accumulate irreversibly across steps — the same "frequent-call, precision-loss" mechanism described in the referenced report, but landing on the liquidity index that feeds directly into the zToken oracle price (`resolve-ztoken`), a callcode price transformation explicitly reachable in the pricing path.

### Finding Description
`next-liquidity-index` computes the new liquidity index as: [1](#0-0) 

using `calc-multiplier-delta` with `round-up = false`, which floors the interest contribution for the elapsed `time-delta`: [2](#0-1) 

and `calc-index-next` performs another floor division when compounding: [3](#0-2) 

`last-update` is reset to `stacks-block-time` on every accrual, so any vault interaction (e.g. minimal supply/repay through the authorized market path) collapses the elapsed window for the *next* accrual back to a single block interval. Repeating this every block, over what would otherwise be one large elapsed window, replaces one large-`time-delta` floor-rounding operation with many small-`time-delta` floor-rounding operations — each of which discards a larger relative fraction of true interest (the classic "many small floors lose more than one big floor" precision-loss pattern from the referenced report).

This lindex feeds directly into the oracle's zToken price resolution, the callcode transformation path: [4](#0-3) 
which is used to price zTokens both as collateral and as debt throughout `market.clar`'s health/price-resolve flow.

### Impact Explanation
The manipulated (systematically under-accrued) `lindex` permanently understates the true yield earned by liquidity providers, since floored fractional interest per compounding step can never be recovered in later periods — it is lost forever, not merely deferred. This is a permanent freezing/loss of unclaimed yield owed to zToken holders, which also propagates into the oracle-reported zToken price used for collateral/debt valuation throughout the market. This lands on the in-scope **High** impact category: permanent freezing of unclaimed yield.

### Likelihood Explanation
Likelihood is **Medium**: triggering the vault's accrual path is possible via any minimal-value supply/repay routed through the authorized market contract, and no `require`/minimum-amount check prevents frequent, low-value calls from resetting `last-update` every block. The attack is economically similar to the original report's — cheap, repeatable calls that force worst-case small `time-delta` accrual windows — though it requires sustained transaction spam across many blocks rather than many calls within a single block (since `stacks-block-time` only changes once per block).

### Recommendation
Avoid resetting `last-update`/compounding via repeated floor-rounded small-`time-delta` steps. Either (a) always round liquidity-index multiplier contributions up (never down) so precision loss favors depositors rather than being lost, mirroring the debt-index behavior which already rounds up, or (b) track and carry forward the rounding remainder between accruals so fractional interest is not discarded, or (c) use a higher-precision intermediate representation for the multiplier calculation to make the floor-rounding negligible regardless of call frequency.

### Proof of Concept
1. Deploy/observe `v0-vault-usdc.clar` with a nonzero utilization and interest rate such that `liquidity-rate * 1 * INDEX-PRECISION / SECONDS-PER-YEAR-BPS` floors to 0 (or a value smaller than the true fractional interest) for `time-delta = 1` block interval.
2. Case A (baseline): Let one hour elapse with no vault interaction, then perform a single supply/withdraw to trigger one large-`time-delta` accrual of `lindex`.
3. Case B (attack): Every block during that same hour, submit a minimal-value supply (e.g., 1 unit) through the market to force `accrue`/`next-liquidity-index` to run with `time-delta` equal to one block interval each time.
4. Compare the resulting `lindex` (via `get-index`/`get-total-assets`) between Case A and Case B — Case B accrues strictly less liquidity index due to repeated floor-rounding, understating zToken price via `resolve-ztoken` and permanently denying depositors the difference in yield.

### Citations

**File:** mainnet/contracts/vault/v0-vault-usdc.clar (L184-185)
```text
  (mul-div-down index-curr multiplier INDEX-PRECISION))

```

**File:** mainnet/contracts/vault/v0-vault-usdc.clar (L390-402)
```text
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

**File:** mainnet/contracts/vault/v0-vault-sbtc.clar (L171-179)
```text
  (+ INDEX-PRECISION
    (if round-up
      (mul-div-up rate
                  (* time-delta INDEX-PRECISION)
                  SECONDS-PER-YEAR-BPS)
      (mul-div-down rate
                  (* time-delta INDEX-PRECISION)
                  SECONDS-PER-YEAR-BPS))))

```

**File:** mainnet/contracts/market/v0-4-market.clar (L343-347)
```text
(define-private (resolve-ztoken (p uint) (aid uint))
  (let ((cached (unwrap! (get-cached-indexes aid) ERR-ORACLE-CALLCODE))
        (cached-lindex (get lindex cached))
        (scaled (* p cached-lindex)))
    (ok (div-down scaled INDEX-PRECISION))))
```
