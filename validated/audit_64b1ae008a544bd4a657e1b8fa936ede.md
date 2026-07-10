### Title
Missing Recipient Account Validation Against Signed Payload in `finalize_transfer_sol` — (File: `solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer_sol.rs`)

---

### Summary

The `FinalizeTransferSol` instruction transfers SOL from the vault to a caller-supplied `recipient` account without verifying that this account matches the `recipient` encoded in the MPC-signed `FinalizeTransferPayload`. Any caller who obtains a valid signed payload can redirect the SOL to an arbitrary account by substituting a different `recipient` in the accounts struct.

---

### Finding Description

In `finalize_transfer_sol.rs`, the `recipient` account is declared as a bare mutable `UncheckedAccount` with no constraint tying it to the payload:

```rust
/// CHECK: this can be any type of account
#[account(mut)]
pub recipient: UncheckedAccount<'info>,
``` [1](#0-0) 

The `process` function then unconditionally transfers the full amount to `self.recipient`:

```rust
transfer(
    CpiContext::new_with_signer(
        self.common.system_program.to_account_info(),
        Transfer {
            from: self.sol_vault.to_account_info(),
            to: self.recipient.to_account_info(),
        },
        &[&[SOL_VAULT_SEED, &[self.config.bumps.sol_vault]]],
    ),
    data.amount.try_into().map_err(|_| error!(ErrorCode::AmountOverflow))?,
)?;
``` [2](#0-1) 

The signed `FinalizeTransferPayload` (deserialized from `data`) contains the intended recipient address as authenticated by the MPC/Wormhole signature. However, nowhere in the accounts constraints or in `process` is there a check of the form `require!(self.recipient.key() == data.recipient, ...)`. The nonce is correctly consumed to prevent replay, but the recipient binding is entirely absent.

The same structural flaw exists in `finalize_transfer.rs`, where the `token_account` ATA is derived from the unchecked `recipient` account rather than from the payload's recipient field:

```rust
/// CHECK: this can be any type of account
pub recipient: UncheckedAccount<'info>,
...
#[account(
    init_if_needed,
    ...
    associated_token::authority = recipient,
    ...
)]
pub token_account: Box<InterfaceAccount<'info, TokenAccount>>,
``` [3](#0-2) 

---

### Impact Explanation

**Critical.** An attacker who observes a valid signed `FinalizeTransferPayload` (e.g., from a pending Wormhole VAA or mempool) for a victim's SOL transfer can call `finalize_transfer_sol` first, supplying their own account as `recipient`. The nonce is consumed, the SOL is sent to the attacker, and the legitimate recipient receives nothing and cannot retry (nonce already used). This constitutes direct theft of native SOL assets locked in the bridge vault.

For `finalize_transfer.rs`, the same attack redirects bridged SPL tokens to the attacker's ATA.

---

### Likelihood Explanation

**High.** Wormhole VAAs are publicly observable once emitted. Any unprivileged actor can submit a valid VAA with a substituted `recipient` account. No special access or key material is required beyond monitoring the chain for pending finalization messages.

---

### Recommendation

Add an explicit constraint in the accounts struct (or a `require!` in `process`) that enforces `self.recipient.key() == data.recipient` (where `data.recipient` is the Solana pubkey encoded in the signed payload). For example, in the accounts struct:

```rust
#[account(
    mut,
    constraint = recipient.key() == data.payload.recipient @ ErrorCode::InvalidRecipient
)]
pub recipient: UncheckedAccount<'info>,
```

Apply the same fix to `finalize_transfer.rs`.

---

### Proof of Concept

1. Alice initiates a NEAR → Solana SOL transfer. The NEAR bridge emits a Wormhole message; MPC signs a `FinalizeTransferPayload` with `recipient = alice_pubkey`, `destination_nonce = N`, `amount = X`.
2. Attacker observes the signed VAA on-chain before Alice finalizes.
3. Attacker calls `finalize_transfer_sol` with the valid `SignedPayload` but passes `attacker_pubkey` as the `recipient` account.
4. `UsedNonces::use_nonce(N, ...)` succeeds (nonce not yet used).
5. `transfer(sol_vault → attacker_pubkey, X)` executes — attacker receives Alice's SOL.
6. Alice attempts to call `finalize_transfer_sol` with her own pubkey; the call reverts with `NonceAlreadyUsed`. [4](#0-3)

### Citations

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer_sol.rs (L52-54)
```rust
    /// CHECK: this can be any type of account
    #[account(mut)]
    pub recipient: UncheckedAccount<'info>,
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer_sol.rs (L67-102)
```rust
impl FinalizeTransferSol<'_> {
    pub fn process(&mut self, data: FinalizeTransferPayload) -> Result<()> {
        UsedNonces::use_nonce(
            data.destination_nonce,
            &self.used_nonces,
            &mut self.config,
            self.authority.to_account_info(),
            self.common.payer.to_account_info(),
            &Rent::get()?,
            self.system_program.to_account_info(),
        )?;

        transfer(
            CpiContext::new_with_signer(
                self.common.system_program.to_account_info(),
                Transfer {
                    from: self.sol_vault.to_account_info(),
                    to: self.recipient.to_account_info(),
                },
                &[&[SOL_VAULT_SEED, &[self.config.bumps.sol_vault]]],
            ),
            data.amount.try_into().map_err(|_| error!(ErrorCode::AmountOverflow))?,
        )?;

        let payload = FinalizeTransferResponse {
            token: Pubkey::default(),
            amount: data.amount,
            fee_recipient: data.fee_recipient.unwrap_or_default(),
            transfer_id: data.transfer_id,
        }
        .serialize_for_near(())?;

        self.common.post_message(payload)?;

        Ok(())
    }
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs (L50-80)
```rust
    /// CHECK: this can be any type of account
    pub recipient: UncheckedAccount<'info>,

    #[account(
        mut,
        mint::token_program = token_program,
    )]
    pub mint: Box<InterfaceAccount<'info, Mint>>,

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

    #[account(
        init_if_needed,
        payer = common.payer,
        associated_token::mint = mint,
        associated_token::authority = recipient,
        token::token_program = token_program,
    )]
    pub token_account: Box<InterfaceAccount<'info, TokenAccount>>,
```
