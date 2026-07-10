The grep confirms there is **zero** production code checking for `permanent_delegate` anywhere in the Solana program. The SECURITY.md mentions transfer hooks as a known denial-only issue but is silent on `permanent_delegate`. The attack path is concrete and complete.

---

### Title
Unguarded `permanent_delegate` Extension on Token-2022 Mints Allows Vault Drain After `log_metadata` Registration — (`solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs`)

### Summary
`log_metadata` creates a vault for any Token-2022 mint whose `mint_authority` is not the bridge authority. It never inspects the `PermanentDelegate` extension. An attacker who creates a Token-2022 mint with `permanent_delegate = attacker` can register it, wait for victims to lock tokens via `init_transfer`, then drain the vault using the permanent-delegate authority — bypassing the vault's PDA ownership entirely. All pending `finalize_transfer` operations for that mint then fail permanently.

### Finding Description

`LogMetadata::process` unpacks the mint's extension data only to read `MetadataPointer` for name/symbol: [1](#0-0) 

The only guard on the mint is: [2](#0-1) 

This rejects mints where the bridge PDA is the `mint_authority`, but says nothing about `permanent_delegate`. No other production file checks for this extension: [3](#0-2) 

The vault is created with `token::authority = authority` (the bridge PDA). Under Token-2022, the `PermanentDelegate` extension grants its holder the ability to call `transfer` or `burn` on **any** token account of that mint, regardless of the token account's `authority` field. The vault's PDA ownership provides no protection.

Once the vault exists, `init_transfer` treats the mint as "native" and locks user tokens into it: [4](#0-3) 

`finalize_transfer` then attempts to release from the vault: [5](#0-4) 

If the vault has been drained by the permanent delegate, this `transfer_checked` fails with an insufficient-funds error, and the nonce is already consumed: [6](#0-5) 

The nonce is marked used before the transfer, so the operation cannot be retried. Recipients are permanently unable to claim their tokens.

The SECURITY.md acknowledges transfer hooks as a known denial-only issue but does not mention `permanent_delegate`: [7](#0-6) 

### Impact Explanation
Every `finalize_transfer` for the drained vault fails after the nonce is consumed. Victims who locked tokens via `init_transfer` lose them permanently with no recovery path. The wrapped supply on NEAR becomes unbacked. This matches the critical impact category: **permanent freezing / unclaimable settlement of user funds in vault flows**.

### Likelihood Explanation
The attacker needs no privilege — `log_metadata` is a fully public, permissionless instruction. The attacker only needs to create a Token-2022 mint (trivial on-chain operation), distribute tokens to victims (e.g., airdrop, DEX listing, impersonation of a legitimate token), and wait for victims to bridge. The `permanent_delegate` extension is immutable once set, so there is no window for remediation after vault creation. Likelihood is **medium** (requires victim participation) but the technical path is fully permissionless.

### Recommendation
In `LogMetadata::process`, after unpacking `mint_with_extension`, reject any mint that carries a `PermanentDelegate` extension with a non-default address:

```rust
use spl_token_2022::extension::permanent_delegate::PermanentDelegate;

if let Ok(pd) = mint_with_extension.get_extension::<PermanentDelegate>() {
    require!(
        pd.delegate.0 == Pubkey::default(),
        ErrorCode::UnsupportedMintExtension
    );
}
```

Similarly, consider rejecting mints with `TransferFeeConfig`, `TransferHook`, or `ConfidentialTransferMint` extensions that could interfere with vault accounting or CPI execution.

### Proof of Concept
1. Attacker calls `spl_token_2022::instruction::initialize_permanent_delegate` on a fresh mint, setting `delegate = attacker_keypair.pubkey()`.
2. Attacker calls `log_metadata` with this mint → vault PDA is created, no error.
3. Victim acquires attacker's tokens and calls `init_transfer(amount=1_000_000)` → vault balance = 1,000,000.
4. NEAR side emits a `finalize_transfer` VAA for the victim.
5. Attacker calls `spl_token_2022::instruction::transfer` with `authority = attacker_keypair` (permanent delegate) → vault balance = 0.
6. Relayer calls `finalize_transfer` → `transfer_checked` from vault fails (`InsufficientFunds`), nonce already consumed → victim's tokens are permanently lost.

### Citations

**File:** solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs (L41-45)
```rust
    #[account(
        constraint = !mint.mint_authority.contains(authority.key),
        mint::token_program = token_program,
    )]
    pub mint: Box<InterfaceAccount<'info, Mint>>,
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs (L50-62)
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

**File:** solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs (L95-114)
```rust
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

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs (L91-99)
```rust
        UsedNonces::use_nonce(
            data.destination_nonce,
            &self.used_nonces,
            &mut self.config,
            self.authority.to_account_info(),
            self.common.payer.to_account_info(),
            &Rent::get()?,
            self.system_program.to_account_info(),
        )?;
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs (L101-116)
```rust
        if let Some(vault) = &self.vault {
            // Native version. We have a proof of token registration by vault existence
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

**File:** solana/SECURITY.md (L19-19)
```markdown
- **Token-2022 tokens with transfer hooks are not supported** — Transfer hook extra account metas are not included in instruction account sets. Affected tokens will fail at runtime (denial, not fund loss).
```
