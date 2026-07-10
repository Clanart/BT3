### Title
Token-2022 Transfer Fee Extension Causes Vault Undercollateralization and Permanently Locked Fee Residuals — (`solana/programs/bridge_token_factory/src/instructions/user/init_transfer.rs`)

---

### Summary

An unprivileged attacker can register a Token-2022 mint that carries a transfer fee extension via `log_metadata`. Once the vault is created, every subsequent `init_transfer` call will have the Token-2022 runtime silently withhold the fee from the vault, while the Wormhole message records the full pre-fee amount. NEAR mints wrapped tokens equal to the full amount, permanently undercollateralizing the bridge by the cumulative fee residual.

---

### Finding Description

**`log_metadata` — no transfer-fee extension guard**

`LogMetadata::process` reads only the metadata pointer and name/symbol extensions from the Token-2022 mint state. [1](#0-0) 

The only constraint on the mint is that the bridge authority is not the mint authority: [2](#0-1) 

There is no check for a `TransferFeeConfig` extension. A mint with a 1 % transfer fee passes all validation, the vault is created via `init_if_needed`, and a Wormhole registration message is posted.

**`init_transfer` — amount in Wormhole message is the pre-fee nominal**

When the vault exists (native-token path), `transfer_checked` is called with the raw `payload.amount`: [3](#0-2) 

For a Token-2022 mint with a transfer fee extension, the SPL Token-2022 runtime withholds the fee from the **destination** (the vault). With a 1 % fee and `amount = 1000`, the vault receives 990; 10 tokens are withheld in the vault's `withheld_amount` field.

Immediately after, the Wormhole message is posted using the same unmodified `payload.amount`: [4](#0-3) 

The serialized payload writes `self.amount` verbatim: [5](#0-4) 

NEAR therefore receives a message claiming 1000 tokens were deposited and mints 1000 wrapped tokens, while the vault only holds 990.

**No production-code mitigation exists**

A codebase-wide search for `transfer_fee`, `TransferFee`, `transfer_fee_config`, and `withheld` in production Solana source returns zero matches — only a test file. No instruction rejects or adjusts for transfer-fee mints.

---

### Impact Explanation

- **Accounting drift per transfer:** every `init_transfer` on a fee-bearing mint undercollateralizes the bridge by `fee_bps / 10000 * amount`.
- **Cumulative undercollateralization:** after N transfers the outstanding wrapped supply on NEAR exceeds vault holdings by the sum of all withheld fees; the bridge can never fully redeem all wrapped tokens.
- **Permanently locked residual:** withheld tokens sit in the vault's `withheld_amount` field; the bridge has no instruction to harvest or return them, so they are irrecoverably locked.

---

### Likelihood Explanation

The attacker needs only a funded Solana wallet and the ability to call two public instructions (`log_metadata`, then any user's `init_transfer`). Token-2022 mints with transfer fees are a standard, well-documented feature. No privileged role, leaked key, or oracle manipulation is required.

---

### Recommendation

In `log_metadata::process`, after unpacking the mint extensions, reject any mint that carries a `TransferFeeConfig` extension:

```rust
use spl_token_2022::extension::transfer_fee::TransferFeeConfig;

if mint_with_extension.get_extension::<TransferFeeConfig>().is_ok() {
    return err!(ErrorCode::UnsupportedMintExtension);
}
```

Apply the same guard in `init_transfer` as a defense-in-depth check before calling `transfer_checked`, so that even if a fee-bearing vault was registered before the fix, new transfers are blocked.

---

### Proof of Concept

```
1. attacker: spl-token-2022 create-token --transfer-fee 100 1000  // 1% fee
2. attacker: bridge log_metadata(mint=fee_mint)
   → vault PDA created; Wormhole message posted (registration)
3. victim:   bridge init_transfer(mint=fee_mint, amount=1000, vault=vault_pda, ...)
   → transfer_checked(1000) executes:
       vault.amount   += 990   (Token-2022 withholds 10)
       vault.withheld += 10
   → Wormhole message: amount=1000
4. NEAR:     mints 1000 wrapped tokens
5. invariant broken: vault.amount(990) < wrapped_supply(1000)
6. repeat N times → bridge undercollateralized by N*10 tokens
7. withheld tokens have no harvest/recovery path in the bridge program
```

### Citations

**File:** solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs (L41-45)
```rust
    #[account(
        constraint = !mint.mint_authority.contains(authority.key),
        mint::token_program = token_program,
    )]
    pub mint: Box<InterfaceAccount<'info, Mint>>,
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

**File:** solana/programs/bridge_token_factory/src/instructions/user/init_transfer.rs (L88-102)
```rust
        if let Some(vault) = &self.vault {
            // Native version. We have a proof of token registration by vault existence
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

**File:** solana/programs/bridge_token_factory/src/state/message/init_transfer.rs (L31-33)
```rust
        // 4. amount
        self.amount.serialize(&mut writer)?;
        // 5. fee
```
