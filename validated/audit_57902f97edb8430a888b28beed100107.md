The grep confirms `permanent_delegate`/`PermanentDelegate` appear **nowhere** in production source files — only in test helpers. The only `get_extension` call in the entire bridge source is for `MetadataPointer` in `log_metadata.rs`.

---

### Title
Token-2022 `permanent_delegate` Allows Vault Drain After `log_metadata` Registration — (`solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs`)

### Summary
`LogMetadata::process` creates a vault PDA for any Token-2022 mint that lacks the bridge authority as `mint_authority`, but never inspects the mint's `PermanentDelegate` extension. An unprivileged attacker can register a Token-2022 mint whose `permanent_delegate` is attacker-controlled, lure victims into locking tokens via `init_transfer`, then drain the vault using the SPL Token-2022 `permanent_delegate` authority — a transfer that requires no vault-authority signature. All pending `finalize_transfer` operations then fail with insufficient balance, permanently freezing victims' cross-chain settlements.

### Finding Description

`LogMetadata` account validation enforces only one mint constraint:

```rust
constraint = !mint.mint_authority.contains(authority.key),
``` [1](#0-0) 

Inside `process()`, the only Token-2022 extension ever read is `MetadataPointer` (for name/symbol extraction). No extension enumeration or blocklist is applied:

```rust
if let Ok(metadata_pointer) = mint_with_extension.get_extension::<MetadataPointer>() {
``` [2](#0-1) 

The vault is unconditionally created via `init_if_needed` with `token::authority = authority` (the bridge PDA):

```rust
#[account(
    init_if_needed,
    ...
    token::authority = authority,
    seeds = [VAULT_SEED, mint.key().as_ref()],
    ...
)]
pub vault: Box<InterfaceAccount<'info, TokenAccount>>,
``` [3](#0-2) 

`init_transfer` then accepts tokens into this vault with no extension checks on the mint:

```rust
transfer_checked(
    CpiContext::new(..., TransferChecked {
        from: self.from.to_account_info(),
        to: vault.to_account_info(),
        authority: self.user.to_account_info(),
        mint: self.mint.to_account_info(),
    }),
    ...
)?;
``` [4](#0-3) 

`finalize_transfer` then attempts `transfer_checked` from the vault, which will fail if the vault has been drained:

```rust
transfer_checked(
    CpiContext::new_with_signer(..., TransferChecked {
        from: vault.to_account_info(),
        to: self.token_account.to_account_info(),
        authority: self.authority.to_account_info(),
        ...
    }, &[&[AUTHORITY_SEED, &[self.config.bumps.authority]]]),
    ...
)?;
``` [5](#0-4) 

The SPL Token-2022 `PermanentDelegate` extension grants the designated address the ability to call `transfer` or `burn` on **any** token account of that mint without the account owner's or authority's signature. Setting `token::authority = authority` on the vault PDA does not block a `permanent_delegate` transfer — the delegate bypasses the authority check entirely at the SPL Token-2022 program level.

### Impact Explanation

- All tokens deposited into the vault via `init_transfer` can be drained by the attacker at any time after vault creation.
- Every pending `finalize_transfer` for that mint will revert with insufficient balance.
- The NEAR-side wrapped tokens minted against those `init_transfer` messages are permanently unbacked.
- Victims cannot reclaim their Solana-side tokens (vault is empty) and cannot receive their NEAR-side tokens (finalize fails). The loss is irrecoverable without an emergency upgrade.

**Impact: Critical — permanent, irrecoverable lock of user funds across all pending finalize_transfer operations for the affected mint.**

### Likelihood Explanation

- Requires no privileged access. Any account can create a Token-2022 mint with `permanent_delegate` set and call `log_metadata`.
- The attack is fully permissionless and can be executed on mainnet today.
- Victims need only use the bridge normally with the registered token; the attacker can wait until vault balance is maximized before draining.

### Recommendation

In `LogMetadata::process`, after unpacking the mint with `StateWithExtensions`, enumerate dangerous extensions and reject the mint if any are present. At minimum, block `PermanentDelegate` (and also `TransferHook`, `ConfidentialTransferMint`, and `CloseAuthority` for defense-in-depth):

```rust
use spl_token_2022::extension::permanent_delegate::PermanentDelegate;

if let Ok(pd) = mint_with_extension.get_extension::<PermanentDelegate>() {
    if Option::<Pubkey>::from(pd.delegate).is_some() {
        return err!(ErrorCode::UnsupportedMintExtension);
    }
}
```

Apply the same check in `init_transfer` as a defense-in-depth guard before accepting tokens into the vault.

### Proof of Concept

```
1. attacker: spl-token-2022 create-token --enable-permanent-delegate
   → set permanent_delegate = attacker_pubkey

2. attacker: calls log_metadata(mint=attacker_mint)
   → vault PDA created at [VAULT_SEED, attacker_mint]
   → no permanent_delegate check, succeeds

3. victim: calls init_transfer(mint=attacker_mint, amount=1_000_000)
   → 1_000_000 tokens transferred to vault
   → Wormhole message emitted; NEAR side mints wrapped tokens to victim

4. attacker: calls spl_token_2022::transfer(
       source=vault, dest=attacker_ata,
       authority=attacker_pubkey,   // permanent_delegate
       amount=1_000_000
   )
   → succeeds without vault PDA signature
   → vault.amount = 0

5. relayer: calls finalize_transfer(mint=attacker_mint, amount=1_000_000)
   → transfer_checked from vault fails: insufficient funds
   → recipient never receives tokens

Invariant broken: vault.amount (0) < sum of pending finalize_transfer amounts (1_000_000)
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

**File:** solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs (L98-114)
```rust
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
