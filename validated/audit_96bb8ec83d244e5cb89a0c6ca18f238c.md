### Title
Unvalidated `recipient` Account in Solana `FinalizeTransfer` Allows Attacker to Redirect Bridged Tokens to Arbitrary Address — (File: `solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs`)

---

### Summary

The Solana `FinalizeTransfer` instruction accepts a MPC-signed `FinalizeTransferPayload` but never validates that the `recipient` account passed in the transaction matches the intended recipient encoded in (or implied by) the signed payload. Because `recipient` is declared as an unconstrained `UncheckedAccount` with no Anchor address constraint, any caller can substitute their own address as `recipient`, causing the bridge to mint or release tokens into the attacker's associated token account while permanently consuming the destination nonce, permanently locking the legitimate recipient out of their funds.

---

### Finding Description

In `FinalizeTransfer`, the Anchor account struct is:

```rust
#[derive(Accounts)]
#[instruction(data: SignedPayload<FinalizeTransferPayload>)]
pub struct FinalizeTransfer<'info> {
    ...
    /// CHECK: this can be any type of account
    pub recipient: UncheckedAccount<'info>,

    #[account(
        init_if_needed,
        payer = common.payer,
        associated_token::mint = mint,
        associated_token::authority = recipient,   // ← derived from attacker-supplied recipient
        ...
    )]
    pub token_account: Box<InterfaceAccount<'info, TokenAccount>>,
    ...
}
``` [1](#0-0) 

The `recipient` field carries the Anchor safety comment `/// CHECK: this can be any type of account` and has **no `address =` constraint** tying it to any field in `data.payload`. The `#[instruction(data: SignedPayload<FinalizeTransferPayload>)]` attribute makes the signed payload available for use in account constraints, but no such constraint is applied to `recipient`. [2](#0-1) 

Inside `process`, the only nonce-level guard is:

```rust
UsedNonces::use_nonce(
    data.destination_nonce,
    &self.used_nonces,
    ...
)?;
``` [3](#0-2) 

After the nonce is consumed, tokens are unconditionally transferred or minted to `self.token_account`, which is the ATA of the caller-supplied `recipient`:

```rust
transfer_checked(... to: self.token_account ..., ...)?;
// or
mint_to(... to: self.token_account ..., ...)?;
``` [4](#0-3) 

On the NEAR side, `sign_transfer` explicitly includes the `recipient` in the MPC-signed `TransferMessagePayload`:

```rust
let transfer_payload = TransferMessagePayload {
    ...
    recipient: transfer_message.recipient,
    ...
};
``` [5](#0-4) 

The MPC signature therefore covers a specific intended recipient. However, the Solana program never enforces that the `recipient` account supplied in the transaction equals the recipient committed to in the signed payload. The signature verification passes (the payload bytes are unchanged), but the tokens flow to an attacker-controlled address.

---

### Impact Explanation

**Critical — Direct theft of bridged assets.**

An attacker who front-runs or replaces a legitimate `finalize_transfer` transaction with the same valid MPC-signed payload but their own address as `recipient` will:

1. Pass signature verification (payload is unmodified).
2. Consume the destination nonce, making it permanently unusable.
3. Receive all bridged tokens (native unlock or bridged mint) into their own ATA.

The legitimate recipient permanently loses their funds with no recourse, because the nonce is marked used and the bridge will reject any future attempt to finalize the same transfer.

---

### Likelihood Explanation

**High.** The signed payload is broadcast on-chain via NEAR events (`SignTransferEvent`) and is publicly observable. Any network participant can extract the MPC signature and payload, construct a `finalize_transfer` transaction substituting their own address as `recipient`, and submit it before or instead of the legitimate relayer. No privileged access, leaked key, or colluding MPC signer is required — only the ability to read public NEAR chain events and submit a Solana transaction.

---

### Recommendation

Add an Anchor address constraint on `recipient` that binds it to the recipient field in the signed payload. For example:

```rust
#[account(address = data.payload.recipient @ ErrorCode::InvalidRecipient)]
pub recipient: UncheckedAccount<'info>,
```

This ensures the MPC-signed recipient is the only account that can receive the tokens, closing the substitution attack entirely.

---

### Proof of Concept

1. Alice initiates a bridge transfer from NEAR to Solana for 1000 USDC, with her Solana address as recipient.
2. The NEAR bridge MPC signs a `FinalizeTransferPayload` (covering `destination_nonce`, `amount`, `transfer_id`, and the intended recipient) and emits a `SignTransferEvent`.
3. Attacker Bob observes the event, extracts the signed payload.
4. Bob constructs a `finalize_transfer` Solana transaction using the **identical** signed payload but sets `recipient = Bob's Pubkey`.
5. Anchor derives `token_account` as Bob's ATA for the mint.
6. The program verifies the MPC signature (passes — payload unchanged), marks the nonce used, and mints/transfers 1000 USDC to Bob's ATA.
7. Alice's transfer is permanently finalized with Bob as recipient. Alice receives nothing and cannot retry (nonce exhausted). [6](#0-5)

### Citations

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs (L50-81)
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

**File:** near/omni-bridge/src/lib.rs (L491-500)
```rust
        let transfer_payload = TransferMessagePayload {
            prefix: PayloadType::TransferMessage,
            destination_nonce: transfer_message.destination_nonce,
            transfer_id,
            token_address,
            amount: U128(amount_to_transfer),
            recipient: transfer_message.recipient,
            fee_recipient,
            message,
        };
```
