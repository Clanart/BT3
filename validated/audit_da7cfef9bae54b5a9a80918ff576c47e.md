### Title
Missing ratio/exchange-rate adjustment for `stSTXbtc` in `get-asset-price` causes mispriced collateral - (File: `mainnet/contracts/utility/v0-1-data.clar`)

### Summary
`get-asset-price` in `v0-1-data.clar` applies an exchange-rate/ratio multiplier for every liquid-staking derivative it prices *except* `stSTXbtc`, which is instead priced as a raw 1:1 copy of the STX/USD Pyth feed. This mirrors the "missing update call" bug class from the referenced report: a value-adjustment step that is consistently applied for sibling assets is silently skipped for one specific asset, leaving the registry/price state for that asset stale and inconsistent with its true economic value.

### Finding Description
`get-asset-price` computes prices per asset ID with a distinct branch for each asset: [1](#0-0) 
For `stSTX`, the function explicitly reads a dedicated ratio (`get-ststx-ratio`) and multiplies it into the STX price, because `stSTX` is a distinct, appreciating liquid-staking token whose redemption value versus STX changes over time. [2](#0-1) 
For `stSTXbtc`, however, the price is set directly to `(get-pyth-price PYTH-STX)` with **no ratio/exchange-rate adjustment at all** — treating 1 `stSTXbtc` as worth exactly 1 STX regardless of the token's actual redemption rate.

This is inconsistent with how the protocol treats every other liquid-staking wrapper:
- `stSTX` → STX price × `get-ststx-ratio()`
- `zSTX`, `zsBTC`, `zstSTX`, `zUSDC`, `zUSDH`, `zstSTXbtc` → underlying price × vault liquidity index

`stSTXbtc`'s underlying token is a distinct on-chain contract (`ststxbtc-token-v2`, deployed at a separate principal), configured in its own vault: [3](#0-2) 
and documented as a liquid-staking derivative with its own, separately accruing exchange rate ("BTC-denominated yield"), distinct from plain STX: [4](#0-3) 

Because the pricing function omits the analogous ratio lookup/multiplication that every comparable derivative asset receives, the on-chain price used for LTV/health calculations for `stSTXbtc` diverges from its real redemption value as soon as that ratio departs from 1.0 (which is guaranteed over time, since liquid-staking exchange rates monotonically increase as staking rewards accrue).

### Impact Explanation
Since `stSTXbtc`'s true redemption value grows above 1 STX over time while the protocol continues to price it at exactly 1× STX, the protocol systematically **undervalues** this collateral in every collateral, borrow, and health/liquidation calculation that reads `get-asset-price` for asset ID `stSTXbtc` (aid u10) and its wrapped ztoken `zstSTXbtc` (aid u11, which itself multiplies this already-wrong base price by the vault liquidity index). This mispricing:
- Understates users' true collateral value, unnecessarily restricting borrowing capacity and potentially freezing usable collateral value (temporary freezing of funds), and
- Can push otherwise-healthy positions below liquidation thresholds, triggering unwarranted liquidations that transfer value from `stSTXbtc` collateral holders to liquidators at an unfair, understated price (temporary freezing / misallocation of funds for the borrower, profit for the liquidator).

If the true exchange rate ever moves in the opposite economic direction (e.g., due to how `ststxbtc-token-v2`'s BTC-denominated yield mechanics actually work), the same missing-conversion bug could instead **overvalue** the asset, letting borrowers extract more debt than their real collateral backs — a direct insolvency/theft vector. Without visibility into the exact redemption mechanics of `ststxbtc-token-v2` (not present in this index), the precise direction cannot be fully confirmed, but the missing ratio call itself is the demonstrable root cause, on par with the sibling `stSTX` code path that correctly performs this adjustment.

### Likelihood Explanation
This triggers on every price read for `stSTXbtc`/`zstSTXbtc` — i.e., every borrow, collateral-add/remove, and liquidation health check involving this asset — since the price function is unconditionally missing the ratio step for this one asset ID. It requires no attacker action beyond simply holding or trading against this collateral type; it is a deterministic protocol bug, not a race condition.

### Recommendation
Add the missing exchange-rate lookup for `stSTXbtc`, mirroring the pattern used for `stSTX` (`get-ststx-ratio`) — introduce an equivalent `get-ststxbtc-ratio` (or reuse the vault's underlying redemption-rate accessor) and multiply it into the STX (or appropriate base) price before returning it from `get-asset-price`, consistent with how every other derivative asset's price is computed in this function.

### Proof of Concept
1. Compare the `stSTX` branch, which multiplies `stx-price` by `(unwrap-panic (get-ststx-ratio))`, against the `stSTXbtc` branch, which returns `(default-to u0 (get-pyth-price PYTH-STX))` unmodified. [5](#0-4) 
2. As `ststxbtc-token-v2`'s real redemption rate versus STX drifts away from 1.0 over time (as all liquid-staking wrappers do), every downstream consumer of `get-asset-price` for `stSTXbtc`/`zstSTXbtc` (collateral valuation, LTV/health checks, liquidation triggers in `v0-4-market.clar`) computes an incorrect USD notional for this asset, producing systematically wrong health verdicts for any account holding it as collateral or debt.

### Citations

**File:** mainnet/contracts/utility/v0-1-data.clar (L545-587)
```text
  ;; stSTX - STX price x stSTX ratio
  (if (is-eq aid stSTX) 
      (let ((stx-price (default-to u0 (get-pyth-price PYTH-STX)))
            (ratio (unwrap-panic (get-ststx-ratio))))
        (mul-div-down stx-price ratio STSTX-RATIO-DECIMALS))
  ;; USDC - Pyth oracle
  (if (is-eq aid USDC) (default-to u0 (get-pyth-price PYTH-USDC))
  ;; USDH - DIA oracle
  (if (is-eq aid USDH) (default-to u0 (get-dia-price DIA-USDH))
  ;; zSTX - STX price x liquidity index
  (if (is-eq aid zSTX)
      (let ((stx-price (default-to u0 (get-pyth-price PYTH-STX)))
            (lindex (get-vault-liquidity-index STX)))
        (mul-div-down stx-price lindex INDEX-PRECISION))
  ;; zsBTC - BTC price x liquidity index
  (if (is-eq aid zsBTC)
      (let ((btc-price (default-to u0 (get-pyth-price PYTH-BTC)))
            (lindex (get-vault-liquidity-index sBTC)))
        (mul-div-down btc-price lindex INDEX-PRECISION))
  ;; zstSTX - stSTX price x liquidity index (stSTX already includes ratio)
  (if (is-eq aid zstSTX)
      (let ((stx-price (default-to u0 (get-pyth-price PYTH-STX)))
            (ratio (unwrap-panic (get-ststx-ratio)))
            (ststx-price (mul-div-down stx-price ratio STSTX-RATIO-DECIMALS))
            (lindex (get-vault-liquidity-index stSTX)))
        (mul-div-down ststx-price lindex INDEX-PRECISION))
  ;; zUSDC - USDC price x liquidity index
  (if (is-eq aid zUSDC)
      (let ((usdc-price (default-to u0 (get-pyth-price PYTH-USDC)))
            (lindex (get-vault-liquidity-index USDC)))
        (mul-div-down usdc-price lindex INDEX-PRECISION))
  ;; zUSDH - USDH price x liquidity index
  (if (is-eq aid zUSDH)
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

**File:** mainnet/contracts/vault/v0-vault-ststxbtc.clar (L18-22)
```text
;; -- Core configuration
(define-constant UNDERLYING 'SP4SZE494VC2YC5JYG7AYFQ44F5Q4PYV7DVMDPBG.ststxbtc-token-v2)
(define-constant NAME "Zest stSTXbtc")
(define-constant SYMBOL "zstSTXbtc")
(define-constant DECIMALS u6)
```

**File:** docs/vaults.md (L232-236)
```markdown
### 6. vault-ststxbtc
- **Underlying:** Liquid staked STX (BTC yield)
- **Ztoken:** zstSTXbtc
- **Purpose:** Liquid staking with BTC-denominated yield

```
