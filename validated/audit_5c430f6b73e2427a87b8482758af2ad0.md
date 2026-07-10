Looking at the Solana bridge program's finalization instructions, I need to check whether the `recipient` account is constrained against the signed payload.

### Title
Unconstrained `recipient` Account in `FinalizeTransferSol` and `FinalizeTransfer` Allows Theft of Bridged Assets — (`solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer_sol.rs`, `finalize_transfer.rs`)

---

### Summary

In both `FinalizeTransferSol` and `FinalizeTransfer`, the `recipient` account passed by the caller is never validated against any field in the MPC-signed `FinalizeTransferPayload`. An unprivileged attacker can supply a valid signed payload (obtained from on-chain Wormhole VAA data) while substituting their own address as `recipient`, causing bridged SOL or tokens to be transferred to the attacker instead of the intended beneficiary. The nonce is then consumed, permanently blocking the legitimate recipient from claiming.

---

### Finding Description

In `FinalizeTransferSol`, the account struct declares `recipient` as a plain mutable unchecked account with no constraint tying it to the payload:

```rust
/// CHECK: this can be any type of account
#[account(mut)]
pub recipient: UncheckedAccount<'info>,
``` [1](#0-0) 

The `process` function then transfers SOL directly to `self.recipient` — the caller-supplied account — not to any recipient encoded in the signed payload:

```rust
transfer(
    CpiContext::new_with_signer(
        self.common.system_program.to_account_info(),
        Transfer {
            from: self.sol_vault.to_account_info(),
            to: self.recipient.to_account_info(),   // caller-controlled
        },
        &[&[SOL_VAULT_SEED, &[self.config.bumps.sol_vault]]],
    ),
    data.amount.try_into()...?,
)?;
``` [2](#0-1) 

Critically, the `FinalizeTransferPayload` fields accessed in `process` are only `destination_nonce`, `amount`, `fee_recipient`, and `transfer_id` — no `recipient` field is read from the payload or compared against the `recipient` account. The `#[instruction(data: SignedPayload<FinalizeTransferPayload>)]` attribute makes `data` available for account constraints, but no constraint of the form `constraint = recipient.key() == data.payload.recipient` exists. [3](#0-2) 

The same pattern exists in `FinalizeTransfer` for SPL tokens: `recipient` is unchecked, and `token_account` is the ATA of `recipient` (caller-supplied), so tokens are minted or transferred to the attacker's ATA:

```rust
/// CHECK: this can be any type of account
pub recipient: UncheckedAccount<'info>,
``` [4](#0-3) 

```rust
#[account(
    init_if_needed,
    ...
    associated_token::authority = recipient,   // attacker-controlled
    ...
)]
pub token_account: Box<InterfaceAccount<'info, TokenAccount>>,
``` [5](#0-4) 

The `process` function sends tokens to `self.token_account` (the ATA of the attacker-supplied `recipient`), not to any payload-encoded recipient: [6](#0-5) 

---

### Impact Explanation

**Critical — Direct theft of bridged native SOL and bridged SPL tokens.**

An attacker who front-runs or replays a valid Wormhole VAA containing a `FinalizeTransferPayload` can redirect the entire bridged amount to themselves. The `UsedNonces::use_nonce` call then marks the nonce as consumed, permanently preventing the legitimate recipient from ever claiming their funds. This breaks bridge collateralization and constitutes direct, irrecoverable theft of user assets. [7](#0-6) 

---

### Likelihood Explanation

**High.** No special privileges are required. Wormhole VAAs are publicly observable on-chain. Any actor monitoring the Wormhole guardian network can extract a valid signed `FinalizeTransferPayload` and submit `finalize_transfer_sol` or `finalize_transfer` before the legitimate relayer, substituting their own address as `recipient`. Solana's fast block times make front-running straightforward. The attack requires no leaked keys, no colluding operators, and no chain-level assumptions.

---

### Recommendation

Add an explicit account constraint in both `FinalizeTransferSol` and `FinalizeTransfer` that enforces the `recipient` account matches the recipient encoded in the signed payload. For example:

```rust
/// CHECK: verified against signed payload
#[account(
    mut,
    constraint = recipient.key() == data.payload.recipient @ ErrorCode::InvalidRecipient
)]
pub recipient: UncheckedAccount<'info>,
```

The `FinalizeTransferPayload` must include a `recipient: Pubkey` field that the MPC signs over, so the signature cryptographically commits to the intended beneficiary. This mirrors the fix recommended in the original report: ensure all critical output parameters are covered by the authorization check.

---

### Proof of Concept

1. Alice initiates a NEAR → Solana bridge transfer of 100 SOL, specifying her Solana address as recipient.
2. The NEAR MPC signs a `FinalizeTransferPayload` containing `destination_nonce=N`, `amount=100_SOL`, `fee_recipient=...`, `transfer_id=...` (no `recipient` field).
3. The signed VAA is published on Wormhole and becomes publicly visible.
4. Attacker Bob calls `finalize_transfer_sol` with the valid signed VAA but passes his own address as the `recipient` account.
5. `UsedNonces::use_nonce(N, ...)` succeeds and marks nonce `N` as consumed. [8](#0-7) 
6. `transfer(sol_vault → Bob, 100_SOL)` executes successfully. [2](#0-1) 
7. Alice attempts to call `finalize_transfer_sol` with the same VAA; it reverts with `NonceAlreadyUsed`. Her 100 SOL is permanently stolen.

### Citations

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer_sol.rs (L26-65)
```rust
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

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer_sol.rs (L68-102)
```rust
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

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs (L50-51)
```rust
    /// CHECK: this can be any type of account
    pub recipient: UncheckedAccount<'info>,
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs (L73-81)
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
