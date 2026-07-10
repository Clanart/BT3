### Title
Transfer to Unregistered Destination Chain Causes Permanent Fund Loss — (`File: near/omni-bridge/src/lib.rs`)

### Summary

`init_transfer` in the NEAR bridge contract burns or locks user tokens before verifying that the token has a registered address on the destination chain. If the destination chain has no registered token address, the subsequent `sign_transfer` call will always panic, leaving the transfer permanently stuck with no cancel path.

### Finding Description

`init_transfer` (called via `ft_transfer_call`) accepts any `OmniAddress` recipient whose chain is not `ChainKind::Near`. The only guard is:

```rust
require!(
    init_transfer_msg.recipient.get_chain() != ChainKind::Near,
    BridgeError::InvalidRecipientChain.as_ref()
);
``` [1](#0-0) 

There is no check that `token_id_to_address` contains an entry for `(destination_chain, token_id)`. Execution proceeds directly to `init_transfer_internal`, which irreversibly burns deployed (bridged) tokens or locks native tokens:

```rust
self.burn_tokens_if_needed(token_id.clone(), transfer_message.amount);
self.lock_tokens_if_needed(
    transfer_message.get_destination_chain(),
    &token_id,
    transfer_message.amount.0,
);
``` [2](#0-1) 

The burn is fire-and-forget (`.detach()`), so it cannot be rolled back:

```rust
fn burn_tokens_if_needed(&self, token: AccountId, amount: U128) {
    if self.is_deployed_token(&token) {
        ext_token::ext(token)
            .with_static_gas(BURN_TOKEN_GAS)
            .burn(amount)
            .detach();
    }
}
``` [3](#0-2) 

Later, when a trusted relayer calls `sign_transfer`, it calls `get_token_address` for the destination chain:

```rust
let token_address = self
    .get_token_address(
        transfer_message.get_destination_chain(),
        self.get_token_id(&transfer_message.token),
    )
    .unwrap_or_else(|| {
        env::panic_str(BridgeError::FailedToGetTokenAddress.to_string().as_str())
    });
``` [4](#0-3) 

`get_token_address` is a simple map lookup that returns `None` if the token is not registered for that chain:

```rust
pub fn get_token_address(&self, chain_kind: ChainKind, token: AccountId) -> Option<OmniAddress> {
    self.token_id_to_address.get(&(chain_kind, token))
}
``` [5](#0-4) 

If the token is not registered on the destination chain, `sign_transfer` panics every time it is called for this transfer ID. There is no public `cancel_transfer` function. The transfer message remains in `pending_transfers` indefinitely, and the burned tokens are unrecoverable.

### Impact Explanation

**Critical — Permanent irrecoverable lock/burn of user funds.**

- For **deployed (bridged) tokens**: tokens are burned on NEAR via a detached promise. Even if the token is later registered on the destination chain, the burned supply cannot be restored without a corresponding unlock on the origin chain, which never happened. Funds are permanently destroyed.
- For **native NEAR tokens**: tokens are locked in the bridge. They are inaccessible to the user with no cancel path. Only a privileged DAO `set_locked_tokens` call could manually adjust the accounting, but the user has no recourse.

This matches the allowed impact: *"Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows."*

### Likelihood Explanation

**Medium.** The Omni Bridge supports multiple destination chains (Eth, Base, Arb, Bnb, Pol, Sol, Strk, HyperEvm, Abs, Fogo, BTC, Zcash). A token may be registered on some chains but not others. A user who specifies a recipient address on a chain where the token has not yet been deployed (or will never be deployed) will lose their funds. The `OmniAddress` type encodes the chain in the address prefix, making it straightforward for a user to construct a recipient on any supported chain. No special privileges are required — any token holder calling `ft_transfer_call` is exposed.

### Recommendation

In `init_transfer`, before calling `init_transfer_internal`, verify that the token has a registered address on the destination chain:

```rust
require!(
    self.get_token_address(
        init_transfer_msg.get_destination_chain(),
        token_id.clone(),
    ).is_some(),
    BridgeError::FailedToGetTokenAddress.as_ref()
);
```

This mirrors the fix described in the external report (`_wormholeRemotes[dstWormholeChainId] != bytes(0)`) and ensures the transfer is rejected before any irreversible state change occurs.

### Proof of Concept

1. Token `usdc.near` is registered on `ChainKind::Eth` but **not** on `ChainKind::Sol`.
2. User calls `ft_transfer_call` on `usdc.near` with `msg` = `InitTransferMsg { recipient: OmniAddress::Sol(<valid_sol_address>), ... }`.
3. `init_transfer` passes the only guard (`recipient.get_chain() != ChainKind::Near` → `Sol != Near` → passes). [1](#0-0) 
4. `init_transfer_internal` is called. `burn_tokens_if_needed` fires a detached burn of the user's USDC. [6](#0-5) 
5. Transfer message is stored in `pending_transfers`.
6. Trusted relayer calls `sign_transfer` for this transfer ID.
7. `get_token_address(ChainKind::Sol, usdc.near)` returns `None` → `env::panic_str(FailedToGetTokenAddress)`. [4](#0-3) 
8. No public cancel function exists. The transfer is permanently stuck. The burned USDC is unrecoverable.

### Citations

**File:** near/omni-bridge/src/lib.rs (L462-469)
```rust
        let token_address = self
            .get_token_address(
                transfer_message.get_destination_chain(),
                self.get_token_id(&transfer_message.token),
            )
            .unwrap_or_else(|| {
                env::panic_str(BridgeError::FailedToGetTokenAddress.to_string().as_str())
            });
```

**File:** near/omni-bridge/src/lib.rs (L531-534)
```rust
        require!(
            init_transfer_msg.recipient.get_chain() != ChainKind::Near,
            BridgeError::InvalidRecipientChain.as_ref()
        );
```

**File:** near/omni-bridge/src/lib.rs (L1360-1366)
```rust
    pub fn get_token_address(
        &self,
        chain_kind: ChainKind,
        token: AccountId,
    ) -> Option<OmniAddress> {
        self.token_id_to_address.get(&(chain_kind, token))
    }
```

**File:** near/omni-bridge/src/lib.rs (L1806-1812)
```rust
    fn burn_tokens_if_needed(&self, token: AccountId, amount: U128) {
        if self.is_deployed_token(&token) {
            ext_token::ext(token)
                .with_static_gas(BURN_TOKEN_GAS)
                .burn(amount)
                .detach();
        }
```

**File:** near/omni-bridge/src/lib.rs (L1850-1857)
```rust
        if let OmniAddress::Near(token_id) = transfer_message.token.clone() {
            self.burn_tokens_if_needed(token_id.clone(), transfer_message.amount);

            self.lock_tokens_if_needed(
                transfer_message.get_destination_chain(),
                &token_id,
                transfer_message.amount.0,
            );
```
