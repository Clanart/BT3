### Title
`deploy_token` on Solana Cannot Be Paused — Missing Pause Flag and Check - (`solana/programs/bridge_token_factory/src/lib.rs`)

### Summary

The Solana `bridge_token_factory` program defines pause flags and enforces them on `init_transfer`, `init_transfer_sol`, `finalize_transfer`, and `finalize_transfer_sol`, but the `deploy_token` instruction has no pause check and no corresponding pause flag exists. Even when the bridge is "fully paused" via `pause()`, token deployment on Solana remains callable by any submitter with a valid MPC signature.

### Finding Description

In `constants.rs`, only two pause flags are defined: [1](#0-0) 

`ALL_PAUSED = INIT_TRANSFER_PAUSED | FINALIZE_TRANSFER_PAUSED`. There is no `DEPLOY_TOKEN_PAUSED` flag. The `pause()` instruction sets `config.paused = ALL_PAUSED`: [2](#0-1) 

The four transfer instructions correctly gate on the pause state, e.g.: [3](#0-2) 

But `deploy_token` has no such guard: [4](#0-3) 

By contrast, the EVM `OmniBridge.deployToken` carries `whenNotPaused(PAUSED_DEPLOY_TOKEN)`: [5](#0-4) 

And the StarkNet `omni_bridge.cairo` `deploy_token` asserts `!_is_paused(@self, PAUSE_DEPLOY_TOKEN)`: [6](#0-5) 

The Solana implementation is the only chain where `deploy_token` is unguarded and where `ALL_PAUSED` does not cover token deployment at all.

### Impact Explanation

When an operator pauses the Solana bridge in response to an emergency (e.g., a compromised MPC key or a discovered vulnerability in the token-deployment flow), `deploy_token` continues to accept calls from any submitter holding a valid (or attacker-forged) MPC signature. This means:

- A compromised MPC key can still be used to deploy arbitrary wrapped token mints on Solana even after the bridge is paused.
- Once the bridge is unpaused (or if `finalize_transfer` is re-enabled), those attacker-deployed mints can be used to mint unbacked tokens to arbitrary recipients.
- The emergency pause — the primary on-chain defense against active exploits — is silently ineffective for the token-deployment path on Solana, inconsistent with EVM and StarkNet behavior.

This maps to: **High — Proof/MPC verification bypass enabling unauthorized token deployment**, because the missing pause allows token deployment to proceed even when the operator has explicitly attempted to halt all bridge operations.

### Likelihood Explanation

Any proof/message submitter who can produce a valid MPC-signed `DeployTokenPayload` (or who has access to a compromised MPC key) can call `deploy_token` on Solana regardless of pause state. The entry point is fully public and requires only a valid signature — the same condition that would trigger an emergency pause in the first place.

### Recommendation

1. Add a `DEPLOY_TOKEN_PAUSED: u8 = 1 << 2` constant to `constants.rs`.
2. Update `ALL_PAUSED` to include it: `ALL_PAUSED = INIT_TRANSFER_PAUSED | FINALIZE_TRANSFER_PAUSED | DEPLOY_TOKEN_PAUSED`.
3. Add a pause guard to `deploy_token` in `lib.rs`:

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

### Proof of Concept

1. Admin detects a compromised MPC key and calls `pause()` on the Solana bridge, setting `config.paused = ALL_PAUSED (0x03)`.
2. Attacker (holding the compromised key) constructs a `DeployTokenPayload` for a new token and signs it with the compromised key.
3. Attacker calls `deploy_token` on Solana. The instruction has no `require!(config.paused & DEPLOY_TOKEN_PAUSED == 0, ...)` check, so it proceeds.
4. A new SPL mint is created on Solana and a Wormhole message is posted back to NEAR, registering the attacker-controlled token.
5. Once the bridge is unpaused (or via a separate exploit of `finalize_transfer`), the attacker can mint unbacked tokens to arbitrary recipients using the newly registered mint. [4](#0-3) [1](#0-0)

### Citations

**File:** solana/programs/bridge_token_factory/src/constants.rs (L36-42)
```rust
pub const INIT_TRANSFER_PAUSED: u8 = 1 << 0;

#[constant]
pub const FINALIZE_TRANSFER_PAUSED: u8 = 1 << 1;

#[constant]
pub const ALL_PAUSED: u8 = INIT_TRANSFER_PAUSED | FINALIZE_TRANSFER_PAUSED;
```

**File:** solana/programs/bridge_token_factory/src/instructions/admin/pause.rs (L25-30)
```rust
impl Pause<'_> {
    pub fn process(&mut self) -> Result<()> {
        self.config.paused = ALL_PAUSED;

        Ok(())
    }
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

**File:** solana/programs/bridge_token_factory/src/lib.rs (L78-95)
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
