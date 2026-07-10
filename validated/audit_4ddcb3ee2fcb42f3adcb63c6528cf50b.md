### Title
Unchecked `recipient` Account in Solana `FinalizeTransfer` and `FinalizeTransferSol` Allows Arbitrary Recipient Substitution - (File: `solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs`, `finalize_transfer_sol.rs`)

### Summary
The Solana bridge program's `FinalizeTransfer` and `FinalizeTransferSol` instruction account structs declare the `recipient` account as `UncheckedAccount` with no address constraint tying it to the intended recipient encoded in the signed `FinalizeTransferPayload`. As a result, any caller can pass an arbitrary account as `recipient` and receive the bridged tokens or native SOL instead of the intended beneficiary.

### Finding Description
In `FinalizeTransfer`, the `recipient` field is declared as:

```rust
/// CHECK: this can be any type of account
pub recipient: UncheckedAccount<'info>,
``` [1](#0-0) 

The `token_account` is derived as the ATA of `recipient`:

```rust
#[account(
    init_if_needed,
    payer = common.payer,
    associated_token::mint = mint,
    associated_token::authority = recipient,
    ...
)]
pub token_account: Box<InterfaceAccount<'info, TokenAccount>>,
``` [2](#0-1) 

Tokens are then minted or transferred into `self.token_account` (i.e., the ATA of the caller-supplied `recipient`):

```rust
transfer_checked(... TransferChecked { from: vault, to: self.token_account, ... }, ...)?;
// or
mint_to(... MintTo { to: self.token_account, ... }, ...)?;
``` [3](#0-2) 

The same pattern exists in `FinalizeTransferSol`, where native SOL is transferred directly to `self.recipient`:

```rust
transfer(
    CpiContext::new_with_signer(..., Transfer { from: self.sol_vault, to: self.recipient }, ...),
    data.amount...,
)?;
``` [4](#0-3) 

In neither instruction is there an Anchor account constraint of the form `#[account(address = data.payload.recipient)]` that would enforce `self.recipient.key() == data.payload.recipient`. The `#[instruction(data: SignedPayload<FinalizeTransferPayload>)]` attribute makes the payload available for such a constraint, but it is not used for the `recipient` field. [5](#0-4) 

The `FinalizeTransferResponse` sent back to NEAR does not include the actual recipient who received the funds — only `token`, `amount`, `fee_recipient`, and `transfer_id` — so NEAR cannot detect the substitution:

```rust
let payload = FinalizeTransferResponse {
    token: Pubkey::default(),
    amount: data.amount,
    fee_recipient: data.fee_recipient.unwrap_or_default(),
    transfer_id: data.transfer_id,
}.serialize_for_near(())?;
``` [6](#0-5) 

### Impact Explanation
An attacker who obtains a valid signed `FinalizeTransferPayload` (e.g., by observing the Wormhole VAA or being the relayer) can call `finalize_transfer` or `finalize_transfer_sol` with their own address as `recipient`. The nonce is consumed, the intended recipient receives nothing, and the attacker receives the full bridged amount. This constitutes **direct theft of bridged assets** on Solana — a Critical impact.

### Likelihood Explanation
The `finalize_transfer` instruction is callable by any unprivileged account (no role check). Wormhole VAAs are publicly observable on-chain. A front-running attacker or a malicious relayer can trivially substitute the `recipient` account. Likelihood is **High**.

### Recommendation
Add an address constraint on the `recipient` account in both `FinalizeTransfer` and `FinalizeTransferSol` that enforces equality with the recipient encoded in the signed payload:

```rust
/// CHECK: validated against payload recipient
#[account(address = data.payload.recipient @ ErrorCode::InvalidRecipient)]
pub recipient: UncheckedAccount<'info>,
```

Additionally, include the actual recipient in `FinalizeTransferResponse` so the NEAR side can independently verify delivery.

### Proof of Concept
1. Alice initiates a bridge transfer from NEAR specifying her Solana address as recipient.
2. NEAR's MPC signs a `FinalizeTransferPayload` (with `destination_nonce = N`, `amount = X`).
3. The signed VAA is published on Wormhole and becomes publicly visible.
4. Attacker Bob calls `finalize_transfer_sol` (or `finalize_transfer`) with the valid VAA but passes his own Solana address as `recipient`.
5. The nonce `N` is marked used; `X` lamports (or SPL tokens) are transferred to Bob's account.
6. Alice's transfer is permanently consumed with zero delivery to her. The `FinalizeTransferResponse` sent back to NEAR contains no recipient field, so NEAR marks the transfer complete without detecting the theft. [7](#0-6) [5](#0-4)

### Citations

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs (L23-87)
```rust
#[derive(Accounts)]
#[instruction(data: SignedPayload<FinalizeTransferPayload>)]
pub struct FinalizeTransfer<'info> {
    #[account(
        mut,
        seeds = [CONFIG_SEED],
        bump = config.bumps.config,
    )]
    pub config: Box<Account<'info, Config>>,
    #[account(
        init_if_needed,
        space = usize::try_from(USED_NONCES_ACCOUNT_SIZE).unwrap(),
        payer = common.payer,
        seeds = [
            USED_NONCES_SEED,
            &(data.payload.destination_nonce / u64::from(USED_NONCES_PER_ACCOUNT)).to_le_bytes(),
        ],
        bump,
    )]
    pub used_nonces: AccountLoader<'info, UsedNonces>,
    #[account(
        mut,
        seeds = [AUTHORITY_SEED],
        bump = config.bumps.authority,
    )]
    pub authority: SystemAccount<'info>,

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

    pub common: WormholeCPI<'info>,

    pub associated_token_program: Program<'info, AssociatedToken>,
    pub system_program: Program<'info, System>,
    pub token_program: Interface<'info, TokenInterface>,
}
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

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer_sol.rs (L25-65)
```rust
#[derive(Accounts)]
#[instruction(data: SignedPayload<FinalizeTransferPayload>)]
pub struct FinalizeTransferSol<'info> {
    #[account(
        mut,
        seeds = [CONFIG_SEED],
        bump = config.bumps.config,
    )]
    pub config: Box<Account<'info, Config>>,
    #[account(
        init_if_needed,
        space = usize::try_from(USED_NONCES_ACCOUNT_SIZE).unwrap(),
        payer = common.payer,
        seeds = [
            USED_NONCES_SEED,
            &(data.payload.destination_nonce / u64::from(USED_NONCES_PER_ACCOUNT)).to_le_bytes(),
        ],
        bump,
    )]
    pub used_nonces: AccountLoader<'info, UsedNonces>,
    #[account(
        mut,
        seeds = [AUTHORITY_SEED],
        bump = config.bumps.authority,
    )]
    pub authority: SystemAccount<'info>,

    /// CHECK: this can be any type of account
    #[account(mut)]
    pub recipient: UncheckedAccount<'info>,

    #[account(
        mut,
        seeds = [SOL_VAULT_SEED],
        bump = config.bumps.sol_vault,
    )]
    pub sol_vault: SystemAccount<'info>,

    pub common: WormholeCPI<'info>,
    pub system_program: Program<'info, System>,
}
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

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer_sol.rs (L91-97)
```rust
        let payload = FinalizeTransferResponse {
            token: Pubkey::default(),
            amount: data.amount,
            fee_recipient: data.fee_recipient.unwrap_or_default(),
            transfer_id: data.transfer_id,
        }
        .serialize_for_near(())?;
```
