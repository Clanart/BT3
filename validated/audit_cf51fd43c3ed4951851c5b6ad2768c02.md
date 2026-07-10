### Title
`deploy_token` Callable When Bridge Is Fully Paused on Solana — (File: `solana/programs/bridge_token_factory/src/lib.rs`)

---

### Summary

The Solana `bridge_token_factory` program's `deploy_token` instruction has no pause-flag check, allowing token deployments to proceed even when the bridge is fully paused via `pause()`. This is inconsistent with every other chain's implementation and with the other guarded instructions in the same program.

---

### Finding Description

The Solana program defines two pause-flag constants and imports them only where they are used:

```rust
use super::constants::{FINALIZE_TRANSFER_PAUSED, INIT_TRANSFER_PAUSED};
``` [1](#0-0) 

Every sensitive instruction that moves value or finalises state correctly gates on these flags:

- `finalize_transfer` — checks `FINALIZE_TRANSFER_PAUSED`
- `finalize_transfer_sol` — checks `FINALIZE_TRANSFER_PAUSED`
- `init_transfer` — checks `INIT_TRANSFER_PAUSED`
- `init_transfer_sol` — checks `INIT_TRANSFER_PAUSED` [2](#0-1) 

However, `deploy_token` has **no pause check at all**:

```rust
pub fn deploy_token(
    ctx: Context<DeployToken>,
    data: SignedPayload<DeployTokenPayload>,
) -> Result<()> {
    msg!("Deploying token");
    data.verify_signature((), &ctx.accounts.common.config.derived_near_bridge_address)?;
    ctx.accounts.initialize_token_metadata(data.payload)?;
    Ok(())
}
``` [3](#0-2) 

When `pause()` is called it sets `ALL_PAUSED`, which is intended to halt all bridge operations: [4](#0-3) 

Yet `deploy_token` remains callable regardless of the pause state. There is no `DEPLOY_TOKEN_PAUSED` constant defined or imported anywhere in the program.

**Cross-chain comparison confirms this is an omission, not a design choice:**

- EVM `OmniBridge.deployToken` is guarded by `whenNotPaused(PAUSED_DEPLOY_TOKEN)`. [5](#0-4) 

- Starknet `deploy_token` asserts `!_is_paused(@self, PAUSE_DEPLOY_TOKEN)`. [6](#0-5) 

- NEAR `deploy_token` carries `#[pause(except(roles(Role::DAO)))]`. [7](#0-6) 

Only the Solana program is missing this guard.

---

### Impact Explanation

When an admin triggers `pause()` to halt the Solana bridge during a security incident, `deploy_token` continues to accept calls. A relayer holding a valid, previously-obtained `SignedPayload<DeployTokenPayload>` (signed by the NEAR MPC key before the pause) can still execute token deployments on Solana. This:

1. **Bypasses the emergency stop** — the admin's intent to freeze all bridge state changes is not honoured for token deployment.
2. **Creates persistent on-chain token mappings** — a newly deployed SPL token mint is registered in the program's state and will be used by future `finalize_transfer` calls once the bridge is unpaused, potentially directing minted tokens to an unintended mint address.
3. **Enables unauthorized token deployment** — matching the allowed High impact: *"Proof, signature, MPC, Wormhole, or light-client verification bypass enabling unauthorized transfer finalization, **token deployment**, or message execution."*

---

### Likelihood Explanation

Any relayer that obtained a valid `DeployTokenPayload` signature before the pause can submit it during the pause window. Relayers routinely hold signed payloads in flight; this is a normal operational state. No key compromise or privileged access is required beyond possessing a legitimately signed payload.

---

### Recommendation

Add a `DEPLOY_TOKEN_PAUSED` constant (e.g., `pub const DEPLOY_TOKEN_PAUSED: u8 = 1 << 2;`) and insert the same guard pattern used by the other instructions:

```rust
pub fn deploy_token(
    ctx: Context<DeployToken>,
    data: SignedPayload<DeployTokenPayload>,
) -> Result<()> {
    require!(
        ctx.accounts.common.config.paused & DEPLOY_TOKEN_PAUSED == 0,
        error::ErrorCode::Paused
    );
    msg!("Deploying token");
    data.verify_signature((), &ctx.accounts.common.config.derived_near_bridge_address)?;
    ctx.accounts.initialize_token_metadata(data.payload)?;
    Ok(())
}
```

This aligns the Solana program with the EVM, Starknet, and NEAR implementations.

---

### Proof of Concept

1. Admin calls `pause()` on the Solana `bridge_token_factory` — `config.paused` is set to `ALL_PAUSED`.
2. A relayer holds a valid `SignedPayload<DeployTokenPayload>` obtained before the pause.
3. Relayer submits `deploy_token` with that payload.
4. The instruction executes successfully — no pause check exists to reject it.
5. A new SPL token mint is registered in the program state, bypassing the emergency stop.
6. Contrast: calling `init_transfer` or `finalize_transfer` in the same paused state returns `ErrorCode::Paused` and reverts. [3](#0-2) [8](#0-7) [9](#0-8)

### Citations

**File:** solana/programs/bridge_token_factory/src/lib.rs (L29-29)
```rust
    use super::constants::{FINALIZE_TRANSFER_PAUSED, INIT_TRANSFER_PAUSED};
```

**File:** solana/programs/bridge_token_factory/src/lib.rs (L66-76)
```rust
    pub fn deploy_token(
        ctx: Context<DeployToken>,
        data: SignedPayload<DeployTokenPayload>,
    ) -> Result<()> {
        msg!("Deploying token");

        data.verify_signature((), &ctx.accounts.common.config.derived_near_bridge_address)?;
        ctx.accounts.initialize_token_metadata(data.payload)?;

        Ok(())
    }
```

**File:** solana/programs/bridge_token_factory/src/lib.rs (L78-148)
```rust
    pub fn finalize_transfer(
        ctx: Context<FinalizeTransfer>,
        data: SignedPayload<FinalizeTransferPayload>,
    ) -> Result<()> {
        require!(
            ctx.accounts.common.config.paused & FINALIZE_TRANSFER_PAUSED == 0,
            error::ErrorCode::Paused
        );
        msg!("Finalizing transfer");

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
    }

    pub fn log_metadata(ctx: Context<LogMetadata>) -> Result<()> {
        msg!("Logging metadata");

        ctx.accounts.process()?;

        Ok(())
    }

    pub fn init_transfer(ctx: Context<InitTransfer>, payload: InitTransferPayload) -> Result<()> {
        require!(
            ctx.accounts.common.config.paused & INIT_TRANSFER_PAUSED == 0,
            error::ErrorCode::Paused
        );
        msg!("Initializing transfer");

        ctx.accounts.process(&payload)?;

        Ok(())
    }

    pub fn init_transfer_sol(
        ctx: Context<InitTransferSol>,
        payload: InitTransferPayload,
    ) -> Result<()> {
        require!(
            ctx.accounts.common.config.paused & INIT_TRANSFER_PAUSED == 0,
            error::ErrorCode::Paused
        );
        msg!("Initializing transfer");

        ctx.accounts.process(&payload)?;

        Ok(())
```

**File:** solana/programs/bridge_token_factory/src/lib.rs (L151-157)
```rust
    pub fn pause(ctx: Context<Pause>) -> Result<()> {
        msg!("Pausing");

        ctx.accounts.process()?;

        Ok(())
    }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L135-138)
```text
    function deployToken(
        bytes calldata signatureData,
        BridgeTypes.MetadataPayload calldata metadata
    ) external payable whenNotPaused(PAUSED_DEPLOY_TOKEN) returns (address) {
```

**File:** starknet/src/omni_bridge.cairo (L202-203)
```text
        fn deploy_token(ref self: ContractState, signature: Signature, payload: MetadataPayload) {
            assert(!_is_paused(@self, PAUSE_DEPLOY_TOKEN), 'ERR_DEPLOY_TOKEN_PAUSED');
```

**File:** near/omni-bridge/src/lib.rs (L1136-1138)
```rust
    #[payable]
    #[pause(except(roles(Role::DAO)))]
    pub fn deploy_token(&mut self, #[serializer(borsh)] args: DeployTokenArgs) -> Promise {
```
