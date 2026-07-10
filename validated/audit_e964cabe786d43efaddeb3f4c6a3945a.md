### Title
Vault-Bypass Unbacked Mint via Optional Vault Account in `finalize_transfer` — (`solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs`)

---

### Summary

`FinalizeTransfer` uses the caller-supplied presence or absence of the optional `vault` account as the sole discriminator between the native-token (vault-release) path and the bridged-token (mint) path. There is no on-chain check that a vault PDA does not exist when `vault=None` is passed. A token creator who transfers mint authority to the bridge authority PDA after vault creation can cause any subsequent `finalize_transfer` call that omits the vault to mint unbacked supply, permanently stranding the original locked collateral.

---

### Finding Description

**Step 1 — Vault creation via `log_metadata`.**

`LogMetadata` enforces a one-time constraint at registration:

```rust
constraint = !mint.mint_authority.contains(authority.key),
```

This passes only when the bridge authority does **not** hold mint authority, and it creates the vault PDA at `[VAULT_SEED, mint.key()]`. [1](#0-0) 

**Step 2 — Mint authority transfer (post-registration).**

After `log_metadata` succeeds, the token creator calls SPL `SetAuthority` to transfer the mint authority to the bridge authority PDA. The bridge program has no instruction or constraint that monitors or prevents this post-hoc transfer.

**Step 3 — `finalize_transfer` with `vault=None`.**

The `vault` field is declared `Option<…>`:

```rust
pub vault: Option<Box<InterfaceAccount<'info, TokenAccount>>>,
``` [2](#0-1) 

When the caller omits the vault account, Anchor skips all PDA/seed constraints on it. The runtime branch is:

```rust
if let Some(vault) = &self.vault {
    // vault transfer path
} else {
    require!(
        self.mint.mint_authority.contains(self.authority.key),
        ErrorCode::InvalidBridgedToken
    );
    mint_to(...)
}
``` [3](#0-2) 

Because the bridge authority now holds mint authority (step 2), the `require!` passes and `mint_to` executes — minting tokens to the recipient without touching the vault.

**The missing guard:** there is no instruction anywhere in the program that checks whether the vault PDA account exists on-chain before taking the mint path. The comment on line 59 ("if this account exists the mint registration is already sent") documents the intended invariant but does not enforce it when the account is omitted. [2](#0-1) 

---

### Impact Explanation

- **Unbacked supply minted:** `mint_to` creates new tokens backed by nothing; the NEAR-side proof is consumed (nonce marked used), so the recipient receives tokens without any collateral being released.
- **Vault collateral permanently stranded:** the vault PDA still holds every token ever locked by users who bridged Solana→NEAR. Those tokens can never be released because the nonce for this settlement has already been consumed.
- **Double-supply condition:** total on-chain supply exceeds total locked collateral by exactly the minted amount, breaking the 1:1 bridge invariant permanently.

---

### Likelihood Explanation

The prerequisite — transferring mint authority to the bridge authority PDA — requires the token creator to take a deliberate action. However:

- The token creator is not a privileged bridge operator; they are an arbitrary external party.
- The bridge protocol places no restriction on post-registration `SetAuthority` calls.
- A malicious token creator can set up the condition and then act as the relayer themselves, making this a single-actor exploit.
- The exploit is fully local-testable with no external dependencies.

---

### Recommendation

In the `else` branch of `FinalizeTransfer::process`, add an explicit on-chain check that the vault PDA does not exist before taking the mint path:

```rust
} else {
    // Ensure no vault PDA exists for this mint (would indicate a native token)
    let vault_pda = Pubkey::find_program_address(
        &[VAULT_SEED, self.mint.key().as_ref()],
        ctx.program_id,
    ).0;
    require!(
        ctx.accounts.vault_info.key() != vault_pda || !vault_info_is_initialized,
        ErrorCode::NativeTokenMustUseVault
    );
    require!(
        self.mint.mint_authority.contains(self.authority.key),
        ErrorCode::InvalidBridgedToken
    );
    mint_to(...)?;
}
```

Alternatively, make `vault` a **required** (non-optional) account and use a separate instruction discriminator for bridged vs. native tokens, so the path cannot be chosen by the caller at runtime.

---

### Proof of Concept

```
1. Create SPL token T; mint authority = attacker keypair A.
2. Call log_metadata(T) → vault PDA V = [VAULT_SEED, T] is created.
   (Constraint !mint_authority.contains(bridge_authority) passes.)
3. User U bridges 1000 T from Solana→NEAR:
   init_transfer deposits 1000 T into V.
4. Attacker calls SPL SetAuthority(T, MintTokens, bridge_authority_PDA).
   Now T.mint_authority == bridge_authority_PDA.
5. Attacker (as relayer) calls finalize_transfer(vault=None, amount=1000, recipient=attacker).
   - vault is None → else branch taken.
   - require!(mint_authority.contains(bridge_authority)) → PASSES.
   - mint_to(attacker_ATA, 1000) → succeeds.
6. Observe:
   - Attacker ATA holds 1000 T (minted, unbacked).
   - Vault V still holds 1000 T (locked, permanently stranded).
   - Total supply increased by 1000; bridge is undercollateralized by 1000 T.
   - Nonce consumed; U's locked tokens can never be recovered.
```

### Citations

**File:** solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs (L41-62)
```rust
    #[account(
        constraint = !mint.mint_authority.contains(authority.key),
        mint::token_program = token_program,
    )]
    pub mint: Box<InterfaceAccount<'info, Mint>>,

    /// CHECK: may be unitialized
    pub metadata: Option<UncheckedAccount<'info>>,

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

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs (L59-71)
```rust
    // if this account exists the mint registration is already sent
    #[account(
        mut,
        token::mint = mint,
        token::authority = authority,
        seeds = [
            VAULT_SEED,
            mint.key().as_ref(),
        ],
        bump,
        token::token_program = token_program,
    )]
    pub vault: Option<Box<InterfaceAccount<'info, TokenAccount>>>,
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs (L101-136)
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
        } else {
            // Bridged version. May be a fake token with our authority set but it will be ignored on the near side
            require!(
                self.mint.mint_authority.contains(self.authority.key),
                ErrorCode::InvalidBridgedToken
            );

            mint_to(
                CpiContext::new_with_signer(
                    self.token_program.to_account_info(),
                    MintTo {
                        mint: self.mint.to_account_info(),
                        to: self.token_account.to_account_info(),
                        authority: self.authority.to_account_info(),
                    },
                    &[&[AUTHORITY_SEED, &[self.config.bumps.authority]]],
                ),
                data.amount.try_into().map_err(|_| error!(ErrorCode::AmountOverflow))?,
            )?;
        }
```
