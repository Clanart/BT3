### Title
Vault Token Account Permanently Freezable via Unguarded `freeze_authority` on Registered Mint — (`solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs`)

---

### Summary

`log_metadata` creates a vault PDA for any mint whose `mint_authority` is not the bridge authority, but it never checks whether the mint has an active `freeze_authority`. An attacker who controls a mint's `freeze_authority` can freeze the vault token account after victims have deposited into it, permanently blocking all `finalize_transfer` withdrawals with no recovery path.

---

### Finding Description

The `LogMetadata` account struct enforces exactly one constraint on the mint: [1](#0-0) 

```rust
constraint = !mint.mint_authority.contains(authority.key),
```

This only prevents bridged tokens (where the bridge is the mint authority) from being registered as native tokens. There is **no constraint on `mint.freeze_authority`**. The vault is then unconditionally created: [2](#0-1) 

Once the vault exists, `init_transfer` treats its existence as proof of native token registration and transfers user tokens into it: [3](#0-2) 

`finalize_transfer` then attempts `transfer_checked` **from** the vault to release funds: [4](#0-3) 

If the vault token account is frozen, this CPI fails with `TokenAccountFrozen`. There is no thaw path, no admin escape hatch, and no alternative withdrawal route in the program.

A grep across all production Solana source confirms `freeze_authority` is never read or constrained anywhere in the bridge program: [5](#0-4) 

(No `freeze_authority`-related error code exists.)

---

### Impact Explanation

**Critical — Permanent irrecoverable lock of all user tokens in the vault.**

Any token deposited via `init_transfer` into a vault whose mint has an active `freeze_authority` can be permanently frozen by the freeze authority holder. The `finalize_transfer` CPI will revert on every attempt, and there is no admin instruction to thaw the vault or drain it by another path. The locked tokens are unrecoverable.

---

### Likelihood Explanation

The attacker needs only to:
1. Create a Token-2022 or SPL mint with `freeze_authority = attacker` (permissionless, costs ~0.01 SOL).
2. Call `log_metadata` — passes all existing constraints.
3. Wait for any user to call `init_transfer` for that mint (or socially engineer deposits).
4. Call the standard SPL `freeze_account` instruction on the vault PDA using their `freeze_authority`.

All four steps are unprivileged, require no special access, and are executable on-chain by anyone. The attack is fully local-testable.

---

### Recommendation

Add a constraint in `LogMetadata` that rejects mints with any active `freeze_authority`:

```rust
#[account(
    constraint = !mint.mint_authority.contains(authority.key),
    constraint = mint.freeze_authority.is_none() @ ErrorCode::MintHasFreezeAuthority,
    mint::token_program = token_program,
)]
pub mint: Box<InterfaceAccount<'info, Mint>>,
```

Add the corresponding error variant to `error.rs`. The same guard should be applied in `init_transfer` as a defense-in-depth check before transferring into the vault.

---

### Proof of Concept

```
1. attacker: create_mint(freeze_authority=attacker, mint_authority=attacker)
   → mint M created, freeze_authority=attacker

2. attacker: log_metadata(mint=M)
   → constraint !mint.mint_authority.contains(authority.key) passes (attacker ≠ bridge)
   → vault PDA [VAULT_SEED, M] created via init_if_needed

3. victim: init_transfer(mint=M, vault=vault_PDA, amount=1000)
   → vault.amount = 1000 tokens locked

4. attacker: spl_token::freeze_account(account=vault_PDA, mint=M, authority=attacker)
   → vault_PDA.state = Frozen

5. relayer: finalize_transfer(mint=M, vault=vault_PDA, amount=1000)
   → transfer_checked(from=vault_PDA, ...) → Error: TokenAccountFrozen
   → transaction reverts; tokens permanently locked; no recovery path exists
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

**File:** solana/programs/bridge_token_factory/src/error.rs (L1-27)
```rust
use anchor_lang::prelude::*;

#[error_code(offset = 6000)]
pub enum ErrorCode {
    #[msg("Invalid arguments")]
    InvalidArgs,
    #[msg("Signature verification failed")]
    SignatureVerificationFailed,
    #[msg("Malleable signature")]
    MalleableSignature,
    #[msg("Nonce already used")]
    NonceAlreadyUsed,
    #[msg("Token metadata not provided")]
    TokenMetadataNotProvided,
    #[msg("Invalid token metadata address")]
    InvalidTokenMetadataAddress,
    #[msg("Invalid bridged token")]
    InvalidBridgedToken,
    #[msg("Invalid fee")]
    InvalidFee,
    #[msg("Paused")]
    Paused,
    #[msg("Unauthorized")]
    Unauthorized,
    #[msg("Amount overflow (unexpected state)")]
    AmountOverflow,
}
```
