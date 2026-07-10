### Title
Unconstrained `recipient` Account in `FinalizeTransfer` Allows Attacker to Redirect Bridged Tokens to Arbitrary Address — (File: `solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs`)

---

### Summary

The Solana `FinalizeTransfer` instruction accepts a `recipient` account with no constraint binding it to the signed payload's intended recipient. Because the `token_account` (where tokens are minted or transferred) is derived as the ATA of the caller-supplied `recipient`, any party can call `finalize_transfer` for a pending cross-chain transfer and redirect the bridged tokens to an arbitrary address. The nonce is then consumed, permanently preventing the legitimate recipient from claiming their funds.

---

### Finding Description

In `finalize_transfer.rs`, the Anchor accounts struct declares `recipient` as a bare `UncheckedAccount` with no constraint:

```rust
/// CHECK: this can be any type of account
pub recipient: UncheckedAccount<'info>,
``` [1](#0-0) 

The `token_account` — the destination for minted or vault-released tokens — is derived entirely from this unconstrained `recipient`:

```rust
#[account(
    init_if_needed,
    payer = common.payer,
    associated_token::mint = mint,
    associated_token::authority = recipient,
    token::token_program = token_program,
)]
pub token_account: Box<InterfaceAccount<'info, TokenAccount>>,
``` [2](#0-1) 

Inside `process()`, the signed `FinalizeTransferPayload` fields consumed are `destination_nonce`, `amount`, `fee_recipient`, and `transfer_id`. There is no `data.recipient` field referenced, and no constraint of the form `recipient.key() == data.payload.recipient` anywhere in the accounts struct:

```rust
pub fn process(&mut self, data: FinalizeTransferPayload) -> Result<()> {
    UsedNonces::use_nonce(data.destination_nonce, ...)?;
    // tokens transferred/minted to self.token_account (ATA of caller-supplied recipient)
    ...
    let payload = FinalizeTransferResponse {
        token: self.mint.key(),
        amount: data.amount,
        fee_recipient: data.fee_recipient.unwrap_or_default(),
        transfer_id: data.transfer_id,
    }.serialize_for_near(())?;
``` [3](#0-2) 

The `FinalizeTransferResponse` posted back to NEAR also omits the recipient, confirming the recipient is not part of the MPC-signed payload and is entirely caller-controlled. [4](#0-3) 

The same pattern exists in `FinalizeTransferSol` for native SOL transfers:

```rust
/// CHECK: this can be any type of account
#[account(mut)]
pub recipient: UncheckedAccount<'info>,
``` [5](#0-4) 

The SOL transfer goes directly to `self.recipient` with no verification against the signed payload. [6](#0-5) 

---

### Impact Explanation

**Critical — Direct theft of bridged assets on Solana.**

An attacker who observes a pending `finalize_transfer` call (e.g., from a relayer's mempool submission or by monitoring the Wormhole VAA) can front-run it by submitting their own `finalize_transfer` transaction with the correct signed payload but substituting their own Solana address as `recipient`. The tokens are minted or released to the attacker's ATA. The destination nonce is then marked used, making the transfer permanently unclaimable by the legitimate recipient. This constitutes direct theft of bridged assets and permanent freezing of the victim's funds in a single atomic action.

---

### Likelihood Explanation

**High.** Solana transactions are publicly observable before finalization. Any party monitoring the Wormhole VAA feed or the bridge relayer's pending transactions can extract the signed `FinalizeTransferPayload` (which is public), construct a competing transaction with an attacker-controlled `recipient`, and submit it with higher priority fees. No privileged access, leaked keys, or colluding validators are required — only the ability to submit a valid Solana transaction.

---

### Recommendation

Add an explicit constraint in the `FinalizeTransfer` and `FinalizeTransferSol` accounts structs that binds the `recipient` account to the recipient encoded in the signed payload:

```rust
#[account(
    constraint = recipient.key() == data.payload.recipient @ ErrorCode::InvalidRecipient
)]
pub recipient: UncheckedAccount<'info>,
```

This requires adding a `recipient: Pubkey` field to `FinalizeTransferPayload` (covered by the MPC signature) and verifying it in the Anchor constraint. The NEAR side must include the Solana recipient address in the signed transfer payload so the MPC signature covers it.

---

### Proof of Concept

1. Alice initiates a NEAR → Solana transfer of 1000 USDC, specifying her Solana address `alice_pubkey` as recipient.
2. The MPC produces a signed `FinalizeTransferPayload` with `destination_nonce = N`, `amount = 1000`, `transfer_id = T`. The payload does **not** include `alice_pubkey`.
3. A relayer broadcasts a `finalize_transfer` transaction with `recipient = alice_pubkey`.
4. Attacker observes this transaction before it lands. Attacker constructs an identical `finalize_transfer` call using the same signed payload but sets `recipient = attacker_pubkey`.
5. Attacker submits with higher priority fee. Anchor validates: nonce N not yet used ✓, signature valid ✓, `recipient` unconstrained ✓. Tokens are minted to attacker's ATA.
6. Relayer's transaction lands next: `UsedNonces::use_nonce` fails — nonce N already used. Alice's 1000 USDC is permanently lost.

### Citations

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

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs (L89-148)
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
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer_sol.rs (L52-54)
```rust
    /// CHECK: this can be any type of account
    #[account(mut)]
    pub recipient: UncheckedAccount<'info>,
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer_sol.rs (L79-89)
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
```
