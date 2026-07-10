### Title
Missing Recipient Verification in `finalize_transfer` / `finalize_transfer_sol` Allows Attacker to Redirect Bridged Assets — (`solana/programs/bridge_token_factory/src/instructions/user/finalize_transfer.rs`, `finalize_transfer_sol.rs`)

---

### Summary

The `FinalizeTransfer` and `FinalizeTransferSol` Solana instructions transfer tokens (or SOL) to a caller-supplied `recipient` account that is never verified against the intended recipient encoded in the signed cross-chain payload. Because these instructions are permissionless, any attacker can call them with their own address as `recipient` and steal the bridged funds.

---

### Finding Description

In `finalize_transfer.rs`, the accounts struct declares `recipient` as a bare `UncheckedAccount` with only the comment `/// CHECK: this can be any type of account`: [1](#0-0) 

The `token_account` is then derived as the Associated Token Account (ATA) of that unchecked `recipient`: [2](#0-1) 

In `process()`, tokens are minted or transferred directly into `self.token_account` (i.e., the ATA of the attacker-supplied `recipient`): [3](#0-2) 

At no point is `self.recipient.key()` compared against any field in `data` (the Wormhole-verified `FinalizeTransferPayload`). The payload's intended recipient is completely ignored.

The same pattern exists in `finalize_transfer_sol.rs`, where `recipient` is `UncheckedAccount` with `#[account(mut)]` and SOL is transferred directly to it from the vault: [4](#0-3) [5](#0-4) 

---

### Impact Explanation

**Critical — Direct theft of native or bridged assets.**

An attacker intercepts any pending cross-chain transfer finalization on Solana. They call `finalize_transfer` (or `finalize_transfer_sol`) with their own wallet as `recipient`. The program:
1. Marks the nonce as used (preventing the legitimate recipient from ever claiming).
2. Mints/transfers the full token amount (or SOL) to the attacker's ATA (or wallet).

The legitimate recipient receives nothing and cannot retry because the nonce is consumed.

---

### Likelihood Explanation

**High.** `finalize_transfer` is a permissionless instruction — any account can be the `payer`/caller. Cross-chain transfers are publicly observable on-chain and via Wormhole VAAs. An attacker only needs to front-run or race the legitimate relayer, which is straightforward on Solana given public mempool visibility and the ability to submit transactions directly.

---

### Recommendation

Add a constraint in the `FinalizeTransfer` and `FinalizeTransferSol` accounts structs that enforces `recipient.key() == data.payload.recipient` (where `recipient` is the Solana pubkey encoded in the signed `FinalizeTransferPayload`). For example:

```rust
/// CHECK: verified against payload recipient
#[account(
    constraint = recipient.key() == data.payload.recipient @ ErrorCode::InvalidRecipient
)]
pub recipient: UncheckedAccount<'info>,
```

This mirrors the fix in the referenced report: binding the account to the value committed in the signed/verified message, so no caller-supplied substitution is possible.

---

### Proof of Concept

1. Alice initiates a bridge transfer from NEAR to Solana for 1000 USDC, with her Solana address as recipient. A Wormhole VAA is produced containing the signed `FinalizeTransferPayload` (nonce, amount, Alice's pubkey as recipient).
2. Attacker Bob observes the VAA before any relayer submits it.
3. Bob calls `finalize_transfer` with `recipient = Bob's pubkey`. The `token_account` constraint resolves to Bob's ATA (created if needed, paid by Bob as `payer`).
4. The program verifies the Wormhole signature on the payload (valid), marks the nonce used, and mints/transfers 1000 USDC to Bob's ATA.
5. Alice's nonce is now consumed. She receives nothing and has no recourse.

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
