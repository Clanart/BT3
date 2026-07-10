### Title
Token-2022 `PermanentDelegate` Extension Allows Vault Drainage After `init_transfer` Lock — (`solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs`, `init_transfer.rs`)

---

### Summary

Neither `log_metadata` nor `init_transfer` checks for the Token-2022 `PermanentDelegate` extension on the mint being registered. An attacker can register a Token-2022 mint whose `permanent_delegate` is set to an attacker-controlled address, lock tokens into the bridge vault via `init_transfer`, and then use the permanent delegate authority to drain the vault — while NEAR retains a full credit for the locked amount, creating unbacked supply and constituting direct theft.

---

### Finding Description

**Step 1 — Registration via `log_metadata` (no `PermanentDelegate` check)**

The only constraint on the mint in `LogMetadata` is:

```rust
constraint = !mint.mint_authority.contains(authority.key),
``` [1](#0-0) 

There is no inspection of Token-2022 extensions. A mint with `permanent_delegate = attacker` passes this constraint trivially (the mint authority is not the bridge authority). The vault is then created with `token::authority = authority` (the bridge PDA): [2](#0-1) 

**Step 2 — Locking via `init_transfer`**

When `vault` is `Some`, `init_transfer` performs a `transfer_checked` from the user's account into the vault: [3](#0-2) 

No extension check is performed here either. A grep across the entire Solana program source confirms zero references to `PermanentDelegate` or any extension guard:



**Step 3 — Vault drainage via permanent delegate**

The Token-2022 `PermanentDelegate` extension grants the designated address the ability to transfer or burn tokens from **any** token account holding that mint, regardless of the account's `authority` field. The vault's `token::authority = authority` (bridge PDA) only prevents normal CPI transfers that require the PDA's signature — it does not block a `transfer_checked` CPI invoked by the permanent delegate. The attacker calls `spl_token_2022::transfer_checked(from=vault, to=attacker_ata, authority=permanent_delegate)` directly, outside the bridge program, draining the vault.

**Step 4 — NEAR retains credit**

After `init_transfer` posts the Wormhole message, NEAR records the locked amount. The vault is now empty, but NEAR will process a `finalize_transfer` on the destination chain, minting or releasing bridged tokens backed by nothing.

---

### Impact Explanation

Direct theft of locked native tokens from the vault. The attacker receives the drained tokens on Solana while NEAR issues a corresponding credit, breaking bridge collateralization. This is a Critical impact: unauthorized release of native assets from the vault.

---

### Likelihood Explanation

The attack is fully unprivileged. Creating a Token-2022 mint with `permanent_delegate` is a standard, permissionless on-chain operation. The entire call sequence — `log_metadata` → `init_transfer` → permanent-delegate drain — is executable by any attacker with no special access. The only cost is the token amount locked in `init_transfer`, which is immediately recovered by the drain.

---

### Recommendation

In `log_metadata`, after unpacking the mint with `StateWithExtensions`, check for the `PermanentDelegate` extension and reject registration if it is present:

```rust
use spl_token_2022::extension::permanent_delegate::PermanentDelegate;

if mint_with_extension.get_extension::<PermanentDelegate>().is_ok() {
    return err!(ErrorCode::UnsupportedMintExtension);
}
```

Apply the same guard in `init_transfer` (or in a shared mint-validation helper) to prevent tokens from being locked into a vault whose mint carries this extension. Similar guards should be considered for other dangerous Token-2022 extensions (e.g., `TransferHook`, `ConfidentialTransfer`, `MintCloseAuthority`).

---

### Proof of Concept

```
1. Create Token-2022 mint M with permanent_delegate = ATTACKER.
2. Call log_metadata(mint=M) → vault V is created, Wormhole message sent to NEAR.
3. NEAR registers M as a native token.
4. Call init_transfer(mint=M, from=USER_ATA, vault=V, amount=N) → V holds N tokens;
   Wormhole message sent to NEAR crediting N.
5. Outside the bridge program, call:
     spl_token_2022::transfer_checked(
         from=V, to=ATTACKER_ATA, authority=ATTACKER, amount=N, mint=M
     )
   → V is now empty; ATTACKER_ATA holds N tokens.
6. NEAR processes the init_transfer message and issues N bridged tokens on the
   destination chain, backed by zero collateral in V.
```

Vault balance invariant broken: vault holds 0 after step 5, but NEAR holds a credit of N — direct theft confirmed.

### Citations

**File:** solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs (L41-45)
```rust
    #[account(
        constraint = !mint.mint_authority.contains(authority.key),
        mint::token_program = token_program,
    )]
    pub mint: Box<InterfaceAccount<'info, Mint>>,
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
