### Title
Vault `deposit` can mint zero shares for a nonzero underlying transfer, permanently losing user funds - ([File: mainnet/contracts/vault/v0-vault-stx.clar])

### Summary
The `mul-div-down` rounding in `convert-to-shares-preview` can return `0` shares for a nonzero deposit amount when `total-assets` has grown large relative to `total-supply` (e.g., after significant interest accrual/index growth or on a young vault with a very large early depositor). `deposit` transfers the user's underlying tokens into the vault and mints the (possibly zero) share amount, but unlike `redeem`, it does not assert the minted share amount is nonzero.

### Finding Description
Share conversion uses floor division: [1](#0-0) 

`deposit` calls this helper and only checks `amount > 0` and the slippage bound `inkind >= min-out`; it never checks that the computed `inkind` (shares to mint) is itself nonzero: [2](#0-1) 

Contrast this with `redeem`, which explicitly guards against a zero output: [3](#0-2) 

If a caller passes the default `min-out` of `u0` (or any value `<= 0`), `deposit` will succeed even when `convert-to-shares-preview` rounds `amount * total-supply / total-assets` down to `0`: `receive-underlying` pulls the user's tokens into the vault and `var-set assets` increases, but `ft-mint? zft 0 recipient` mints nothing to the user. The deposited value is absorbed into `total-assets`, silently diluting the value-per-share upward for existing ztoken holders while the depositor receives no claim at all. The same pattern is repeated identically in `v0-vault-stx.clar`, `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-ststxbtc.clar`, and `v0-vault-usdh.clar` (all built from the same `convert-to-shares-preview` / `deposit` template): [4](#0-3) [5](#0-4) 

### Impact Explanation
Direction of error: the depositor loses 100% of the deposited underlying value with rounding always in favor of the existing ztoken supply (share price increases without minting new shares to the depositor). Existing ztoken holders profit passively (their shares become worth marginally more), while the affected depositor's principal is directly and permanently lost with no ability to recover it (no shares were minted to redeem later). This is a direct, permanent loss of user funds at rest, matching the in-scope Critical impact class.

### Likelihood Explanation
The condition requires `total-assets` (post interest accrual) to be large enough relative to `total-supply` that `amount * total-supply / total-assets` floors to zero for the attempted deposit amount. This becomes increasingly likely for vaults with a high per-share value (e.g., after long-running interest accrual, or in low-decimal/high-value underlying assets such as sBTC) combined with small deposit amounts, or is trivially reachable if a malicious/careless first depositor inflates `total-assets` relative to `total-supply` before a victim deposits a small amount. Because UIs typically pass `min-out = u0` by default for straightforward deposits (there's no incentive for the depositor to set a nonzero `min-out` on a deposit, unlike a swap), the missing zero-check is easily triggered without any attacker action beyond timing a deposit after sufficient index growth or supply skew.

### Recommendation
Add an explicit output-zero guard in every vault's `deposit` function, mirroring the existing `redeem` protection:
```clarity
(asserts! (> inkind u0) ERR-OUTPUT-ZERO)
```
placed alongside the other `asserts!` calls before `receive-underlying`/`ft-mint?` are invoked, in `v0-vault-stx.clar`, `v0-vault-sbtc.clar`, `v0-vault-ststx.clar`, `v0-vault-ststxbtc.clar`, `v0-vault-usdc.clar`, and `v0-vault-usdh.clar`.

### Proof of Concept
1. Let a vault reach a state where `total-assets` (post `accrue`, including accrued interest) is large relative to `total-supply` — e.g., through normal interest accrual over time, or a large early depositor followed by heavy borrowing/interest accrual that inflates `total-assets` without proportionally inflating `total-supply`.
2. A user calls `deposit(amount, min-out: u0, recipient: user)` with a small `amount` such that `mul-div-down(amount, total-supply, total-assets)` floors to `0`. [2](#0-1) 
3. `receive-underlying` transfers `amount` of the underlying token from the user into the vault, `var-set assets` increases by `amount`, but `ft-mint? zft inkind recipient` mints `0` shares to the user.
4. The `deposit` call returns `(ok 0)` successfully; the user has permanently lost `amount` of underlying tokens with no corresponding ztoken balance to redeem.

### Citations

**File:** mainnet/contracts/vault/v0-vault-usdc.clar (L306-313)
```text
(define-private (convert-to-shares-preview (amount uint))
  (let ((ta (total-assets-preview))
        (ts (total-supply-preview)))
    (if (is-eq ts u0)
        amount
        (if (is-eq ta u0)
            u0
            (mul-div-down amount ts ta)))))
```

**File:** mainnet/contracts/vault/v0-vault-usdc.clar (L761-779)
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
```

**File:** mainnet/contracts/vault/v0-vault-usdc.clar (L795-817)
```text
(define-public (redeem (amount uint) (min-out uint) (recipient principal))
  (let (
    (states (var-get pause-states))
    (u (try! (accrue)))
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

**File:** mainnet/contracts/vault/v0-vault-sbtc.clar (L761-793)
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

**File:** mainnet/contracts/vault/v0-vault-ststxbtc.clar (L763-795)
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
