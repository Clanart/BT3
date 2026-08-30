Confirmed across all six mainnet vault contracts (`v0-vault-usdc.clar`, `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-ststxbtc.clar`, `v0-vault-stx.clar`, `v0-vault-usdh.clar`): the `redeem` function explicitly guards against zero output via `(asserts! (> inkind u0) ERR-OUTPUT-ZERO)`, but the symmetric `deposit` function has no equivalent check on `inkind` (the minted shares), only on the raw `amount`.

### Title
Deposit can mint zero shares for a non-zero underlying deposit due to unchecked rounding in share conversion - ([File: mainnet/contracts/vault/v0-vault-usdc.clar], and identically in v0-vault-sbtc.clar, v0-vault-ststx.clar, v0-vault-ststxbtc.clar, v0-vault-stx.clar, v0-vault-usdh.clar)

### Summary
The vault `deposit` function computes minted shares via `convert-to-shares-preview`, a floor-rounding conversion, but never verifies the result is non-zero before minting and accepting the deposit. This is the same class of bug the external Lido report flags for `wrap()`/`unwrap()`: a share/amount conversion that can legitimately return zero is used without a `> 0` guard, silently accepting value at a "price" of zero shares.

### Finding Description
`convert-to-shares-preview` performs `(mul-div-down amount ts ta)` (floor division) whenever both `total-supply` and `total-assets` are non-zero: [1](#0-0) 

In `deposit`, this value (`inkind`) is used to gate slippage against `min-out`, but there is no assertion that `inkind > 0`: [2](#0-1) 

Compare this to `redeem` in the very same file, which explicitly rejects a zero-output conversion result with `ERR-OUTPUT-ZERO` before proceeding: [3](#0-2) 

Since `min-out` is a caller-supplied parameter (commonly `0` for a simple deposit with no explicit slippage protection), the slippage check `(>= inkind min-out)` is satisfied trivially when `inkind` is `0`. The only positivity check in `deposit` is on the raw input `amount`, not on the derived `inkind` (shares minted): [4](#0-3) 

Once `total-assets` (`ta`) grows relative to `total-supply` (`ts`) — which is the normal, expected state after interest accrual raises the share price above 1:1 — `mul-div-down amount ts ta` floors to `0` for any `amount` small enough that `amount * ts < ta`. In that case `deposit` still: transfers the underlying asset in via `receive-underlying`, mints `0` shares to the recipient, and increases `assets` by the full deposited `amount`: [5](#0-4) 

The net effect is identical in kind to the wstETH issue cited in the report: a conversion function legitimately returns zero, and the caller proceeds anyway, so real value is deposited but the depositor receives zero shares representing it.

### Impact Explanation
The depositor's underlying tokens are added to `assets` (the vault's asset pool) but no `zft` shares are minted to them, so their contributed value is permanently and irrecoverably transferred to existing shareholders (the price-per-share for all other holders increases with no offsetting share dilution). This is a direct loss of user funds at rest — the depositing user has no claim on the value they deposited. This falls into the Critical impact category (permanent loss of user funds) or High (permanent freezing of unclaimed yield/funds) depending on materiality per instance, matching the theft/permanent-freezing classes.

### Likelihood Explanation
This requires the share price (`ta/ts`) to already be above `1` (i.e., meaningful interest has accrued), which is the vault's normal steady-state after any usage, and a caller depositing an `amount` small enough relative to that price ratio, and/or a caller not specifying a protective `min-out`. Both conditions are easily met — small deposits are common (e.g., dust deposits, or deposits into a high-decimal/high-price-ratio vault), and callers relying on default/naive integrations often pass `min-out = 0`.

### Recommendation
Add an explicit check in every vault's `deposit` function mirroring the existing `redeem` guard:
```clarity
(asserts! (> inkind u0) ERR-OUTPUT-ZERO)
```
placed alongside the other `asserts!` calls in `deposit`, before `receive-underlying` and `ft-mint?` are invoked, in `v0-vault-usdc.clar`, `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-ststxbtc.clar`, `v0-vault-stx.clar`, and `v0-vault-usdh.clar` (and their `local-testing` counterparts for consistency).

### Proof of Concept
1. Let a vault reach a state where `total-assets (ta)` and `total-supply (ts)` satisfy `ta > ts` sufficiently (e.g., `ta = 10_000_000`, `ts = 1_000_000`, i.e., price per share = 10 underlying units) through normal interest accrual over time.
2. Caller calls `deposit(amount=5, min-out=0, recipient=attacker-or-victim)`.
3. `convert-to-shares-preview(5)` computes `mul-div-down(5, 1_000_000, 10_000_000) = 0` (floor rounding).
4. `(asserts! (>= inkind min-out) ERR-SLIPPAGE)` passes since `0 >= 0`.
5. `deposit` transfers `5` underlying units from the caller into the vault, mints `0` `zft` shares to `recipient`, and increases `assets` by `5`.
6. The caller has permanently lost the `5` underlying units deposited, with no way to redeem them, while all other vault shareholders' shares become worth marginally more. [6](#0-5)

### Citations

**File:** mainnet/contracts/vault/v0-vault-usdc.clar (L306-317)
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
```

**File:** mainnet/contracts/vault/v0-vault-usdc.clar (L761-793)
```text
(define-public (deposit (amount uint) (min-out uint) (recipient principal))
    (let (
      (states (var-get pause-states))
      (u (try! (accrue)))
      (account contract-caller)
      (CAP-SUPPLY (var-get cap-supply))
      (current-assets (var-get assets))
      (inkind (convert-to-shares-preview amount)))

    (asserts! (not (get deposit states)) ERR-PAUSED)
    (asserts! (var-get initialized) ERR-INIT)
    (asserts! (not (var-get in-flashloan)) ERR-REENTRANCY)
    (asserts! (> amount u0) ERR-AMOUNT-ZERO)
    (asserts! (>= inkind min-out) ERR-SLIPPAGE)
    (asserts! (<= (+ current-assets amount) CAP-SUPPLY) ERR-SUPPLY-CAP-EXCEEDED)

    (try! (receive-underlying amount account))
    (try! (ft-mint? zft inkind recipient))
    (var-set assets (+ current-assets amount))

    (print {
      action: "deposit",
      caller: contract-caller,
      data: {
        depositor: account,
        recipient: recipient,
        amount: amount,
        shares-minted: inkind,
        assets: (+ current-assets amount)
      }
    })

    (ok inkind)))
```

**File:** mainnet/contracts/vault/v0-vault-usdc.clar (L804-810)
```text
    (inkind (convert-to-assets-preview amount)))

  (asserts! (>= current-assets inkind) ERR-INSUFFICIENT-ASSETS)
  (asserts! (not (get redeem states)) ERR-PAUSED)
  (asserts! (> amount u0) ERR-AMOUNT-ZERO)
  (asserts! (> inkind u0) ERR-OUTPUT-ZERO)
  (asserts! (>= inkind min-out) ERR-SLIPPAGE)
```
