### Title
Native Token Vault Permanently Frozen via Mint Freeze Authority — (`solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs`)

### Summary

The Solana bridge registers native tokens and creates a per-token vault PDA via `log_metadata`. Neither `log_metadata` nor `init_transfer` validates that the token mint lacks a freeze authority. An external freeze authority holder can freeze the bridge's vault PDA after users have locked tokens into it, permanently blocking both deposits and withdrawals and irrecoverably locking user funds.

### Finding Description

`log_metadata` is the entry point for registering a native Solana token with the bridge. It creates the vault PDA that will hold all locked tokens for that mint. The only constraint on the `mint` account is:

```rust
#[account(
    constraint = !mint.mint_authority.contains(authority.key),
    mint::token_program = token_program,
)]
pub mint: Box<InterfaceAccount<'info, Mint>>,
``` [1](#0-0) 

There is no constraint of the form `mint::freeze_authority = COption::None`. The vault is then created as:

```rust
#[account(
    init_if_needed,
    payer = common.payer,
    token::mint = mint,
    token::authority = authority,
    seeds = [VAULT_SEED, mint.key().as_ref()],
    bump,
    token::token_program = token_program,
)]
pub vault: Box<InterfaceAccount<'info, TokenAccount>>,
``` [2](#0-1) 

Once the vault exists, users call `init_transfer` to lock tokens into it. The `mint` constraint there is equally unchecked for freeze authority:

```rust
#[account(
    mut,
    mint::token_program = token_program,
)]
pub mint: Box<InterfaceAccount<'info, Mint>>,
``` [3](#0-2) 

The actual lock is a `transfer_checked` CPI into the vault: [4](#0-3) 

On the return path, `finalize_transfer` unlocks tokens from the vault via another `transfer_checked`. Its `mint` constraint is also unchecked: [5](#0-4) [6](#0-5) 

SPL Token's `transfer_checked` (and Token-2022's equivalent) unconditionally rejects transfers involving a frozen token account. Once the vault is frozen, every `init_transfer` and `finalize_transfer` call for that mint reverts, and there is no admin escape hatch in the program to unfreeze or rescue the vault.

### Impact Explanation

**Critical — Permanent irrecoverable lock of user funds.**

All tokens deposited into the vault for a freeze-authority-bearing mint become permanently inaccessible. The bridge has no mechanism to unfreeze a vault or migrate funds to a new account. This matches the allowed impact: *"Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows."*

### Likelihood Explanation

**Medium-High.** The attack requires only that:
1. A token with a live freeze authority is registered via `log_metadata` (permissionless — any caller can do this for any mint).
2. Users bridge that token, locking value in the vault.
3. The freeze authority holder (the token issuer, or anyone they delegate to) calls `freeze_account` on the vault PDA.

Step 1 is fully permissionless. Step 3 requires the freeze authority to act, but many real-world SPL tokens retain a freeze authority (e.g., stablecoins, regulated tokens). The freeze authority holder may be a malicious token deployer who specifically registers their token to lure bridge users, or a legitimate issuer who later exercises the authority for compliance reasons.

### Recommendation

Add a `freeze_authority` constraint to the `mint` account in both `log_metadata` and `init_transfer`:

```rust
// In LogMetadata and InitTransfer account structs:
#[account(
    constraint = !mint.mint_authority.contains(authority.key),
    constraint = mint.freeze_authority == COption::None
        @ ErrorCode::FreezeAuthorityNotAllowed,
    mint::token_program = token_program,
)]
pub mint: Box<InterfaceAccount<'info, Mint>>,
```

This ensures that only mints without a freeze authority can be registered as native bridge tokens, eliminating the ability for any external party to freeze the vault.

### Proof of Concept

1. Deploy an SPL token mint `M` with `freeze_authority = ATTACKER`.
2. Call `log_metadata` with `mint = M`. The instruction succeeds; vault PDA `V` is created.
3. NEAR-side bridge registers `M` as a native token.
4. Victim calls `init_transfer` with `mint = M`, locking 1000 tokens into `V`. Wormhole message is posted; NEAR side credits the victim.
5. Attacker calls SPL Token `freeze_account(V, M, ATTACKER)`. Vault `V` is now frozen.
6. Victim attempts `finalize_transfer` to withdraw. The `transfer_checked` CPI from `V` fails with `AccountFrozen`. Funds are permanently locked.
7. Any subsequent `init_transfer` for `M` also fails, halting the entire token's bridge flow.

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

**File:** solana/programs/bridge_token_factory/src/instructions/user/init_transfer.rs (L28-32)
```rust
    #[account(
        mut,
        mint::token_program = token_program,
    )]
    pub mint: Box<InterfaceAccount<'info, Mint>>,
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

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs (L53-57)
```rust
    #[account(
        mut,
        mint::token_program = token_program,
    )]
    pub mint: Box<InterfaceAccount<'info, Mint>>,
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
