### Title
Unconstrained `from` Token Account in `InitTransfer` Allows Draining Delegated Accounts - (File: `solana/programs/bridge_token_factory/src/instructions/user/init_transfer.rs`)

---

### Summary

The Solana `InitTransfer` account struct does not constrain the `from` token account to be owned by the `user` signer. Any caller who holds a valid SPL token delegation over a victim's token account can pass that victim's account as `from`, bridge the victim's tokens to an attacker-controlled destination address, and permanently remove them from the victim's custody.

---

### Finding Description

In `solana/programs/bridge_token_factory/src/instructions/user/init_transfer.rs`, the `InitTransfer` account struct defines `from` with only mint and token-program constraints:

```rust
#[account(
    mut,
    token::mint = mint,
    token::token_program = token_program,
)]
pub from: Box<InterfaceAccount<'info, TokenAccount>>,
``` [1](#0-0) 

There is no `token::authority = user` constraint. The `user` field is only required to be a `Signer` and a system-program-owned account:

```rust
#[account(
    mut,
    owner = common.system_program.key(),
)]
pub user: Signer<'info>,
``` [2](#0-1) 

The `process` function then executes `transfer_checked` (or `burn`) using `self.user` as the authority over `self.from`:

```rust
transfer_checked(
    CpiContext::new(
        self.token_program.to_account_info(),
        TransferChecked {
            from: self.from.to_account_info(),
            ...
            authority: self.user.to_account_info(),
``` [3](#0-2) 

The SPL Token program permits `transfer_checked` when the `authority` is either the **owner** of `from` or an **approved delegate** of `from`. Because Anchor does not enforce `token::authority = user` on `from`, an attacker who holds a delegation over a victim's token account can supply the victim's account as `from` and their own key as `user`. The SPL Token program will accept the transfer because the attacker is a valid delegate.

The same unconstrained pattern applies to the `burn` path for bridged tokens:

```rust
burn(
    CpiContext::new(
        self.token_program.to_account_info(),
        Burn {
            mint: self.mint.to_account_info(),
            from: self.from.to_account_info(),
            authority: self.user.to_account_info(),
        },
    ),
``` [4](#0-3) 

The `recipient` field in `InitTransferPayload` is fully attacker-controlled, so the attacker directs the bridged value to their own address on the destination chain.

---

### Impact Explanation

**Critical — Direct theft of user tokens across chains.**

An attacker who is a delegate of a victim's SPL token account can:
1. Call `init_transfer` with `from = victim_token_account`, `user = attacker`, and `recipient = attacker_destination_address`.
2. The SPL Token program transfers tokens out of the victim's account into the bridge vault (or burns them for bridged tokens).
3. The Wormhole message credits the attacker's destination address with the full bridged amount.

The victim's tokens are permanently removed from their custody and delivered to the attacker on the destination chain. There is no on-chain mechanism to reverse a completed bridge transfer.

---

### Likelihood Explanation

SPL token delegation (`approve`) is used by DeFi protocols, wallets, and aggregators. Realistic delegation scenarios that leave a non-zero delegate set on a user's token account include:

- A prior DeFi interaction that set a delegate and was not revoked.
- An `init_transfer` attempt that failed after `approve` but before the bridge call, leaving the delegation active.
- A user who approved a higher amount than they eventually transferred.
- A user who made an unlimited approval to a protocol the attacker now controls.

Any of these leaves the victim exposed. The attacker only needs to discover the delegation (on-chain state is public) and call `init_transfer` before the victim revokes it.

---

### Recommendation

Add `token::authority = user` to the `from` account constraint in the `InitTransfer` struct:

```rust
#[account(
    mut,
    token::mint = mint,
    token::authority = user,   // <-- add this
    token::token_program = token_program,
)]
pub from: Box<InterfaceAccount<'info, TokenAccount>>,
```

This enforces at the Anchor level that `from` is owned by the signing `user`, preventing any third-party token account from being passed as the source.

---

### Proof of Concept

**Setup:**
- Victim holds 1,000 USDC in `victim_token_account` (mint = USDC).
- Victim previously approved `attacker_pubkey` as a delegate for 500 USDC on `victim_token_account` (e.g., from a failed prior bridge attempt).

**Attack:**
1. Attacker constructs an `InitTransfer` instruction with:
   - `from` = `victim_token_account`
   - `user` = `attacker_pubkey` (signer)
   - `mint` = USDC mint
   - `vault` = bridge USDC vault (exists, so native path is taken)
   - `payload.amount` = 500 (within the delegation limit)
   - `payload.recipient` = attacker's EVM address
2. Attacker submits the transaction.
3. Anchor validates: `from.mint == mint` ✓, `from.token_program == token_program` ✓ — no ownership check.
4. SPL Token executes `transfer_checked(from=victim_token_account, authority=attacker)` — succeeds because attacker is a delegate.
5. 500 USDC moves from `victim_token_account` to the bridge vault.
6. Wormhole message is posted crediting attacker's EVM address with 500 USDC.
7. Attacker finalizes on EVM and receives 500 USDC.

The victim loses 500 USDC with no recourse. [5](#0-4)

### Citations

**File:** solana/programs/bridge_token_factory/src/instructions/user/init_transfer.rs (L20-69)
```rust
#[derive(Accounts)]
pub struct InitTransfer<'info> {
    #[account(
        seeds = [AUTHORITY_SEED],
        bump = common.config.bumps.authority,
    )]
    pub authority: SystemAccount<'info>,

    #[account(
        mut,
        mint::token_program = token_program,
    )]
    pub mint: Box<InterfaceAccount<'info, Mint>>,

    #[account(
        mut,
        token::mint = mint,
        token::token_program = token_program,
    )]
    pub from: Box<InterfaceAccount<'info, TokenAccount>>,
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

    #[account(
        mut,
        seeds = [SOL_VAULT_SEED],
        bump = common.config.bumps.sol_vault,
    )]
    pub sol_vault: SystemAccount<'info>,

    #[account(
        mut,
        owner = common.system_program.key(),
    )]
    pub user: Signer<'info>,

    pub common: WormholeCPI<'info>,

    pub token_program: Interface<'info, TokenInterface>,
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

**File:** solana/programs/bridge_token_factory/src/instructions/user/init_transfer.rs (L110-120)
```rust
            burn(
                CpiContext::new(
                    self.token_program.to_account_info(),
                    Burn {
                        mint: self.mint.to_account_info(),
                        from: self.from.to_account_info(),
                        authority: self.user.to_account_info(),
                    },
                ),
                payload.amount.try_into().map_err(|_| error!(ErrorCode::InvalidArgs))?,
            )?;
```
