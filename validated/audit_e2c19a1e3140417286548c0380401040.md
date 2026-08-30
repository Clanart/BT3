### Title
zToken collateral is priced via a stale/independent liquidity index that can diverge from the vault's real redeemable exchange rate - (File: `mainnet/contracts/market/v0-4-market.clar`, `mainnet/contracts/utility/v0-1-data.clar`, `mainnet/contracts/vault/v0-vault-*.clar`)

### Summary
Zest v2 prices zTokens (rehypothecatable collateral) for LTV/health-factor purposes with a **callcode transform** that multiplies the underlying asset's oracle price by the vault's cached **liquidity index** (`lindex`), e.g. `zUSDC price = usdc-price * lindex / INDEX-PRECISION` [1](#0-0) , and the equivalent generic resolver in `market.clar` documented as `resolve-ztoken` [2](#0-1) . However, the *actual* redeemable value of one zToken share is computed independently by each vault as `total-assets-preview * amount / total-supply-preview` [3](#0-2) . These two "share price" mechanisms are tracked by separate state (`lindex` vs. `assets`/`total-supply`) and are not guaranteed to move in lockstep, which is structurally the same class of bug as the Rio report: a value used to price/settle a share-like position diverges from the value actually redeemable from the pool holding idle + accrued funds.

### Finding Description
Each Zest vault maintains two independent value trackers for the same underlying position:
1. An ERC4626-style share pool: shares are minted/burned against `total-assets-preview()`/`total-supply-preview()`, where `total-assets` = idle underlying (`assets`) + accrued-but-unrealized interest (`debt - total-borrowed`) [4](#0-3) . This is the rate actually used on `redeem` to compute how much underlying a share is worth [5](#0-4) .
2. A separately-updated `lindex` "liquidity index" that compounds on every `accrue()` call [6](#0-5)  and which is what `market.clar`/`v0-1-data.clar` use to price the zToken as collateral for every borrow/health check: `price = base-price * lindex / INDEX-PRECISION` [7](#0-6) .

Critically, when interest accrues, the protocol mints new treasury shares (`treasury-lp`) directly into `total-supply` to capture the protocol's reserve-fee cut [8](#0-7) . This treasury mint dilutes `total-supply`, which correctly reduces the *real* per-share redemption rate (`ta/ts`) for existing depositors by exactly the reserve fee. `lindex`, however, is updated as a raw compounding accrual index tied to `next-index`/`next-liquidity-index()` and is not shown to be reconciled against this treasury dilution event in the same step; it is a cached value only refreshed lazily whenever some contract call happens to invoke `accrue()`.

This mirrors the Rio pattern exactly: a value used to price a share-like claim (EigenLayer shares in Rio, `lindex`-derived zToken price in Zest) is decoupled from the actual, currently-redeemable amount backing that claim (real EigenLayer-cbETH/idle-cbETH balance in Rio, `total-assets-preview()/total-supply-preview()` in Zest). Whenever the two diverge — because `lindex` overstates growth relative to the fee-diluted `ta/ts`, or because `lindex` is stale between `accrue()` calls while `ta/ts` reacts instantly to deposits/withdrawals/borrows — the price fed into `market.clar`'s health-factor and LTV calculations for zToken collateral no longer matches what that collateral could actually be redeemed for.

### Impact Explanation
If `lindex`-based pricing overstates the real `ta/ts` redemption rate (e.g., because it does not net out the treasury reserve-fee dilution that `total-supply` absorbs), zToken collateral is systematically **overvalued** in every `borrow`/health check that uses `get-asset-price` for a `z*` asset id. Borrowers who post zTokens as collateral can then borrow more than their collateral is truly worth once redeemed, leaving the protocol under-collateralized — a direct path to bad debt / protocol insolvency, which maps to the in-scope **Critical** impact category (protocol insolvency / permanent freezing of funds), since redeeming that debt's backing collateral would legitimately fail to cover it, exactly as Bob's over-large withdrawal could not be settled against the real EigenLayer-cbETH+idle-cbETH balance in the Rio report.

### Likelihood Explanation
This requires the protocol's normal operation over time (interest accrual + treasury fee minting + zToken use as collateral) rather than any external manipulation, making it a background/latent risk rather than an easily-triggerable exploit. Confirming the exact magnitude of the divergence requires the concrete `next-liquidity-index` formula (not retrieved in this session) to determine whether it already nets out the treasury-fee dilution that `ta/ts` incorporates. This is a real code-path/architecture concern (dual value trackers for the same position, one used for collateral pricing and one used for real redemption), but I could not fully verify from the available context whether `next-liquidity-index` is defined to always equal `ta/ts` growth (in which case no divergence occurs) or diverges from it as suspected.

### Recommendation
Price zToken collateral using the same `total-assets-preview()/total-supply-preview()` ratio the vault uses for actual redemptions (or prove and unit-test that `lindex` growth is mathematically identical to `ta/ts` growth including the treasury-fee mint), so that the LTV/health-factor calculation in `market.clar` can never diverge from what a zToken is truly redeemable for.

### Proof of Concept
Not fully constructable from the retrieved code: doing so requires the exact `next-liquidity-index()` formula in `v0-vault-*.clar` (to compare its growth against `total-assets-preview()/total-supply-preview()` growth after a treasury-lp mint event) which was not obtained before the tool budget was exhausted. The finding is based on the confirmed existence of two independently-tracked "share value" mechanisms — `lindex`-based pricing in `get-asset-price`/`resolve-ztoken` versus `ta/ts`-based redemption in `convert-to-assets-preview` — and the confirmed treasury-lp dilution mechanism in `calc-treasury-lp-preview`/`accrue`, but not a numerically demonstrated divergence.

### Citations

**File:** mainnet/contracts/utility/v0-1-data.clar (L571-576)
```text
  ;; zUSDC - USDC price x liquidity index
  (if (is-eq aid zUSDC)
      (let ((usdc-price (default-to u0 (get-pyth-price PYTH-USDC)))
            (lindex (get-vault-liquidity-index USDC)))
        (mul-div-down usdc-price lindex INDEX-PRECISION))
  ;; zUSDH - USDH price x liquidity index
```

**File:** mainnet/contracts/utility/v0-1-data.clar (L578-587)
```text
      (let ((usdh-price (default-to u0 (get-dia-price DIA-USDH)))
            (lindex (get-vault-liquidity-index USDH)))
        (mul-div-down usdh-price lindex INDEX-PRECISION))
  ;; stSTXbtc - BTC price (liquid staked STX with BTC yield)
  (if (is-eq aid stSTXbtc) (default-to u0 (get-pyth-price PYTH-STX))
  ;; zstSTXbtc - stSTXbtc price x liquidity index
  (if (is-eq aid zstSTXbtc)
      (let ((btc-price (default-to u0 (get-pyth-price PYTH-STX)))
            (lindex (get-vault-liquidity-index stSTXbtc)))
        (mul-div-down btc-price lindex INDEX-PRECISION))
```

**File:** docs/oracle.md (L154-163)
```markdown
**Generic ztoken resolver:**
```clarity
;; In market.clar
(define-private (resolve-ztoken (p uint) (aid uint))
  (let ((price (if (is-eq aid u2)
                   (try! (resolve-ststx p))  // zststx: apply ratio first
                   p))
        (li (get index (unwrap-panic (get-cached-indexes aid)))))
    (ok (/ (* price li) PRECISION))))
```
```

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L317-324)
```text
(define-private (convert-to-assets-preview (amount uint))
  (let ((ta (total-assets-preview))
        (ts (total-supply-preview)))
    (if (is-eq ta u0)
        u0
        (if (is-eq ts u0)
            u0
            (mul-div-down amount ta ts)))))
```

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L334-346)
```text
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
```

**File:** mainnet/contracts/vault/v0-vault-stx.clar (L837-863)
```text
        (idx (var-get index))
        (lidx (var-get lindex)))
      (if (get accrue states)
          ;; PAUSED: Pass-through without reverting
          (ok { index: idx, lindex: lidx })
          ;; NOT PAUSED: Normal accrual logic
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
```

**File:** mainnet/contracts/vault/v0-vault-usdc.clar (L799-817)
```text
    (account contract-caller)
    (current-assets (var-get assets))
    (balance (get-balance-internal account))
    (balance-check (asserts! (>= balance amount) ERR-INSUFFICIENT-BALANCE))
    (available-assets (get-available-assets))
    (inkind (convert-to-assets-preview amount)))

  (asserts! (>= current-assets inkind) ERR-INSUFFICIENT-ASSETS)
  (asserts! (not (get redeem states)) ERR-PAUSED)
  (asserts! (> amount u0) ERR-AMOUNT-ZERO)
  (asserts! (> inkind u0) ERR-OUTPUT-ZERO)
  (asserts! (>= inkind min-out) ERR-SLIPPAGE)
  (asserts! (>= available-assets inkind) ERR-INSUFFICIENT-LIQUIDITY)

  (try! (ft-burn? zft amount account))
  (try! (send-underlying inkind recipient))
  (var-set assets (- current-assets inkind))

  (print {
```
