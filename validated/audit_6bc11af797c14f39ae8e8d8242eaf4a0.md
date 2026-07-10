### Title
Native Fee SOL Permanently Stranded in `sol_vault` with No Disbursement Mechanism — (`solana/programs/bridge_token_factory/src/instructions/user/init_transfer.rs`)

---

### Summary

`init_transfer` (token bridge path) transfers `native_fee` SOL from the user into `sol_vault`. Neither `finalize_transfer` (the corresponding token-side completion) nor any admin instruction ever withdraws these accumulated fees from `sol_vault`. The fees are permanently locked.

---

### Finding Description

**Inflow — `init_transfer::process`**

When `payload.native_fee > 0`, the instruction unconditionally transfers that amount from the user to `sol_vault`: [1](#0-0) 

The token itself is then either locked in a per-mint vault or burned: [2](#0-1) 

**No outflow for token-path fees**

`finalize_transfer` (the Solana-side completion for token transfers) never touches `sol_vault` — it only mints or transfers SPL tokens: [3](#0-2) 

**`finalize_transfer_sol` only releases `data.amount`**

The only instruction that ever moves SOL *out* of `sol_vault` is `finalize_transfer_sol`, and it releases exactly `data.amount` — the bridged principal — with no provision for accumulated native fees from token transfers: [4](#0-3) 

**No admin withdrawal path**

`change_config` — the only admin instruction — only mutates config fields; it has no mechanism to drain `sol_vault`: [5](#0-4) 

**`init_transfer_sol` has the same issue for its `native_fee` portion**

`init_transfer_sol` deposits `native_fee + amount` into `sol_vault`: [6](#0-5) 

When the SOL is bridged back, `finalize_transfer_sol` releases only `data.amount`, leaving the `native_fee` portion permanently in `sol_vault` as well.

**`native_fee` is serialized and sent to NEAR but the SOL never leaves Solana**

The payload encodes `native_fee` for NEAR-side processing: [7](#0-6) 

NEAR receives the fee signal, but the corresponding SOL remains locked in `sol_vault` on Solana with no release path.

---

### Impact Explanation

Every call to `init_transfer` with `native_fee > 0` permanently increases `sol_vault`'s balance by `native_fee` lamports with no corresponding disbursement. After N such calls:

```
sol_vault.balance += N × native_fee   (no offsetting withdrawal)
```

This breaks the collateral accounting invariant:

```
sol_vault.balance ≠ Σ(pending SOL transfers)
```

The excess SOL (all accumulated native fees from token transfers) is irrecoverably stranded — no instruction in the program can withdraw it. This constitutes a permanent loss of user-paid fee funds and a corruption of the `sol_vault` accounting model.

---

### Likelihood Explanation

Any unprivileged user can trigger this by calling `init_transfer` with `native_fee > 0`. The `native_fee` field is a user-supplied parameter with no upper bound enforced on-chain. The condition is reachable on every token bridge transfer where the user opts to pay a native fee (which is the normal relayer-incentive flow). No special role or key is required.

---

### Recommendation

1. **Add a fee-withdrawal instruction** gated to an admin or designated fee-recipient account that can drain the accumulated native-fee balance from `sol_vault` (tracked separately from bridged SOL principal).
2. **Track fees separately** — maintain an on-chain counter of accumulated native fees so the invariant `sol_vault.balance = locked_sol_principal + accumulated_fees` is explicit and auditable.
3. **Alternatively**, route `native_fee` to a dedicated fee vault PDA distinct from `sol_vault`, so the SOL collateral accounting for bridged SOL is never polluted by fee accumulation.

---

### Proof of Concept

```
// Invariant test (pseudo-code)
let initial = sol_vault.lamports();
for _ in 0..N {
    init_transfer(amount=100_000_000, native_fee=50_000_000, ...);
    // tokens locked/burned; 50_000_000 lamports → sol_vault each call
}
assert_eq!(sol_vault.lamports(), initial + N * 50_000_000);
// No finalize_transfer_sol is ever called (these are token transfers)
// No admin instruction can drain the excess
// → N * 50_000_000 lamports permanently stranded
```

### Citations

**File:** solana/programs/bridge_token_factory/src/instructions/user/init_transfer.rs (L75-86)
```rust
        if payload.native_fee > 0 {
            transfer(
                CpiContext::new(
                    self.common.system_program.to_account_info(),
                    Transfer {
                        from: self.user.to_account_info(),
                        to: self.sol_vault.to_account_info(),
                    },
                ),
                payload.native_fee,
            )?;
        }
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/init_transfer.rs (L88-121)
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
        } else {
            // Bridged version. May be a fake token with our authority set but it will be ignored on the near side
            require!(
                self.mint.mint_authority.contains(self.authority.key),
                ErrorCode::InvalidBridgedToken
            );

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
        }
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

**File:** solana/programs/bridge_token_factory/src/instructions/admin/change_config.rs (L21-54)
```rust
impl ChangeConfig<'_> {
    pub fn set_admin(&mut self, admin: Pubkey) -> Result<()> {
        self.config.admin = admin;

        Ok(())
    }

    pub fn set_pausable_admin(&mut self, pausable_admin: Pubkey) -> Result<()> {
        self.config.pausable_admin = pausable_admin;

        Ok(())
    }

    pub fn set_paused(&mut self, paused: u8) -> Result<()> {
        self.config.paused = paused;

        Ok(())
    }

    pub fn set_metadata_admin(&mut self, metadata_admin: Pubkey) -> Result<()> {
        self.config.metadata_admin = metadata_admin;

        Ok(())
    }

    pub fn set_derived_near_bridge_address(
        &mut self,
        derived_near_bridge_address: [u8; 64],
    ) -> Result<()> {
        self.config.derived_near_bridge_address = derived_near_bridge_address;

        Ok(())
    }
}
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/init_transfer_sol.rs (L39-53)
```rust
        transfer(
            CpiContext::new(
                self.common.system_program.to_account_info(),
                Transfer {
                    from: self.user.to_account_info(),
                    to: self.sol_vault.to_account_info(),
                },
            ),
            payload
                .native_fee
                .checked_add(
                    payload.amount.try_into().map_err(|_| error!(ErrorCode::InvalidArgs))?,
                )
                .ok_or_else(|| error!(ErrorCode::InvalidArgs))?,
        )?;
```

**File:** solana/programs/bridge_token_factory/src/state/message/init_transfer.rs (L35-37)
```rust
        // 6. native_fee
        u128::from(self.native_fee).serialize(&mut writer)?;
        // 7. recipient
```
