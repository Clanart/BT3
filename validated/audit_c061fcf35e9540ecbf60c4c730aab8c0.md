### Title
`deploy_token` Bypasses Pause Mechanism on Solana — (`solana/programs/bridge_token_factory/src/lib.rs`)

### Summary
The Solana `bridge_token_factory` program defines a pause mechanism with two flags (`INIT_TRANSFER_PAUSED`, `FINALIZE_TRANSFER_PAUSED`) and enforces them on `init_transfer`, `init_transfer_sol`, `finalize_transfer`, and `finalize_transfer_sol`. However, `deploy_token` has no pause check at all, and no `DEPLOY_TOKEN_PAUSED` bit exists in the constants. The `ALL_PAUSED` sentinel therefore does not cover token deployment. This is directly analogous to the reported pattern: a pause/lock exists in the system but is not enforced in one of the critical execution paths.

### Finding Description

In `solana/programs/bridge_token_factory/src/constants.rs`, the only pause bits defined are:

```rust
pub const INIT_TRANSFER_PAUSED: u8 = 1 << 0;
pub const FINALIZE_TRANSFER_PAUSED: u8 = 1 << 1;
pub const ALL_PAUSED: u8 = INIT_TRANSFER_PAUSED | FINALIZE_TRANSFER_PAUSED;
``` [1](#0-0) 

There is no `DEPLOY_TOKEN_PAUSED` bit. `ALL_PAUSED` is `0x03`, not `0x07`.

In `lib.rs`, `finalize_transfer` and `init_transfer` both gate on the pause flag:

```rust
require!(
    ctx.accounts.common.config.paused & FINALIZE_TRANSFER_PAUSED == 0,
    error::ErrorCode::Paused
);
``` [2](#0-1) 

But `deploy_token` has no such check:

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

By contrast, both the EVM and StarkNet deployments enforce a dedicated deploy-token pause flag:

- EVM: `deployToken` carries `whenNotPaused(PAUSED_DEPLOY_TOKEN)` [4](#0-3) 
- StarkNet: `deploy_token` asserts `!_is_paused(@self, PAUSE_DEPLOY_TOKEN)` [5](#0-4) 

The Solana program is the only chain-side component where `deploy_token` is entirely unaffected by any pause state.

### Impact Explanation

When the bridge operator pauses the Solana program in response to a security incident (e.g., a discovered vulnerability in token-mapping logic, a compromised relayer, or an ongoing exploit), calling `pause()` — which sets `ALL_PAUSED` — stops transfers but leaves `deploy_token` fully open. Any caller holding a valid MPC-signed `DeployTokenPayload` (obtained legitimately before the pause, or replayed from a pending transaction) can still execute `deploy_token` during the pause window.

Consequences:
1. **Token-mapping corruption**: A new SPL mint is registered in the bridge's token mapping during a period when the operator believed all bridge operations were halted. If the incident involves a flaw in the deployment path (e.g., decimal normalization, salt collision, or metadata injection), the operator has no on-chain lever to stop it on Solana.
2. **Broken emergency response**: The pause is the primary incident-response tool. Its incompleteness on Solana means the operator's mental model ("I paused everything") is wrong, potentially delaying or misdirecting the response while a bad token mapping is committed on-chain.
3. **Downstream accounting corruption**: A token deployed with incorrect metadata (wrong decimals, wrong NEAR token ID binding) during the pause window creates a persistent incorrect mapping that will misdirect value in all subsequent `finalize_transfer` calls for that token once the bridge is unpaused.

This maps to the allowed impact: **High — token-mapping or accounting corruption that breaks bridge collateralization or misdirects value**. [3](#0-2) 

### Likelihood Explanation

- The entry path is public: any caller can invoke `deploy_token` with a valid signed payload.
- Valid signed payloads are produced by the NEAR MPC for every legitimate token deployment request. A payload signed before the pause is still valid after it — there is no expiry or pause-awareness in the signature scheme.
- An operator pausing the bridge during an incident would reasonably expect `deploy_token` to be blocked, but it is not. The gap is invisible from the operator's perspective because `ALL_PAUSED` appears to cover everything.
- No privileged access beyond a valid MPC signature is required; obtaining one is a normal part of the bridge's token-onboarding flow.

### Recommendation

1. Add a `DEPLOY_TOKEN_PAUSED: u8 = 1 << 2` constant and update `ALL_PAUSED` to `INIT_TRANSFER_PAUSED | FINALIZE_TRANSFER_PAUSED | DEPLOY_TOKEN_PAUSED`.
2. Add the corresponding pause guard at the top of `deploy_token` in `lib.rs`, mirroring the pattern used for `finalize_transfer` and `init_transfer`.
3. Align the Solana pause constants with the EVM (`PAUSED_DEPLOY_TOKEN = 1 << 2`) and StarkNet (`PAUSE_DEPLOY_TOKEN = 0x04`) implementations for cross-chain consistency.

### Proof of Concept

1. Admin detects an exploit and calls `pause(ctx)` on the Solana program, setting `config.paused = ALL_PAUSED = 0x03`.
2. Attacker holds a valid `SignedPayload<DeployTokenPayload>` for token `"token.near"` (obtained from a prior legitimate NEAR-side request).
3. Attacker calls `deploy_token(ctx, signed_payload)`.
4. The function executes: `data.verify_signature(...)` passes (signature is valid), `ctx.accounts.initialize_token_metadata(data.payload)` runs, and a new SPL mint is registered in the bridge's token mapping.
5. No pause check is evaluated at any point. The call succeeds despite `config.paused == 0x03`.
6. The bridge now has a token mapping committed during the pause window. If the payload contained manipulated metadata (e.g., wrong decimals), all future `finalize_transfer` calls for that token will use the corrupted mapping.

### Citations

**File:** solana/programs/bridge_token_factory/src/constants.rs (L36-42)
```rust
pub const INIT_TRANSFER_PAUSED: u8 = 1 << 0;

#[constant]
pub const FINALIZE_TRANSFER_PAUSED: u8 = 1 << 1;

#[constant]
pub const ALL_PAUSED: u8 = INIT_TRANSFER_PAUSED | FINALIZE_TRANSFER_PAUSED;
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

**File:** solana/programs/bridge_token_factory/src/lib.rs (L82-85)
```rust
        require!(
            ctx.accounts.common.config.paused & FINALIZE_TRANSFER_PAUSED == 0,
            error::ErrorCode::Paused
        );
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
