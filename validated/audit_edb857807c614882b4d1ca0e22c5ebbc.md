The core question is whether the bridge validates `freeze_authority` on native token mints when creating or using the vault. Let me confirm the complete picture.

The grep confirms there is **zero mention** of `freeze_authority`, `freeze_account`, or `thaw_account` anywhere in the production bridge program source. Combined with the code I've read, I have a complete picture.

---

### Title
Vault Token Account Can Be Frozen by Mint's `freeze_authority`, Permanently Locking Native Collateral — (`solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs`, `init_transfer.rs`, `finalize_transfer.rs`)

### Summary

The bridge's native-token vault PDA is a standard SPL token account. The `log_metadata` instruction that creates it validates only that `mint_authority ≠ bridge_authority`, but never checks `freeze_authority`. An attacker who controls `freeze_authority` of a native token mint can freeze the vault after tokens are locked in it, making every future `finalize_transfer` (NEAR→Solana) for that token permanently fail, with no recovery path in the bridge.

### Finding Description

**Registration — no `freeze_authority` check:**

`log_metadata` creates the vault PDA with `init_if_needed`. The only mint constraint is:

```rust
constraint = !mint.mint_authority.contains(authority.key),
``` [1](#0-0) 

There is no constraint of the form `mint.freeze_authority == COption::None` or any equivalent. The entire production source contains zero references to `freeze_authority`.

**Locking — `init_transfer` deposits into vault:**

For native tokens, `init_transfer::process` calls `transfer_checked` from the user's account into the vault PDA:

```rust
transfer_checked(
    CpiContext::new(..., TransferChecked {
        from: self.from.to_account_info(),
        to: vault.to_account_info(),
        ...
    }),
    ...
)?;
``` [2](#0-1) 

**Release — `finalize_transfer` reads from vault:**

For native tokens, `finalize_transfer::process` calls `transfer_checked` from the vault to the recipient:

```rust
transfer_checked(
    CpiContext::new_with_signer(..., TransferChecked {
        from: vault.to_account_info(),
        to: self.token_account.to_account_info(),
        authority: self.authority.to_account_info(),
        ...
    }, ...),
    ...
)?;
``` [3](#0-2) 

SPL Token's `transfer_checked` unconditionally fails if the **source** account's state is `Frozen`. The bridge has no `thaw_account` or admin-rescue instruction anywhere in its instruction set.

**Attack sequence:**

1. Attacker creates a mint with `freeze_authority = attacker_keypair` (permissionless on Solana).
2. Anyone calls `log_metadata` to register the token; the vault PDA is created.
3. Victim calls `init_transfer` — tokens are locked in the vault; a Wormhole message is emitted crediting the victim on NEAR.
4. Attacker calls SPL Token's `freeze_account` on the vault PDA using `attacker_keypair` as `freeze_authority`.
5. Any subsequent `finalize_transfer` for this token (NEAR→Solana) will revert at the `transfer_checked` CPI — the vault is frozen.
6. Because Solana transactions are atomic, the nonce is not consumed on revert, but the vault remains frozen indefinitely. Tokens locked in step 3 are irrecoverable.

There is no admin instruction to unfreeze the vault or rescue its contents.

### Impact Explanation

Permanent, irrecoverable lock of all native collateral held in the vault for the affected token. Users who completed `init_transfer` (Solana→NEAR) have their Solana-side tokens permanently frozen with no bridge-level recovery path. Any NEAR→Solana `finalize_transfer` for the same token will also be permanently blocked.

### Likelihood Explanation

The precondition — attacker controls `freeze_authority` — is trivially satisfied by creating a new mint. The `log_metadata` registration is permissionless. The remaining steps (freeze the vault) require only a single SPL Token CPI call. The only friction is convincing users to bridge the attacker-created token, which is a social-engineering step. For tokens where `freeze_authority` is held by a third party (e.g., a stablecoin issuer), the same path applies if that party acts adversarially or is compromised.

### Recommendation

In `log_metadata`, add a constraint requiring that the mint's `freeze_authority` is `None`:

```rust
#[account(
    constraint = !mint.mint_authority.contains(authority.key),
    constraint = mint.freeze_authority == COption::None
        @ ErrorCode::MintHasFreezeAuthority,
    mint::token_program = token_program,
)]
pub mint: Box<InterfaceAccount<'info, Mint>>,
``` [1](#0-0) 

This prevents registration of any native token whose vault could later be frozen by an external party.

### Proof of Concept

```rust
// 1. Create mint with freeze_authority = attacker
let mint = create_mint(&[attacker_keypair], Some(&attacker_pubkey), None, 6);

// 2. Register token with bridge (permissionless)
log_metadata(mint, ...);

// 3. Victim locks tokens
init_transfer(mint, vault, amount=1_000_000, ...);
// vault now holds 1_000_000 tokens

// 4. Attacker freezes vault
freeze_account(&token_program, &vault_pda, &mint, &attacker_keypair);

// 5. Any finalize_transfer now fails
let result = finalize_transfer(mint, vault, recipient, amount=1_000_000, ...);
assert_eq!(result, Err(TokenError::AccountFrozen));
// Tokens in vault are permanently locked; no bridge recovery path exists
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
