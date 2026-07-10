Based on my analysis of the production Solana bridge code, here is my assessment:

---

### Title
`native_fee` Permanently Locked in `sol_vault` After `init_transfer_sol` / `finalize_transfer_sol` Cycle — (`solana/programs/bridge_token_factory/src/instructions/user/init_transfer_sol.rs`, `finalize_transfer_sol.rs`)

### Summary

`init_transfer_sol` deposits `amount + native_fee` into `sol_vault`, but `finalize_transfer_sol` only releases `data.amount` to the recipient. The `native_fee` portion is never released to any party and no admin withdrawal path for `sol_vault` exists in the program, causing user-paid `native_fee` lamports to be permanently locked.

### Finding Description

**Deposit path — `init_transfer_sol`:**

`InitTransferSol::process` enforces `fee == 0` but allows `native_fee > 0`, then transfers the full `native_fee + amount` into `sol_vault`: [1](#0-0) 

The `native_fee` is also serialized into the Wormhole message sent to NEAR: [2](#0-1) 

**Release path — `finalize_transfer_sol`:**

`FinalizeTransferSol::process` transfers only `data.amount` from `sol_vault` to `recipient`. There is no transfer of `native_fee` to any relayer, fee collector, or admin: [3](#0-2) 

The `FinalizeTransferPayload` that NEAR MPC signs contains only `amount`, `destination_nonce`, `transfer_id`, and `fee_recipient` (a NEAR-side account string). There is no `native_fee` field in the finalize payload, so NEAR signs `amount = A` (the transfer amount), not `A + native_fee`: [4](#0-3) 

**No admin withdrawal path:**

The entire admin instruction set (`change_config`, `initialize`, `pause`, `update_metadata`) contains no instruction that can withdraw lamports from `sol_vault`. The `sol_vault` PDA is only touched by `init_transfer_sol` (deposit) and `finalize_transfer_sol` (partial release): [5](#0-4) 

### Impact Explanation

Every `init_transfer_sol` call with `native_fee > 0` permanently locks `native_fee` lamports in `sol_vault`. After finalization, `sol_vault` retains `native_fee` with no mechanism to release it. This is a direct, irrecoverable loss of user funds. Over time, `sol_vault` accumulates lamports that back no outstanding liability, inflating apparent collateral while the corresponding user value is destroyed. This matches **Critical — Permanent freezing / irrecoverable lock of user funds in bridge vault flows**.

### Likelihood Explanation

Any unprivileged user can trigger this by calling `init_transfer_sol` with `native_fee > 0`. The `native_fee` field is a public, user-controlled input with no upper bound enforced. The path is fully reachable without any privileged access, leaked keys, or external dependency compromise.

### Recommendation

One of the following fixes is needed:

1. **Pay the relayer from `sol_vault`:** In `finalize_transfer_sol`, transfer `native_fee` (embedded in the signed payload or tracked on-chain) to `common.payer` (the relayer) in addition to releasing `data.amount` to `recipient`.
2. **Reject non-zero `native_fee` for SOL transfers:** Add `require!(payload.native_fee == 0, ErrorCode::InvalidFee)` in `init_transfer_sol::process`, mirroring the existing `fee == 0` guard, until a proper relayer payment mechanism is implemented.
3. **Add an admin sweep instruction:** Allow the admin to withdraw accumulated `native_fee` from `sol_vault` to a designated fee treasury.

### Proof of Concept

```
1. Call init_transfer_sol(amount=1_000_000, native_fee=500_000)
   → sol_vault balance increases by 1_500_000 lamports

2. NEAR MPC observes the Wormhole message, signs FinalizeTransferPayload{amount=1_000_000}

3. Call finalize_transfer_sol(signed_payload{amount=1_000_000})
   → sol_vault releases 1_000_000 lamports to recipient
   → sol_vault retains 500_000 lamports (native_fee) permanently

4. Assert: sol_vault.lamports_delta_after_full_cycle == 500_000
5. Assert: no instruction in the program can withdraw those 500_000 lamports
```

### Citations

**File:** solana/programs/bridge_token_factory/src/instructions/user/init_transfer_sol.rs (L36-53)
```rust
        require!(payload.fee == 0, ErrorCode::InvalidFee);
        require!(payload.amount > 0, ErrorCode::InvalidArgs);

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

**File:** solana/programs/bridge_token_factory/src/state/message/init_transfer.rs (L35-36)
```rust
        // 6. native_fee
        u128::from(self.native_fee).serialize(&mut writer)?;
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

**File:** solana/programs/bridge_token_factory/src/state/message/finalize_transfer.rs (L11-16)
```rust
pub struct FinalizeTransferPayload {
    pub destination_nonce: u64,
    pub transfer_id: TransferId,
    pub amount: u128,
    pub fee_recipient: Option<String>,
}
```

**File:** solana/programs/bridge_token_factory/src/instructions/admin/change_config.rs (L1-54)
```rust
use anchor_lang::prelude::*;

use crate::{constants::CONFIG_SEED, state::config::Config};

#[derive(Accounts)]
pub struct ChangeConfig<'info> {
    #[account(
        mut,
        seeds = [CONFIG_SEED],
        bump = config.bumps.config,
    )]
    pub config: Box<Account<'info, Config>>,

    #[account(
        mut,
        constraint = signer.key() == config.admin @ crate::error::ErrorCode::Unauthorized,
    )]
    pub signer: Signer<'info>,
}

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
