### Title
Token-2022 Transfer Fee Extension Causes Vault Collateral Shortfall in `init_transfer` — (`solana/programs/bridge_token_factory/src/instructions/user/init_transfer.rs`)

---

### Summary

`log_metadata` accepts any Token-2022 mint as a native token without checking for a `transfer_fee` extension. When `init_transfer` subsequently calls `transfer_checked(payload.amount)`, the Token-2022 runtime withholds a fee from the vault, so the vault receives strictly less than `payload.amount`. The Wormhole message still reports the full `payload.amount` to NEAR, permanently breaking the 1:1 collateral invariant.

---

### Finding Description

**Step 1 — Unconstrained registration via `log_metadata`**

`LogMetadata` carries only one constraint on the mint:

```rust
#[account(
    constraint = !mint.mint_authority.contains(authority.key),
    mint::token_program = token_program,
)]
pub mint: Box<InterfaceAccount<'info, Mint>>,
``` [1](#0-0) 

There is no check for the Token-2022 `transfer_fee` extension. Any Token-2022 mint — including one with `transfer_fee_basis_points > 0` — passes this constraint and gets a vault PDA created for it.

**Step 2 — `transfer_checked` silently withholds the fee**

In `InitTransfer::process`, the native-token branch calls:

```rust
transfer_checked(
    CpiContext::new(..., TransferChecked {
        from: self.from.to_account_info(),
        to: vault.to_account_info(),
        ...
    }),
    payload.amount.try_into()...?,
    self.mint.decimals,
)?;
``` [2](#0-1) 

Under Token-2022 semantics, `transfer_checked` with a `transfer_fee` mint debits `payload.amount` from the sender but credits only `payload.amount − fee` to the vault; the fee is withheld inside the vault's token account and is not spendable by the bridge authority.

**Step 3 — Wormhole message reports the full amount**

Immediately after, the Wormhole message is posted with the original `payload.amount`:

```rust
self.common.post_message(payload.serialize_for_near((
    self.common.sequence.sequence,
    self.user.key(),
    self.mint.key(),
))?)?;
``` [3](#0-2) 

NEAR therefore credits `N` tokens to the recipient while the vault holds only `N × (1 − fee_rate/10000)` spendable tokens.

**Step 4 — `finalize_transfer` attempts to release the full amount**

When bridging back, `FinalizeTransfer::process` calls `transfer_checked` with `data.amount` (the full NEAR-credited amount):

```rust
transfer_checked(
    CpiContext::new_with_signer(..., TransferChecked {
        from: vault.to_account_info(),
        to: self.token_account.to_account_info(),
        ...
    }, ...),
    data.amount.try_into()...?,
    self.mint.decimals,
)?;
``` [4](#0-3) 

The vault's spendable balance is `N × (1 − r)` but the instruction demands `N`, so the CPI reverts. The tokens credited on NEAR cannot be redeemed on Solana.

---

### Impact Explanation

Each `init_transfer` call with a fee-bearing Token-2022 mint creates a permanent shortfall of `N × fee_rate / 10000` spendable tokens in the vault. Repeated calls accumulate the deficit. Users who bridged legitimate tokens into the same vault (or who hold NEAR-side credits for this token) will find their redemptions reverting on Solana — a permanent, irrecoverable lock of their funds. The attacker also effectively obtains `N` NEAR-side tokens for only `N × (1 − r)` Solana-side tokens locked, extracting `N × r` tokens of value from the collective vault.

**Scoped impact:** High — balance/accounting corruption that breaks bridge collateralization; also Critical — permanent freezing of redeemable funds for other users.

---

### Likelihood Explanation

- Any unprivileged user can call `log_metadata` with a Token-2022 mint they control (or any existing Token-2022 mint with a fee).
- No privileged role is required at any step.
- The Token-2022 `transfer_fee` extension is a standard, widely-used feature.
- The exploit is deterministic and locally reproducible.

---

### Recommendation

In `log_metadata`, reject mints that carry a non-zero `transfer_fee` extension:

```rust
// After unpacking mint_with_extension:
if let Ok(fee_config) = mint_with_extension.get_extension::<TransferFeeConfig>() {
    let epoch = Clock::get()?.epoch;
    let fee = fee_config.get_epoch_fee(epoch);
    require!(
        u16::from(fee.transfer_fee_basis_points) == 0,
        ErrorCode::UnsupportedTransferFeeExtension
    );
}
```

Alternatively, in `init_transfer`, read the vault's actual balance delta after `transfer_checked` and use that value — not `payload.amount` — in the Wormhole message.

---

### Proof of Concept

1. Create a Token-2022 mint with `transfer_fee_basis_points = 1000` (10%).
2. Call `log_metadata(mint)` → vault PDA is created; no rejection.
3. Call `init_transfer(amount = 1_000_000, fee = 0)`:
   - `transfer_checked(1_000_000)` → vault receives `900_000`; `100_000` withheld.
   - Wormhole VAA carries `amount = 1_000_000`.
4. NEAR processes the VAA and credits `1_000_000` tokens to the recipient.
5. Recipient calls `init_transfer` on NEAR to bridge back `1_000_000`.
6. NEAR signs and relayer calls `finalize_transfer(amount = 1_000_000)` on Solana.
7. `transfer_checked(1_000_000)` from vault (balance `900_000`) → **reverts with insufficient funds**.
8. Invariant broken: vault holds `900_000` spendable tokens; NEAR credited `1_000_000`.

### Citations

**File:** solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs (L41-45)
```rust
    #[account(
        constraint = !mint.mint_authority.contains(authority.key),
        mint::token_program = token_program,
    )]
    pub mint: Box<InterfaceAccount<'info, Mint>>,
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
