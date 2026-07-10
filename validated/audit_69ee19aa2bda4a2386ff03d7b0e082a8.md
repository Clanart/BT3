### Title
Token-2022 Transfer Hook Extension Not Supported in `transfer_checked` CPI Calls — Permanent Vault Fund Lock - (File: `solana/programs/bridge_token_factory/src/instructions/user/init_transfer.rs`, `finalize_transfer.rs`)

---

### Summary

The Solana bridge program calls `transfer_checked` for native Token-2022 token deposits and withdrawals without providing the extra accounts required by the Token-2022 Transfer Hook extension. If a registered native token's mint has a Transfer Hook enabled, every `finalize_transfer` attempt will revert, making vault funds permanently irrecoverable through any bridge instruction.

---

### Finding Description

The bridge explicitly supports Token-2022 tokens via `TokenInterface` and creates vaults for them through `log_metadata`. However, both token-movement instructions use bare `CpiContext::new` / `CpiContext::new_with_signer` with no remaining accounts:

**`init_transfer.rs` — deposit path:** [1](#0-0) 

**`finalize_transfer.rs` — withdrawal path:** [2](#0-1) 

When the Token-2022 program executes `transfer_checked` and the mint has a `TransferHook` extension with a non-null program ID, it invokes the hook program and requires the extra account metas to be present as remaining accounts in the CPI call. Because neither instruction passes any remaining accounts, the Token-2022 program reverts with a missing-accounts error.

A grep across the entire Solana program confirms there is zero handling of `transfer_hook`, `TransferHook`, or `extra_account_metas` anywhere in the codebase. 

**`log_metadata` creates the vault without any Transfer Hook check:** [3](#0-2) 

The vault PDA is deterministic (`[VAULT_SEED, mint.key()]`), so tokens can enter it via:
1. A direct SPL transfer to the vault address (bypassing `init_transfer`).
2. `init_transfer` while the Transfer Hook's `program_id` is `None` (disabled), followed by the transfer hook authority updating it to a live hook program — a permitted Token-2022 operation.

Once tokens are in the vault and the hook is active, every `finalize_transfer` call reverts. The nonce is not permanently consumed (Solana atomicity rolls back `use_nonce`), but the transfer will **always** fail because the bridge never supplies the hook's extra accounts. No admin rescue instruction exists in the program. [4](#0-3) 

---

### Impact Explanation

Vault funds for any native Token-2022 token whose mint has an active Transfer Hook become permanently irrecoverable. `finalize_transfer` is the only exit path for native tokens; there is no admin sweep or emergency withdrawal instruction. This matches the **Critical — Permanent freezing / irrecoverable lock of user or protocol funds in bridge vault flows** impact category.

---

### Likelihood Explanation

**Medium.** Token-2022 Transfer Hook tokens exist in production. The bridge explicitly accepts Token-2022 tokens with no extension filtering in `log_metadata`. The two realistic fund-lock paths (direct vault transfer; hook enabled after deposit) are both permissionless or require only the token's own transfer hook authority — not a bridge operator. No on-chain guard prevents registration or deposit of Transfer Hook tokens.

---

### Recommendation

1. **Detect Transfer Hook at registration time.** In `log_metadata`, unpack the mint's extensions and reject (or flag) mints that carry a `TransferHook` extension with a non-null program ID, mirroring the approach used for `MetadataPointer`: [5](#0-4) 

2. **If Transfer Hook tokens must be supported**, replace the bare `transfer_checked` CPI with `spl_transfer_hook_interface::onchain::add_extra_accounts_for_execute_cpi` (or the equivalent anchor-spl helper) to resolve and pass the required extra account metas as remaining accounts in both `init_transfer` and `finalize_transfer`.

---

### Proof of Concept

1. Create a Token-2022 mint with `TransferHook` extension (program ID initially `None`).
2. Call `log_metadata` — vault PDA is created with no error.
3. Directly transfer tokens to the vault PDA address.
4. Update the mint's transfer hook program ID to a live hook program (permitted by Token-2022 for the hook authority).
5. Call `finalize_transfer` with a valid signed NEAR payload targeting the vault.
6. Observe revert: Token-2022 program fails because the hook's extra account metas are absent from the CPI remaining accounts.
7. Repeat indefinitely — always reverts. Vault tokens are permanently locked.

### Citations

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

**File:** solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs (L50-63)
```rust
    #[account(
        init_if_needed,
        payer = common.payer,
        token::mint = mint,
        token::authority = authority,
        seeds = [
            VAULT_SEED,
            mint.key().as_ref(),
        ],
        bump,
        token::token_program = token_program,
    )]
    pub vault: Box<InterfaceAccount<'info, TokenAccount>>,

```

**File:** solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs (L92-114)
```rust
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
```

**File:** solana/programs/bridge_token_factory/src/instructions/admin/mod.rs (L1-10)
```rust
pub mod change_config;
pub mod initialize;
pub mod pause;
pub mod update_metadata;

pub use change_config::*;
pub use initialize::*;
pub use pause::*;
pub use update_metadata::*;

```
