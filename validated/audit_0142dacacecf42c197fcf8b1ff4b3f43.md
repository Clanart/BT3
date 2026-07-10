### Title
Single-Step Admin Transfer Permanently Locks All Critical Bridge Admin Functions - (`solana/programs/bridge_token_factory/src/instructions/admin/change_config.rs`)

---

### Summary

The Solana `bridge_token_factory` program implements a single-step admin transfer via `set_admin`. If the current admin passes an incorrect or inaccessible public key, the `config.admin` field is immediately overwritten with no recovery path. All admin-gated functions — including unpausing the bridge, rotating the MPC verification address, and updating sub-admins — become permanently inaccessible, freezing bridge operations and locking user funds.

---

### Finding Description

`ChangeConfig::set_admin` directly overwrites `config.admin` in a single transaction with no two-step confirmation: [1](#0-0) 

The `ChangeConfig` account constraint enforces that only the current admin can call this: [2](#0-1) 

Once `config.admin` is set to a wrong key, no further `ChangeConfig` instruction can succeed because the constraint `signer.key() == config.admin` will always fail for any legitimate signer. There is no `pending_admin` pattern, no timeout, and no recovery mechanism.

All of the following instructions are gated exclusively through `ChangeConfig` (requiring `config.admin`):

- `set_admin` — re-transfer admin
- `set_pausable_admin` — update pause controller
- `set_metadata_admin` — update metadata controller
- `set_derived_near_bridge_address` — update the MPC/NEAR bridge address used for signature verification
- `unpause` — unpause the bridge [3](#0-2) 

---

### Impact Explanation

If `config.admin` is set to an inaccessible key:

1. **Bridge cannot be unpaused.** If the bridge is paused (e.g., by `pausable_admin` in an emergency), only `admin` can unpause via `unpause`. With admin lost, the bridge is permanently paused and all user funds locked in the SOL vault and token vaults become irrecoverable.

2. **`derived_near_bridge_address` cannot be rotated.** This field is the MPC-derived public key used to verify signatures on `finalize_transfer` and `finalize_transfer_sol`. If the MPC key is ever rotated (a normal operational event), the bridge cannot be updated and all future cross-chain settlements are permanently broken. [4](#0-3) 

The SOL vault holds native SOL for bridged SOL transfers; the token vaults hold native SPL tokens. Both become permanently frozen. [5](#0-4) 

---

### Likelihood Explanation

Admin key rotation is a routine operational event. A single typo or copy-paste error in a 32-byte Solana public key during a `set_admin` call is sufficient to trigger this. Unlike EVM addresses (20 bytes, checksummed), Solana `Pubkey` values are base58-encoded 32-byte arrays with no checksum enforcement at the program level. The error is silent and irreversible.

---

### Recommendation

Implement a two-step admin transfer pattern:

1. Add a `pending_admin: Option<Pubkey>` field to the `Config` struct.
2. `set_admin` sets `pending_admin` only; `config.admin` is unchanged.
3. Add an `accept_admin` instruction that requires the signer to match `pending_admin`, then promotes it to `config.admin`.

This mirrors the `Ownable2StepUpgradeable` pattern already correctly used in the EVM `BridgeToken`: [6](#0-5) 

---

### Proof of Concept

1. Current admin calls `set_admin(ctx, wrong_pubkey)` — e.g., a mistyped key or a key whose private key is unknown.
2. `config.admin` is immediately set to `wrong_pubkey`.
3. Any subsequent call to `set_admin`, `unpause`, `set_derived_near_bridge_address`, etc. requires `signer.key() == config.admin` — which now equals `wrong_pubkey`.
4. No legitimate signer can satisfy this constraint.
5. If the bridge is subsequently paused by `pausable_admin`, `unpause` is unreachable.
6. All SOL and SPL tokens held in `sol_vault` and token vaults are permanently frozen. [7](#0-6)

### Citations

**File:** solana/programs/bridge_token_factory/src/instructions/admin/change_config.rs (L14-18)
```rust
    #[account(
        mut,
        constraint = signer.key() == config.admin @ crate::error::ErrorCode::Unauthorized,
    )]
    pub signer: Signer<'info>,
```

**File:** solana/programs/bridge_token_factory/src/instructions/admin/change_config.rs (L21-26)
```rust
impl ChangeConfig<'_> {
    pub fn set_admin(&mut self, admin: Pubkey) -> Result<()> {
        self.config.admin = admin;

        Ok(())
    }
```

**File:** solana/programs/bridge_token_factory/src/lib.rs (L88-113)
```rust
        data.verify_signature(
            (ctx.accounts.mint.key(), ctx.accounts.recipient.key()),
            &ctx.accounts.common.config.derived_near_bridge_address,
        )?;
        ctx.accounts.process(data.payload)?;

        Ok(())
    }

    pub fn finalize_transfer_sol(
        ctx: Context<FinalizeTransferSol>,
        data: SignedPayload<FinalizeTransferPayload>,
    ) -> Result<()> {
        require!(
            ctx.accounts.common.config.paused & FINALIZE_TRANSFER_PAUSED == 0,
            error::ErrorCode::Paused
        );
        msg!("Finalizing transfer");

        data.verify_signature(
            (Pubkey::default(), ctx.accounts.recipient.key()),
            &ctx.accounts.config.derived_near_bridge_address,
        )?;
        ctx.accounts.process(data.payload)?;

        Ok(())
```

**File:** solana/programs/bridge_token_factory/src/lib.rs (L159-201)
```rust
    pub fn unpause(ctx: Context<ChangeConfig>, paused: u8) -> Result<()> {
        msg!("Unpausing");

        ctx.accounts.set_paused(paused)?;

        Ok(())
    }

    pub fn set_admin(ctx: Context<ChangeConfig>, admin: Pubkey) -> Result<()> {
        msg!("Setting admin");

        ctx.accounts.set_admin(admin)?;

        Ok(())
    }

    pub fn set_pausable_admin(ctx: Context<ChangeConfig>, pausable_admin: Pubkey) -> Result<()> {
        msg!("Setting pausable admin");

        ctx.accounts.set_pausable_admin(pausable_admin)?;

        Ok(())
    }

    pub fn set_metadata_admin(ctx: Context<ChangeConfig>, metadata_admin: Pubkey) -> Result<()> {
        msg!("Setting metadata admin");

        ctx.accounts.set_metadata_admin(metadata_admin)?;

        Ok(())
    }

    pub fn set_derived_near_bridge_address(
        ctx: Context<ChangeConfig>,
        derived_near_bridge_address: [u8; 64],
    ) -> Result<()> {
        msg!("Setting derived NEAR bridge address");

        ctx.accounts
            .set_derived_near_bridge_address(derived_near_bridge_address)?;

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

**File:** evm/src/omni-bridge/contracts/BridgeToken.sol (L4-14)
```text
import {ERC20Upgradeable} from "@openzeppelin/contracts-upgradeable/token/ERC20/ERC20Upgradeable.sol";
import {Ownable2StepUpgradeable} from "@openzeppelin/contracts-upgradeable/access/Ownable2StepUpgradeable.sol";
import {Initializable} from "@openzeppelin/contracts-upgradeable/proxy/utils/Initializable.sol";
import {UUPSUpgradeable} from "@openzeppelin/contracts-upgradeable/proxy/utils/UUPSUpgradeable.sol";
import {IBridgeToken} from "../../common/IBridgeToken.sol";

contract BridgeToken is
    Initializable,
    UUPSUpgradeable,
    ERC20Upgradeable,
    Ownable2StepUpgradeable,
```

**File:** solana/programs/bridge_token_factory/src/state/config.rs (L18-29)
```rust
#[account]
#[derive(InitSpace)]
pub struct Config {
    pub admin: Pubkey,
    pub max_used_nonce: u64,
    pub derived_near_bridge_address: [u8; 64],
    pub bumps: ConfigBumps,
    pub paused: u8,
    pub pausable_admin: Pubkey,
    pub metadata_admin: Pubkey,
    pub padding: [u8; 35],
}
```
