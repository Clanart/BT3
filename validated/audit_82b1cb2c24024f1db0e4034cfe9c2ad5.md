### Title
Destination-chain pause permanently freezes in-flight bridge transfers with no recovery mechanism - (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`, `near/omni-bridge/src/lib.rs`, `solana/programs/bridge_token_factory/src/lib.rs`)

### Summary
Every destination-chain bridge contract in Omni Bridge (EVM `OmniBridge`, NEAR `omni-bridge`, Solana `bridge_token_factory`) exposes a pause mechanism that blocks the finalization step (`finTransfer` / `fin_transfer` / `finalize_transfer`). Because assets are irrevocably burned or locked on the source chain before the destination-chain finalization is attempted, a pause active during the relay window causes permanent, unrecoverable loss of user funds. No refund or retry-from-source path exists in any of the affected contracts.

### Finding Description

**Vulnerability class:** callback/state desync — pause blocks destination-chain finalization after source-chain assets are already committed.

The bridge flow across all supported chains follows the same two-phase pattern:

1. **Source chain** — user assets are burned or locked (irreversible).
2. **Destination chain** — a relayer submits a finalization transaction to mint or release assets.

Each destination-chain contract guards its finalization entry point with a pause flag:

**EVM (`OmniBridge.finTransfer`):**
```solidity
function finTransfer(
    bytes calldata signatureData,
    BridgeTypes.TransferMessagePayload calldata payload
) external payable whenNotPaused(PAUSED_FIN_TRANSFER) {
``` [1](#0-0) 

`PAUSED_FIN_TRANSFER` is one of the flags set by `pauseAll()`, callable by any account holding `PAUSABLE_ADMIN_ROLE`: [2](#0-1) 

**NEAR (`omni-bridge` `fin_transfer`):**
```rust
#[payable]
#[trusted_relayer]
#[pause(except(roles(Role::DAO)))]
pub fn fin_transfer(&mut self, #[serializer(borsh)] args: FinTransferArgs) -> Promise {
``` [3](#0-2) 

When the NEAR contract is paused, only `Role::DAO` may call `fin_transfer`; regular trusted relayers are blocked.

**Solana (`bridge_token_factory` `finalize_transfer` / `finalize_transfer_sol`):**
```rust
require!(
    ctx.accounts.common.config.paused & FINALIZE_TRANSFER_PAUSED == 0,
    error::ErrorCode::Paused
);
``` [4](#0-3) [5](#0-4) 

`FINALIZE_TRANSFER_PAUSED` is bit 1 of the `paused` byte; `ALL_PAUSED` sets both bits: [6](#0-5) 

**Source-chain asset commitment is irreversible in all directions:**

*EVM → NEAR:* `initTransfer` burns bridge tokens or locks native tokens on EVM with no refund path: [7](#0-6) 

*NEAR → EVM (zero-fee):* `init_transfer_internal` burns deployed tokens on NEAR, and `sign_transfer_callback` **deletes the transfer message** from `pending_transfers` for zero-fee transfers immediately after signing — before EVM finalization is confirmed: [8](#0-7) [9](#0-8) 

*Solana → NEAR:* `init_transfer` / `init_transfer_sol` burns or locks tokens in the Solana vault before the Wormhole message is relayed to NEAR: [10](#0-9) 

### Impact Explanation

**Impact: Critical — permanent freezing / irrecoverable lock of user funds.**

- **EVM → NEAR:** Tokens are burned or locked on EVM. If the NEAR bridge is paused when the relayer submits `fin_transfer`, the call reverts. There is no on-chain refund function on EVM; the user's assets are permanently lost.
- **NEAR → EVM (zero-fee):** Tokens are burned on NEAR and the transfer message is deleted from `pending_transfers` after `sign_transfer_callback` runs. If the EVM bridge is paused when the relayer submits `finTransfer`, the call reverts. Because the NEAR-side record is gone, no re-signing is possible; the user's assets are permanently lost.
- **Solana → NEAR / NEAR → Solana:** Identical pattern — vault tokens locked on Solana with no unlock path if NEAR `fin_transfer` or Solana `finalize_transfer` is paused.

### Likelihood Explanation

**Likelihood: Low** (matching the external report's assessment).

The scenario requires the destination-chain contract to be paused during the relay window. This is a realistic operational event: emergency pauses are triggered in response to exploits, upgrades, or security incidents. The relay window can span minutes to hours depending on Wormhole VAA finality or MPC signing latency, creating a non-trivial exposure period. The `pauseAll()` function on EVM is callable by any `PAUSABLE_ADMIN_ROLE` holder, and the NEAR `PauseManager` role similarly allows rapid pausing.

### Recommendation

1. **Do not delete the NEAR transfer message before destination-chain finalization is confirmed.** For zero-fee transfers, defer `remove_transfer_message` until a proof of EVM/Solana finalization is submitted (analogous to the `claim_fee` flow for non-zero-fee transfers).
2. **Add a source-chain refund/cancel path.** If finalization has not occurred within a timeout, allow the user (or a relayer on their behalf) to cancel the transfer and reclaim locked/burned tokens on the source chain.
3. **Exclude `fin_transfer` / `finTransfer` / `finalize_transfer` from the global pause**, or implement a separate, narrower pause flag that does not block in-flight finalizations — only new initiations.

### Proof of Concept

**EVM → NEAR (most direct analog):**

1. User calls `OmniBridge.initTransfer(tokenAddress, amount, ...)` on EVM. Bridge tokens are burned via `BridgeToken(tokenAddress).burn(msg.sender, amount)`. [11](#0-10) 

2. Admin calls `OmniBridge.pauseAll()` on EVM (or `pause(PAUSED_FIN_TRANSFER)`), setting `PAUSED_FIN_TRANSFER`. [2](#0-1) 

3. Relayer calls `fin_transfer` on NEAR bridge. The `#[pause(except(roles(Role::DAO)))]` macro causes the call to revert for any non-DAO caller. [3](#0-2) 

4. No refund function exists on EVM. User's tokens are permanently lost.

**NEAR → EVM zero-fee (no-recovery path):**

1. User calls `ft_transfer_call` on NEAR token contract with `InitTransfer` message. `init_transfer_internal` burns the deployed token. [8](#0-7) 

2. Relayer calls `sign_transfer` on NEAR. MPC signature is obtained. Because `fee.is_zero()`, `sign_transfer_callback` calls `remove_transfer_message`, deleting the pending transfer record. [9](#0-8) 

3. Admin calls `OmniBridge.pauseAll()` on EVM. Relayer calls `finTransfer` — reverts with `"Pausable: paused"`. [1](#0-0) 

4. The NEAR transfer message is gone; `sign_transfer` cannot be called again. The EVM is paused; `finTransfer` cannot be called. User's tokens are permanently lost with no recovery path.

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L279-283)
```text
    function finTransfer(
        bytes calldata signatureData,
        BridgeTypes.TransferMessagePayload calldata payload
    ) external payable whenNotPaused(PAUSED_FIN_TRANSFER) {
        if (completedTransfers[payload.destinationNonce]) {
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L394-412)
```text
            if (customMinters[tokenAddress] != address(0)) {
                IERC20(tokenAddress).safeTransferFrom(
                    msg.sender,
                    customMinters[tokenAddress],
                    amount
                );
                ICustomMinter(customMinters[tokenAddress]).burn(
                    tokenAddress,
                    amount
                );
            } else if (isBridgeToken[tokenAddress]) {
                BridgeToken(tokenAddress).burn(msg.sender, amount);
            } else {
                IERC20(tokenAddress).safeTransferFrom(
                    msg.sender,
                    address(this),
                    amount
                );
            }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L552-557)
```text
    function pauseAll() external onlyRole(PAUSABLE_ADMIN_ROLE) {
        uint256 flags = PAUSED_FIN_TRANSFER |
            PAUSED_INIT_TRANSFER |
            PAUSED_DEPLOY_TOKEN;
        _pause(flags);
    }
```

**File:** near/omni-bridge/src/lib.rs (L655-658)
```rust
        if let Ok(signature) = call_result {
            if fee.is_zero() {
                self.remove_transfer_message(message_payload.transfer_id);
            }
```

**File:** near/omni-bridge/src/lib.rs (L670-673)
```rust
    #[payable]
    #[trusted_relayer]
    #[pause(except(roles(Role::DAO)))]
    pub fn fin_transfer(&mut self, #[serializer(borsh)] args: FinTransferArgs) -> Promise {
```

**File:** near/omni-bridge/src/lib.rs (L1850-1851)
```rust
        if let OmniAddress::Near(token_id) = transfer_message.token.clone() {
            self.burn_tokens_if_needed(token_id.clone(), transfer_message.amount);
```

**File:** solana/programs/bridge_token_factory/src/lib.rs (L82-85)
```rust
        require!(
            ctx.accounts.common.config.paused & FINALIZE_TRANSFER_PAUSED == 0,
            error::ErrorCode::Paused
        );
```

**File:** solana/programs/bridge_token_factory/src/lib.rs (L101-104)
```rust
        require!(
            ctx.accounts.common.config.paused & FINALIZE_TRANSFER_PAUSED == 0,
            error::ErrorCode::Paused
        );
```

**File:** solana/programs/bridge_token_factory/src/lib.rs (L124-134)
```rust
    pub fn init_transfer(ctx: Context<InitTransfer>, payload: InitTransferPayload) -> Result<()> {
        require!(
            ctx.accounts.common.config.paused & INIT_TRANSFER_PAUSED == 0,
            error::ErrorCode::Paused
        );
        msg!("Initializing transfer");

        ctx.accounts.process(&payload)?;

        Ok(())
    }
```

**File:** solana/programs/bridge_token_factory/src/constants.rs (L36-43)
```rust
pub const INIT_TRANSFER_PAUSED: u8 = 1 << 0;

#[constant]
pub const FINALIZE_TRANSFER_PAUSED: u8 = 1 << 1;

#[constant]
pub const ALL_PAUSED: u8 = INIT_TRANSFER_PAUSED | FINALIZE_TRANSFER_PAUSED;

```
