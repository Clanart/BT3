### Title
Token-2022 Transfer Fee Causes Vault Under-Collateralization in `init_transfer` (Native Path) — (`solana/programs/bridge_token_factory/src/instructions/user/init_transfer.rs`)

---

### Summary

In the native-token path of `InitTransfer::process`, the program calls `transfer_checked(payload.amount, ...)` to move tokens into the vault, then posts a Wormhole message containing the same `payload.amount`. For a Token-2022 mint with a transfer fee extension, `transfer_checked` debits `payload.amount` from the sender but credits only `payload.amount − withheld_fee` to the vault. NEAR receives and processes the full `payload.amount`. The vault is therefore permanently under-collateralized by the withheld fee, breaking the invariant that `vault_balance == Σ locked_amounts`.

---

### Finding Description

**Entrypoint:** `InitTransfer::process` — publicly callable by any unprivileged user.

**Relevant code path:**

1. `transfer_checked` is called with `payload.amount` as the transfer amount: [1](#0-0) 

2. The Wormhole message is then posted with the same `payload.amount` (via `self.amount` in `serialize_for_near`): [2](#0-1) [3](#0-2) 

**Token-2022 transfer fee mechanics:** When `transfer_checked(amount)` is invoked on a mint with the `TransferFeeConfig` extension, the SPL Token-2022 program debits `amount` from the sender but credits only `amount − withheld_fee` to the recipient (vault). The fee is stored in the vault account's `withheld_amount` field and is not part of the vault's spendable balance.

**No guard exists** against mints with transfer fee extensions. The `token_program` field accepts `TokenInterface` (i.e., Token-2022 is explicitly supported): [4](#0-3) 

**On `finalize_transfer`:** When NEAR instructs Solana to release the full `payload.amount` back to a recipient, `transfer_checked(data.amount, ...)` is called from the vault: [5](#0-4) 

The vault holds only `payload.amount − withheld_fee`, so either:
- The transfer fails (funds are permanently locked on NEAR), or
- Other users' deposited collateral is consumed to cover the shortfall.

---

### Impact Explanation

**High — Balance/accounting corruption that breaks bridge collateralization.**

- Vault is under-collateralized by `withheld_fee` per `init_transfer` call.
- Accumulated across many transfers, the deficit grows unboundedly.
- `finalize_transfer` for the full amount will either revert (locking NEAR-side funds) or drain collateral belonging to other users.
- No privileged access is required; any user with a Token-2022 fee-bearing token can trigger this.

---

### Likelihood Explanation

Token-2022 mints with transfer fees are a standard, deployed feature of the Solana ecosystem (e.g., many DeFi tokens use them). The bridge explicitly supports Token-2022 via `TokenInterface`. Any user can register a native token with a fee-bearing mint and call `init_transfer`. The path is fully reachable without any privileged role.

---

### Recommendation

Before posting the Wormhole message, measure the actual vault balance delta rather than trusting `payload.amount`:

```rust
let vault_balance_before = vault.amount;
transfer_checked(..., payload.amount, self.mint.decimals)?;
vault.reload()?;
let actual_received = vault.amount - vault_balance_before;
// Use actual_received in the Wormhole message, not payload.amount
```

Alternatively, explicitly reject mints with a non-zero `TransferFeeConfig` extension by inspecting the mint's extension list before proceeding.

---

### Proof of Concept

```
1. Deploy a Token-2022 mint with TransferFeeConfig: transfer_fee_basis_points = 100 (1%).
2. Register the mint as a native token (vault is created).
3. Call init_transfer with payload.amount = 1_000_000.
   - transfer_checked debits 1_000_000 from user.
   - Vault receives 990_000 (withheld_fee = 10_000 stored in vault.withheld_amount).
   - Wormhole message posts amount = 1_000_000.
4. NEAR credits recipient with 1_000_000.
5. Recipient bridges back 1_000_000 to Solana via finalize_transfer.
   - transfer_checked(1_000_000) from vault fails (vault.amount == 990_000).
   - OR if other deposits exist, 10_000 extra tokens are taken from them.
Assert: vault_balance_after_step3 - vault_balance_before_step3 == 990_000 ≠ 1_000_000 (message_amount).
```

### Citations

**File:** solana/programs/bridge_token_factory/src/instructions/user/init_transfer.rs (L68-68)
```rust
    pub token_program: Interface<'info, TokenInterface>,
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/init_transfer.rs (L90-102)
```rust
            transfer_checked(
                CpiContext::new(
                    self.token_program.to_account_info(),
                    TransferChecked {
                        from: self.from.to_account_info(),
                        to: vault.to_account_info(),
                        authority: self.user.to_account_info(),
                        mint: self.mint.to_account_info(),
                    },
                ),
                payload.amount.try_into().map_err(|_| error!(ErrorCode::InvalidArgs))?,
                self.mint.decimals,
            )?;
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/init_transfer.rs (L123-127)
```rust
        self.common.post_message(payload.serialize_for_near((
            self.common.sequence.sequence,
            self.user.key(),
            self.mint.key(),
        ))?)?;
```

**File:** solana/programs/bridge_token_factory/src/state/message/init_transfer.rs (L32-32)
```rust
        self.amount.serialize(&mut writer)?;
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs (L103-116)
```rust
            transfer_checked(
                CpiContext::new_with_signer(
                    self.token_program.to_account_info(),
                    TransferChecked {
                        from: vault.to_account_info(),
                        to: self.token_account.to_account_info(),
                        authority: self.authority.to_account_info(),
                        mint: self.mint.to_account_info(),
                    },
                    &[&[AUTHORITY_SEED, &[self.config.bumps.authority]]],
                ),
                data.amount.try_into().map_err(|_| error!(ErrorCode::AmountOverflow))?,
                self.mint.decimals,
            )?;
```
