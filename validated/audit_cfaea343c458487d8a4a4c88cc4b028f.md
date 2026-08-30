### Title
Vault `last-update` initialized at contract deployment instead of protocol activation causes artificial index/lindex inflation feeding into zToken oracle price - (File: `mainnet/contracts/vault/v0-vault-usdc.clar`, and equivalently `v0-vault-stx.clar`, `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-ststxbtc.clar`, `v0-vault-usdh.clar`)

### Summary
Every vault contract initializes its interest-accrual timestamp at contract deployment time rather than at the time the vault actually becomes economically active (DAO proposal execution that sets the real interest-rate curve and authorizes the market). This is the same root-cause pattern as the external report: a "start"/reference timestamp is fixed at construction instead of at logical initialization, so a later elapsed-time calculation silently includes a stale pre-activation window. Here the stale window is baked into the liquidity index (`lindex`), which is consumed directly by the price-resolution/callcode pipeline for zTokens.

### Finding Description
Each vault declares:
```
(define-data-var last-update uint stacks-block-time)
``` [1](#0-0) 

This value is fixed the moment the contract is deployed on-chain — analogous to the reported `start = block.timestamp` being set in the constructor rather than in `init`. The vault's own `initialize()` function (which performs the first `MINIMUM-LIQUIDITY` deposit) never resets `last-update`: [2](#0-1) 

`last-update` is later used, unconditionally, to compute elapsed time for both the debt index and the liquidity index:
```
(define-private (next-index)
  ...
  (time-delta (- stacks-block-time (var-get last-update)))
  ...
(define-private (next-liquidity-index)
  ...
  (time-delta (- stacks-block-time (var-get last-update)))
  ...
``` [3](#0-2) 

`last-update` is only advanced when `accrue()` actually changes `index`/`lindex`: [4](#0-3) 

Before the DAO's activation proposal runs, `points-ir` (the rate curve) is `{util: u0, rate: u0}`, so `interest-rate()` returns 0 and no drift accumulates. But real interest-rate curves are only installed later, by a separate governance proposal that is executed independently of, and potentially long after, contract deployment (e.g. `v0-init.clar`, which sets caps, authorizes the market, creates egroups, and installs interest-rate curves in one batched call, but is a distinct on-chain transaction from the vault contract's own deployment). Because `last-update` was never reset to the moment the vault truly starts operating, the very first `accrue()` call after the rate curve is installed computes `time-delta` across the *entire* deploy-to-activation gap and applies the newly non-zero rate (which, for utilization = 0, is the curve's base rate — non-zero for several assets, e.g. sBTC's documented 5% base rate) over that whole stale interval in a single jump. This inflates `lindex` (and `index`) far beyond what real utilization/time would justify.

`lindex` is not an internal-only accounting number — it is consumed directly by the oracle price-resolution pipeline for every zToken:
```
(define-private (resolve-ztoken (p uint) (aid uint))
  (let ((cached (unwrap! (get-cached-indexes aid) ERR-ORACLE-CALLCODE))
        (cached-lindex (get lindex cached))
        (scaled (* p cached-lindex)))
    (ok (div-down scaled INDEX-PRECISION))))
``` [5](#0-4) 
which is selected via `resolve-callcode` for `CALLCODE-ZSTX`, `CALLCODE-ZSBTC`, `CALLCODE-ZUSDC`, `CALLCODE-ZUSDH`, `CALLCODE-ZSTSTXBTC`: [6](#0-5) 

So `zToken price = underlying_price * lindex / INDEX-PRECISION`. Any artificial one-shot inflation of `lindex` at first activation directly and permanently inflates the on-chain USD value assigned to every unit of that zToken used as collateral or debt in `market.clar`'s health/LTV calculations.

### Impact Explanation
An inflated `lindex` (and corresponding `index`) at the moment of activation permanently overstates the value of zToken collateral relative to the real assets actually backing it (only `MINIMUM-LIQUIDITY` has been deposited at that point). A user holding or minting zTokens right after this jump could post that collateral and borrow against a phantom valuation that has no real backing, i.e. over-borrow beyond the vault's true assets — this is a direct theft-of-funds / protocol-insolvency vector (Critical), since it lets a borrower extract more value from other lenders' deposits than their real collateral supports.

### Likelihood Explanation
This requires only the normal, expected deployment flow: vault contracts are deployed, and the DAO proposal that installs real interest-rate curves and authorizes the market runs afterward as a separate transaction/vote. Any nonzero delay between these two steps (which is routine for a governance-gated launch) creates the stale window; the first `accrue()` call after rate-curve installation will always misapply the new rate across that full stale window since `last-update` was never reset in `initialize()`.

### Recommendation
Reset `last-update` to `stacks-block-time` inside `initialize()` (the function that performs the vault's first real deposit / activation), instead of relying solely on the data-var's deploy-time default. This ensures the elapsed-time base for `next-index`/`next-liquidity-index` reflects the vault's actual economic start, not its contract-deployment timestamp — mirroring the recommended fix of assigning `start` in `init` rather than the constructor.

### Proof of Concept
1. Deploy `v0-vault-usdc.clar` (or any vault). `last-update` is set to deploy-time `stacks-block-time`; `points-ir` is `{util: u0, rate: u0}`.
2. Advance the chain by a large real-world time gap (e.g., simulate `stacks-block-time` advancing 30+ days) before the DAO activation proposal executes — a realistic scenario for a phased/governed launch.
3. Execute the DAO proposal (`v0-init.clar`) that sets a non-zero interest-rate curve (e.g., sBTC base rate 5%) and authorizes the market.
4. Trigger any vault action that calls `accrue()` (e.g., a small deposit). Observe `next-liquidity-index()`/`next-index()` compute `time-delta = stacks-block-time - last-update` spanning the entire 30+ day gap, applying the base rate over that whole period in one jump and inflating `lindex`/`index` accordingly.
5. Query the market's price for the corresponding zToken (via `price-resolve`/`resolve-ztoken`) and observe the reported USD value is inflated relative to the vault's actual backing assets (still ~`MINIMUM-LIQUIDITY`), demonstrating collateral over-valuation usable for over-borrowing.

### Citations

**File:** mainnet/contracts/vault/v0-vault-usdc.clar (L121-123)
```text
(define-data-var index uint INDEX-PRECISION)
(define-data-var lindex uint INDEX-PRECISION)
(define-data-var last-update uint stacks-block-time)
```

**File:** mainnet/contracts/vault/v0-vault-usdc.clar (L377-402)
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

**File:** mainnet/contracts/vault/v0-vault-usdc.clar (L490-507)
```text
;; -- Initialization ---------------------------------------------------------

(define-public (initialize)
  (begin
    (asserts! (not (var-get initialized)) ERR-ALREADY-INITIALIZED)
    (var-set initialized true)
    (try! (deposit MINIMUM-LIQUIDITY u0 NULL-ADDRESS))
    
    (print {
      action: "vault-initialize",
      caller: contract-caller,
      data: {
        vault: UNDERLYING,
        minimum-liquidity: MINIMUM-LIQUIDITY
      }
    })
    
    (ok true)))
```

**File:** mainnet/contracts/vault/v0-vault-usdc.clar (L841-864)
```text
          (let ((next (next-index))
                (nliq (next-liquidity-index))
                (scaled-principal (var-get principal-scaled))
                (old-debt (mul-div-down scaled-principal idx INDEX-PRECISION))
                (new-debt (mul-div-down scaled-principal next INDEX-PRECISION))
                (debt-delta (if (> new-debt old-debt) (- new-debt old-debt) u0))
                (reserve-inc (mul-div-down debt-delta (var-get fee-reserve) BPS))
                (treasury-lp (if (> reserve-inc u0) (mul-div-down reserve-inc (total-supply) (- (total-assets-preview) reserve-inc)) u0)))
            (if (not (is-eq idx next))
                (var-set index next)
                false)
            (if (not (is-eq lidx nliq))
                (var-set lindex nliq)
                false)
            (if (> treasury-lp u0)
                (try! (ft-mint? zft treasury-lp .dao-treasury))
                false)
            (if (or (not (is-eq idx next)) (not (is-eq lidx nliq)))
                (var-set last-update stacks-block-time)
                false)
            (ok { index: next, lindex: nliq })))))

(define-public (system-borrow (amount uint) (receiver principal))
  (let (
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
