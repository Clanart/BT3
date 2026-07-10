### Title
Unregistered Token Accepted by `ft_on_transfer` Causes Irrecoverable Fund Lock — (File: near/omni-bridge/src/lib.rs)

---

### Summary

The NEAR bridge's `ft_on_transfer` entry point accepts any NEP-141 token without verifying it is registered in the bridge's token registry. When an unregistered token is transferred to the bridge, the contract retains the tokens (returns `U128(0)`) and emits an `InitTransferEvent`, but the subsequent `sign_transfer` step panics for unregistered tokens. There is no cancel or refund mechanism, so the user's tokens are permanently frozen inside the bridge.

---

### Finding Description

`ft_on_transfer` (line 253) derives the token identity solely from `env::predecessor_account_id()` and passes it directly to `init_transfer` with no registry check: [1](#0-0) 

`init_transfer` builds a `TransferMessage` with `token: OmniAddress::Near(token_id)` and calls `init_transfer_internal`: [2](#0-1) 

Inside `init_transfer_internal`, for an unregistered token the code reaches the `OmniAddress::Near` branch, calls `burn_tokens_if_needed` (no-op for non-deployed tokens) and `lock_tokens_if_needed` (no-op for tokens absent from `locked_tokens`), then emits `InitTransferEvent` and returns `U128(0)` — keeping the tokens: [3](#0-2) 

The helper `burn_tokens_if_needed` only acts on tokens in `deployed_tokens`: [4](#0-3) 

When a relayer later calls `sign_transfer`, it invokes `get_token_address` for the unregistered token, which returns `None` and panics: [5](#0-4) 

`get_token_address` simply looks up `token_id_to_address`, which has no entry for the unregistered token: [6](#0-5) 

There is no public `cancel_transfer` or refund path once `init_transfer_internal` has returned `U128(0)`. The transfer message sits in `pending_transfers` indefinitely and the tokens are irrecoverable.

---

### Impact Explanation

**Permanent freezing / irrecoverable lock of user funds.** Any user who calls `ft_transfer_call` on an unregistered NEP-141 token targeting the bridge will have their tokens permanently locked. The bridge contract holds the tokens, the `InitTransferEvent` is emitted (so the user believes the transfer is in flight), but `sign_transfer` always panics for unregistered tokens and no refund path exists.

---

### Likelihood Explanation

**Medium.** The bridge is designed to be permissionless and the correct flow (register via `log_metadata` first, then bridge) is not enforced at the contract level. A user who bridges a token before it is registered — or who is socially engineered into doing so — permanently loses their funds. The attack requires only a standard NEP-141 `ft_transfer_call`, which is the normal user-facing entry point.

---

### Recommendation

In `ft_on_transfer` or at the start of `init_transfer`, verify that the calling token is registered in the bridge before accepting the transfer. If the token is not registered, return the full `amount` immediately so the NEP-141 standard refunds the sender:

```rust
fn init_transfer(..., token_id: AccountId, ...) {
    // Reject unregistered tokens immediately
    require!(
        self.token_id_to_address.contains_key(&(destination_chain, token_id.clone()))
            || self.is_deployed_token(&token_id),
        BridgeError::TokenNotRegistered.as_ref()
    );
    // ... rest of logic
}
```

Alternatively, add a public `cancel_transfer` function that allows users to reclaim tokens from stuck pending transfers.

---

### Proof of Concept

1. `unregistered.token.near` is a valid NEP-141 token not present in `token_id_to_address`, `deployed_tokens`, or `locked_tokens`.
2. User calls `unregistered.token.near.ft_transfer_call(bridge_id, 1_000_000, init_transfer_msg)`.
3. Bridge's `ft_on_transfer` fires with `token_id = unregistered.token.near`.
4. `init_transfer_internal` passes the storage-balance check, skips burn/lock (no-ops), emits `InitTransferEvent`, and returns `U128(0)` — tokens are now held by the bridge.
5. Relayer calls `sign_transfer` for the pending transfer ID.
6. `get_token_address(destination_chain, "unregistered.token.near")` returns `None`; `unwrap_or_else` panics with `BridgeError::FailedToGetTokenAddress`.
7. The relayer's transaction reverts. The transfer message remains in `pending_transfers`. The 1,000,000 tokens are permanently locked with no recovery path.

### Citations

**File:** near/omni-bridge/src/lib.rs (L252-263)
```rust
    #[pause(except(roles(Role::DAO, Role::UnrestrictedDeposit)))]
    pub fn ft_on_transfer(&mut self, sender_id: AccountId, amount: U128, msg: String) {
        let token_id = env::predecessor_account_id();
        let parsed_msg: BridgeOnTransferMsg = serde_json::from_str(&msg)
            .or_else(|_| serde_json::from_str(&msg).map(BridgeOnTransferMsg::InitTransfer))
            .near_expect(BridgeError::ParseMsg);

        // We can't trust sender_id to pay for storage as it can be spoofed.
        let signer_id = env::signer_account_id();
        let promise_or_promise_index_or_value = match parsed_msg {
            BridgeOnTransferMsg::InitTransfer(init_transfer_msg) => {
                self.init_transfer(sender_id, signer_id, token_id, amount, init_transfer_msg)
```

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

**File:** near/omni-bridge/src/lib.rs (L540-557)
```rust
        let transfer_message = TransferMessage {
            origin_nonce: self.current_origin_nonce,
            token: OmniAddress::Near(token_id),
            amount,
            recipient: init_transfer_msg.recipient,
            fee: Fee {
                fee: init_transfer_msg.fee,
                native_fee: init_transfer_msg.native_token_fee,
            },
            sender: OmniAddress::Near(sender_id),
            msg: init_transfer_msg.msg.map(String::from).unwrap_or_default(),
            destination_nonce,
            origin_transfer_id: None,
        };
        require!(
            transfer_message.fee.fee < transfer_message.amount,
            BridgeError::InvalidFee.as_ref()
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

**File:** near/omni-bridge/src/lib.rs (L1850-1865)
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
    }
```
