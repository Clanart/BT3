### Title
`migrate_deployed_token` Permanently Freezes Old-Token Holders' Funds in Bridge — (`near/omni-bridge/src/lib.rs`)

---

### Summary

`migrate_deployed_token` atomically replaces all bridge mappings for `old_token` with `new_token`, but does not prevent users from continuing to send `old_token` to the bridge via `ft_transfer_call`. Any such transfer succeeds at the `init_transfer` stage (tokens are accepted and held by the bridge), but the subsequent `sign_transfer` call panics because the `token_id_to_address` entry for `old_token` was deleted. The user's tokens are permanently locked in the bridge with no recovery path.

---

### Finding Description

`migrate_deployed_token` performs the following state changes atomically:

1. Removes `old_token` from `deployed_tokens` and `deployed_tokens_v2`.
2. Removes `(origin_chain, old_token)` from `token_id_to_address`.
3. Overwrites `token_address_to_id[origin_address]` with `new_token`.
4. Inserts `old_token → new_token` into `migrated_tokens`. [1](#0-0) 

After this, `ft_on_transfer` still accepts `old_token` without any guard. The `init_transfer` function has no check that the incoming `token_id` is a currently-registered deployed token: [2](#0-1) 

Inside `init_transfer_internal`, the transfer message is stored and `burn_tokens_if_needed` is called. Since `is_deployed_token(old_token)` now returns `false` (it was removed from both `deployed_tokens` and `deployed_tokens_v2`), the tokens are **not burned** — they are silently held by the bridge contract. The function returns `U128(0)`, signalling to the NEP-141 standard that no refund is needed: [3](#0-2) [4](#0-3) 

When a relayer later calls `sign_transfer` for this pending transfer, it executes:

```rust
let token_address = self
    .get_token_address(
        transfer_message.get_destination_chain(),
        self.get_token_id(&transfer_message.token),
    )
    .unwrap_or_else(|| {
        env::panic_str(BridgeError::FailedToGetTokenAddress.to_string().as_str())
    });
``` [5](#0-4) 

`get_token_id` for a NEAR-origin token simply returns the `AccountId` directly: [6](#0-5) 

`get_token_address` then looks up `token_id_to_address.get(&(destination_chain, old_token))`, which returns `None` because the entry was deleted by `migrate_deployed_token`. The call panics unconditionally. The transfer message remains in `pending_transfers` forever, and the user's `old_token` balance is permanently locked inside the bridge contract. [7](#0-6) 

The `swap_migrated_token` mechanism (which lets users convert `old_token` to `new_token`) only works when the user sends tokens to the bridge with the `SwapMigratedToken` message variant. It cannot recover tokens that are already locked inside the bridge from a prior `init_transfer` call: [8](#0-7) 

---

### Impact Explanation

**Permanent freezing / irrecoverable lock of user funds.** Any `old_token` balance sent to the bridge after (or in-flight during) a `migrate_deployed_token` call is permanently locked. The bridge holds the tokens (they are not burned, not refunded, not transferable), and the pending transfer can never be signed or cancelled. This matches the allowed impact: *Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows.*

---

### Likelihood Explanation

**Medium.** Token migrations are operational events that users may not be aware of in real time. The NEAR bridge accepts any NEP-141 token via `ft_on_transfer` without validating that the token is currently registered. A user who holds `old_token` and attempts to bridge it — even seconds after the migration — will lose their funds permanently. No attacker action is required; ordinary user behaviour is sufficient to trigger the loss.

---

### Recommendation

Add a validation in `init_transfer` (or in `ft_on_transfer` before dispatching) that rejects tokens that are not currently registered as deployed tokens:

```rust
require!(
    self.is_deployed_token(&token_id),
    BridgeError::TokenNotRegistered.as_ref()
);
```

Returning a non-zero amount from `ft_on_transfer` will cause the NEP-141 standard to refund the tokens to the sender, preventing the lock. Alternatively, `migrate_deployed_token` could scan and cancel all pending transfers for `old_token` before removing the mapping, or emit an on-chain warning period before the mapping is deleted.

---

### Proof of Concept

1. DAO calls `migrate_deployed_token(ChainKind::Eth, "usdc.old.near", "usdc.new.near")`.
   - `deployed_tokens` no longer contains `"usdc.old.near"`.
   - `token_id_to_address[(Eth, "usdc.old.near")]` is deleted.
   - `migrated_tokens["usdc.old.near"] = "usdc.new.near"`.

2. Alice (unaware of migration) calls `ft_transfer_call` on `"usdc.old.near"` with receiver = bridge, `msg = InitTransfer{recipient: Eth:0xAlice, ...}`.

3. Bridge's `ft_on_transfer` fires. `init_transfer` runs:
   - Transfer message stored: `token = Near("usdc.old.near")`.
   - `burn_tokens_if_needed("usdc.old.near", amount)` → `is_deployed_token` returns `false` → no burn.
   - Returns `U128(0)` → NEP-141 does **not** refund Alice.
   - Alice's `old_token` balance is now held by the bridge.

4. Relayer calls `sign_transfer({origin_chain: Near, origin_nonce: N}, ...)`.
   - `get_token_id(Near("usdc.old.near"))` → `"usdc.old.near"`.
   - `get_token_address(Eth, "usdc.old.near")` → `None`.
   - **PANIC**: `BridgeError::FailedToGetTokenAddress`.

5. Alice's tokens are permanently locked. `swap_migrated_token` cannot help because the tokens are already inside the bridge, not in Alice's wallet.

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

**File:** near/omni-bridge/src/lib.rs (L523-543)
```rust
    fn init_transfer(
        &mut self,
        sender_id: AccountId,
        signer_id: AccountId,
        token_id: AccountId,
        amount: U128,
        init_transfer_msg: InitTransferMsg,
    ) -> PromiseOrPromiseIndexOrValue<U128> {
        require!(
            init_transfer_msg.recipient.get_chain() != ChainKind::Near,
            BridgeError::InvalidRecipientChain.as_ref()
        );

        self.current_origin_nonce += 1;
        let destination_nonce =
            self.get_next_destination_nonce(init_transfer_msg.get_destination_chain());

        let transfer_message = TransferMessage {
            origin_nonce: self.current_origin_nonce,
            token: OmniAddress::Near(token_id),
            amount,
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

**File:** near/omni-bridge/src/lib.rs (L1368-1376)
```rust
    pub fn get_token_id(&self, address: &OmniAddress) -> AccountId {
        if let OmniAddress::Near(token_account_id) = address {
            token_account_id.clone()
        } else {
            self.token_address_to_id
                .get(address)
                .near_expect(BridgeError::TokenNotRegistered)
        }
    }
```

**File:** near/omni-bridge/src/lib.rs (L1617-1642)
```rust
        require!(
            self.deployed_tokens.remove(&old_token),
            BridgeError::OldTokenNotDeployed.as_ref(),
        );
        require!(
            self.deployed_tokens.insert(&new_token),
            BridgeError::TokenExists.as_ref()
        );
        self.deployed_tokens_v2.remove(&old_token);
        self.deployed_tokens_v2.insert(&new_token, &origin_chain);

        let origin_address = self
            .token_id_to_address
            .remove(&(origin_chain, old_token.clone()))
            .near_expect(BridgeError::FailedToGetTokenAddress);

        require!(
            self.token_id_to_address
                .insert(&(origin_chain, new_token.clone()), &origin_address)
                .is_none(),
            BridgeError::TokenExists.as_ref()
        );

        self.token_address_to_id
            .insert(&origin_address, &new_token)
            .near_expect(BridgeError::ExpectedToOverwriteTokenAddress);
```

**File:** near/omni-bridge/src/lib.rs (L1806-1813)
```rust
    fn burn_tokens_if_needed(&self, token: AccountId, amount: U128) {
        if self.is_deployed_token(&token) {
            ext_token::ext(token)
                .with_static_gas(BURN_TOKEN_GAS)
                .burn(amount)
                .detach();
        }
    }
```

**File:** near/omni-bridge/src/lib.rs (L1850-1864)
```rust
        if let OmniAddress::Near(token_id) = transfer_message.token.clone() {
            self.burn_tokens_if_needed(token_id.clone(), transfer_message.amount);

            self.lock_tokens_if_needed(
                transfer_message.get_destination_chain(),
                &token_id,
                transfer_message.amount.0,
            );
        } else {
            self.remove_transfer_message_without_refund(transfer_message.get_transfer_id());
            return transfer_message.amount;
        }

        env::log_str(&OmniBridgeEvent::InitTransferEvent { transfer_message }.to_log_string());
        U128(0)
```

**File:** near/omni-bridge/src/lib.rs (L2738-2753)
```rust
    fn swap_migrated_token(
        &mut self,
        sender_id: AccountId,
        old_token: AccountId,
        amount: U128,
    ) -> Promise {
        let new_token = self
            .migrated_tokens
            .get(&old_token)
            .near_expect(BridgeError::TokenNotMigrated);

        let burn = ext_token::ext(old_token).burn(amount);
        let mint = ext_token::ext(new_token).mint(sender_id, amount, None);

        burn.and(mint)
    }
```
