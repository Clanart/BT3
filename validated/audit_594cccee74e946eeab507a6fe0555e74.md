### Title
Recipient Not Bound by MPC Signature in `finalize_transfer` — Relayer Can Redirect Tokens to Arbitrary Account

**File:** `solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs` and `finalize_transfer_sol.rs`

---

### Summary

The `recipient` account in both `FinalizeTransfer` and `FinalizeTransferSol` is declared as `UncheckedAccount` with no constraint tying it to the MPC-signed payload. The `FinalizeTransferPayload` struct does not contain a recipient field, so the MPC signature covers only `destination_nonce`, `transfer_id`, `amount`, and `fee_recipient`. Any relayer can pass an attacker-controlled pubkey as `recipient` while reusing a legitimately-obtained MPC signature, redirecting all tokens or SOL to the attacker.

---

### Finding Description

`FinalizeTransferPayload` is defined as:

```rust
pub struct FinalizeTransferPayload {
    pub destination_nonce: u64,
    pub transfer_id: TransferId,
    pub amount: u128,
    pub fee_recipient: Option<String>,
}
``` [1](#0-0) 

There is no `recipient` field. The MPC signature covers only these four fields.

In `FinalizeTransfer`, the recipient is declared as:

```rust
/// CHECK: this can be any type of account
pub recipient: UncheckedAccount<'info>,
``` [2](#0-1) 

The `token_account` is derived as the ATA of this unconstrained `recipient`:

```rust
associated_token::authority = recipient,
``` [3](#0-2) 

The `process` function never checks `self.recipient.key()` against any field in the signed payload: [4](#0-3) 

The same flaw exists in `FinalizeTransferSol`, where SOL is transferred directly to the unconstrained `recipient`: [5](#0-4) [6](#0-5) 

---

### Impact Explanation

- For SPL token transfers: tokens are minted or unlocked to the ATA of whoever is passed as `recipient`. An attacker-controlled relayer passes their own pubkey; tokens land in the attacker's ATA.
- For SOL transfers (`FinalizeTransferSol`): SOL is transferred directly from `sol_vault` to the unconstrained `recipient`. Same redirect is possible.
- The nonce is consumed, so the legitimate recipient can never claim the transfer again (permanent loss).

Impact: **Critical** — direct theft of bridged assets.

---

### Likelihood Explanation

The question's proof idea slightly misstates the mechanism: you do not need to generate an MPC signature "for attacker pubkey as recipient." Because `recipient` is not in the signed payload at all, **any** valid MPC signature for a transfer (correct nonce, amount, transfer_id) works regardless of which `recipient` account is passed. A malicious or front-running relayer can intercept any pending finalization and redirect it. No privileged access, leaked key, or MPC collusion is required — only the ability to submit a Solana transaction before the legitimate relayer.

---

### Recommendation

Include the intended recipient pubkey inside `FinalizeTransferPayload` so it is covered by the MPC signature:

```rust
pub struct FinalizeTransferPayload {
    pub destination_nonce: u64,
    pub transfer_id: TransferId,
    pub amount: u128,
    pub fee_recipient: Option<String>,
    pub recipient: Pubkey,   // ADD THIS
}
```

Then in `process`, enforce:

```rust
require_keys_eq!(self.recipient.key(), data.recipient, ErrorCode::InvalidRecipient);
```

This ensures the on-chain `recipient` account must match the NEAR-designated recipient that the MPC signed over.

---

### Proof of Concept

1. User initiates a NEAR→Solana transfer designating `legitimate_user` as recipient.
2. NEAR MPC produces a valid signature over `(destination_nonce, transfer_id, amount, fee_recipient)`.
3. Attacker (relayer) calls `finalize_transfer` with the valid signed payload but substitutes `attacker_pubkey` for `recipient`.
4. Anchor derives `token_account` as the ATA of `attacker_pubkey`.
5. Tokens are minted/unlocked to the attacker's ATA; nonce is consumed.
6. `legitimate_user` receives nothing and cannot retry (nonce is spent).

### Citations

**File:** solana/programs/bridge_token_factory/src/state/message/finalize_transfer.rs (L11-16)
```rust
pub struct FinalizeTransferPayload {
    pub destination_nonce: u64,
    pub transfer_id: TransferId,
    pub amount: u128,
    pub fee_recipient: Option<String>,
}
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs (L50-51)
```rust
    /// CHECK: this can be any type of account
    pub recipient: UncheckedAccount<'info>,
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs (L73-80)
```rust
    #[account(
        init_if_needed,
        payer = common.payer,
        associated_token::mint = mint,
        associated_token::authority = recipient,
        token::token_program = token_program,
    )]
    pub token_account: Box<InterfaceAccount<'info, TokenAccount>>,
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs (L89-149)
```rust
impl FinalizeTransfer<'_> {
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

        let payload = FinalizeTransferResponse {
            token: self.mint.key(),
            amount: data.amount,
            fee_recipient: data.fee_recipient.unwrap_or_default(),
            transfer_id: data.transfer_id,
        }
        .serialize_for_near(())?;

        self.common.post_message(payload)?;

        Ok(())
    }
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer_sol.rs (L52-54)
```rust
    /// CHECK: this can be any type of account
    #[account(mut)]
    pub recipient: UncheckedAccount<'info>,
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer_sol.rs (L79-88)
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
```
