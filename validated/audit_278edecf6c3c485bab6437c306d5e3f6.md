### Title
Token-2022 Transfer Fee Extension Causes Vault Undercollateralization and Withheld-Fee Theft — (`solana/programs/bridge_token_factory/src/instructions/user/init_transfer.rs`, `log_metadata.rs`)

---

### Summary

An unprivileged attacker can register a Token-2022 mint that carries a transfer fee extension via `log_metadata`, then induce victims to call `init_transfer`. Because `transfer_checked` silently withholds the fee from the vault's spendable balance while the Wormhole message records the full nominal amount, NEAR mints more wrapped tokens than are backed by vault collateral. The withheld tokens are permanently unspendable through normal bridge operations and can be harvested by the attacker (as fee authority), draining the vault.

---

### Finding Description

**Step 1 — No guard in `log_metadata`**

`LogMetadata::process` creates a vault PDA for any Token-2022 mint. The only constraint on the mint is:

```rust
constraint = !mint.mint_authority.contains(authority.key),
``` [1](#0-0) 

There is no inspection of the mint's extensions. A mint carrying a `TransferFeeConfig` extension passes this check freely. The grep across all production Solana source confirms zero references to `transfer_fee` or `TransferFee` anywhere in `src/`: [2](#0-1) 

**Step 2 — `init_transfer` records the pre-fee amount in the Wormhole message**

`InitTransfer::process` calls `transfer_checked` with `payload.amount` (the full nominal amount the user specified):

```rust
transfer_checked(
    CpiContext::new(..., TransferChecked { from, to: vault, authority, mint }),
    payload.amount.try_into()...,
    self.mint.decimals,
)?;
``` [3](#0-2) 

Immediately after, the same `payload.amount` is serialized into the Wormhole message:

```rust
self.common.post_message(payload.serialize_for_near((
    self.common.sequence.sequence,
    self.user.key(),
    self.mint.key(),
))?)?;
``` [4](#0-3) 

The serialized payload writes `self.amount` directly: [5](#0-4) 

**Step 3 — SPL Token-2022 transfer fee mechanics create the gap**

Under the Token-2022 transfer fee extension, `transfer_checked(amount=1000)` with a 1% fee:
- Debits the sender by 1000 tokens.
- Credits the vault with **990 spendable tokens** and **10 withheld tokens** (stored in the vault's `withheld_amount` field).
- The vault's total `amount` field = 990; `withheld_amount` = 10.

The Wormhole message records 1000. NEAR mints 1000 wrapped tokens. The vault is immediately undercollateralized by 10 tokens.

**Step 4 — `finalize_transfer` cannot redeem the full amount**

When a user bridges 1000 wrapped tokens back, `FinalizeTransfer::process` calls:

```rust
transfer_checked(
    CpiContext::new_with_signer(..., TransferChecked { from: vault, to: token_account, ... }, ...),
    data.amount.try_into()...,   // 1000
    self.mint.decimals,
)?;
``` [6](#0-5) 

The vault only has 990 spendable tokens. This call fails, making the last ~1% of wrapped supply permanently unclaimable.

**Step 5 — Attacker harvests withheld tokens from the vault**

SPL Token-2022 provides two permissionless/fee-authority-gated instructions:
- `harvest_withheld_tokens_to_mint` — permissionless; moves withheld tokens from any token account (including the vault PDA) to the mint's withheld pool.
- `withdraw_withheld_tokens_from_mint` — requires the fee authority signature (the attacker).

The attacker, as fee authority, can call both instructions to extract the withheld tokens from the vault, directly stealing from bridge collateral.

---

### Impact Explanation

- **Permanent accounting drift**: Every `init_transfer` on a fee-bearing mint creates a deficit equal to the fee percentage. After N transfers the vault is undercollateralized by `N × fee × amount`.
- **Irrecoverable lock / unclaimable settlement**: The last tranche of wrapped-token holders cannot redeem because `finalize_transfer` will revert when the vault's spendable balance is exhausted.
- **Direct theft**: The attacker (fee authority) can harvest withheld tokens from the vault PDA via standard Token-2022 instructions, draining bridge collateral without any bridge-program interaction.

---

### Likelihood Explanation

The attack requires no privileges. Any user can create a Token-2022 mint with a transfer fee extension and call `log_metadata`. The vault is created immediately. Subsequent victim transfers are sufficient to trigger the accounting drift. The fee-harvesting step is executable by the attacker at any time using standard SPL Token-2022 tooling.

---

### Recommendation

In `LogMetadata::process`, after unpacking the mint with `StateWithExtensions`, reject any mint that carries a `TransferFeeConfig` extension:

```rust
if mint_with_extension.get_extension::<TransferFeeConfig>().is_ok() {
    return err!(ErrorCode::UnsupportedMintExtension);
}
```

Apply the same guard in `InitTransfer::process` as a defense-in-depth check before calling `transfer_checked`. Alternatively, use `transfer_checked_with_fee` and verify that the withheld fee is zero, or compute the post-fee amount and use that in the Wormhole message instead of the nominal amount.

---

### Proof of Concept

1. Attacker creates a Token-2022 mint with `TransferFeeConfig { transfer_fee_basis_points: 100, ... }` (1% fee); attacker retains fee authority.
2. Attacker calls `log_metadata` → vault PDA is created; no error.
3. Victim calls `init_transfer(amount=1000)`:
   - `transfer_checked(1000)` → vault receives 990 spendable, 10 withheld.
   - Wormhole message records `amount=1000`.
4. NEAR mints 1000 wrapped tokens.
5. Attacker calls `harvest_withheld_tokens_to_mint([vault])` → 10 tokens move to mint's withheld pool.
6. Attacker calls `withdraw_withheld_tokens_from_mint` → attacker receives 10 tokens.
7. Vault now has 990 tokens; 1000 wrapped tokens are outstanding.
8. Invariant `vault.spendable_amount == sum(init_transfer.amount)` is broken after step 3; attacker profit is realized at step 6.
9. Repeat N times: bridge is undercollateralized by `N × 10` tokens; the last holders of wrapped tokens cannot redeem.

### Citations

**File:** solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs (L41-44)
```rust
    #[account(
        constraint = !mint.mint_authority.contains(authority.key),
        mint::token_program = token_program,
    )]
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs (L91-141)
```rust
    pub fn process(&mut self) -> Result<()> {
        let (name, symbol) = if self.token_program.key() == token_2022::ID {
            let mint_account_info = self.mint.to_account_info();
            let mint_data = mint_account_info.try_borrow_data()?;
            let mint_with_extension =
                StateWithExtensions::<spl_token_2022::state::Mint>::unpack(&mint_data)?;

            if let Ok(metadata_pointer) = mint_with_extension.get_extension::<MetadataPointer>() {
                if metadata_pointer.metadata_address.0 == self.mint.key() {
                    // Embedded metadata
                    let metadata =
                        mint_with_extension.get_variable_len_extension::<TokenMetadata>()?;
                    (metadata.name, metadata.symbol)
                } else if metadata_pointer.metadata_address.0 != Pubkey::default() {
                    // Third-party metadata
                    self.parse_metadata_account(metadata_pointer.metadata_address.0)?
                } else {
                    // No metadata
                    (String::default(), String::default())
                }
            } else {
                // No metadata pointer extension found
                (String::default(), String::default())
            }
        } else {
            // Only metaplex is supported for the classic SPL tokens
            self.parse_metadata_account(
                Pubkey::find_program_address(
                    &[
                        METADATA_SEED,
                        MetaplexID.as_ref(),
                        &self.mint.key().to_bytes(),
                    ],
                    &MetaplexID,
                )
                .0,
            )?
        };

        let payload = LogMetadataPayload {
            token: self.mint.key(),
            name: name.trim_end_matches('\0').to_string(),
            symbol: symbol.trim_end_matches('\0').to_string(),
            decimals: self.mint.decimals,
        }
        .serialize_for_near(())?;

        self.common.post_message(payload)?;

        Ok(())
    }
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
