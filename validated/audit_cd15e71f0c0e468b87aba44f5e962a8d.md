### Title
Detached (Unchecked) Burn Promise in `burn_tokens_if_needed` Allows Silent Burn Failure, Creating Unbacked Supply on Destination Chain — (File: `near/omni-bridge/src/lib.rs`)

### Summary
`burn_tokens_if_needed` fires the cross-contract `burn` call with `.detach()`, meaning the NEAR runtime never awaits or inspects the result. If the burn fails for any reason, the bridge still emits `InitTransferEvent` and the transfer message is stored, causing the relayer to mint tokens on the destination chain against tokens that were never actually destroyed on NEAR.

### Finding Description

In `near/omni-bridge/src/lib.rs`, the helper `burn_tokens_if_needed` schedules a cross-contract `burn` call and immediately detaches the resulting promise: [1](#0-0) 

`.detach()` is the NEAR SDK idiom for "fire and forget" — the parent transaction does not register a callback and cannot observe whether the child call succeeded or panicked.

This helper is invoked inside `init_transfer_internal` **before** the `InitTransferEvent` is emitted: [2](#0-1) 

The sequence is:
1. User calls `ft_transfer_call` on a deployed bridge token, transferring `amount` tokens to the bridge contract.
2. The token contract calls `bridge.ft_on_transfer`, which calls `init_transfer_internal`.
3. `burn_tokens_if_needed` fires a detached `burn` cross-contract call — result never checked.
4. `InitTransferEvent` is emitted unconditionally.
5. `ft_on_transfer` returns `U128(0)` (no refund), so the token contract considers the transfer final.
6. If the detached burn fails (e.g., out-of-gas, token contract panic, insufficient storage registration), the bridge still holds the tokens, but the event has already been emitted.

The `OmniToken.burn` implementation withdraws from `env::predecessor_account_id()` (the bridge): [3](#0-2) 

Any panic inside that call (balance underflow, storage issue, gas exhaustion) is silently swallowed because the promise is detached.

The same pattern appears in the `fin_transfer_send_tokens_callback` refund path, where a detached burn is used to destroy tokens that were minted to the bridge when a `ft_transfer_call` to the recipient was rejected: [4](#0-3) 

If that burn also fails silently, the bridge accumulates minted-but-not-burned tokens.

### Impact Explanation

If the detached burn in `init_transfer_internal` fails:
- The `InitTransferEvent` is already on-chain and the transfer message is stored.
- The relayer processes the event and mints an equivalent amount of tokens on the destination chain (EVM, Solana, StarkNet).
- The NEAR-side tokens are **not** destroyed; they remain in the bridge contract.
- The result is unbacked supply on the destination chain — the bridge's collateral invariant (burned on source ↔ minted on destination) is broken.

This matches the allowed impact: **Balance/accounting corruption that breaks bridge collateralization**, and potentially **unauthorized mint of bridged assets on the destination chain**.

### Likelihood Explanation

The burn fails silently whenever:
- `BURN_TOKEN_GAS` is insufficient for the `internal_withdraw` execution path in the token contract (a realistic misconfiguration, especially as token contract complexity grows).
- The token contract panics for any reason (e.g., storage key corruption, upgrade incompatibility).

An unprivileged user triggers this path by calling `ft_transfer_call` on any deployed bridge token pointing to the bridge contract. No privileged access is required. The user does not need to cause the failure deliberately — any environmental condition that causes the burn to fail is sufficient.

### Recommendation

Replace the detached burn with an awaited promise and add a callback that reverts the transfer message and returns the tokens to the sender if the burn fails:

```rust
// Instead of:
ext_token::ext(token)
    .with_static_gas(BURN_TOKEN_GAS)
    .burn(amount)
    .detach();

// Use:
ext_token::ext(token)
    .with_static_gas(BURN_TOKEN_GAS)
    .burn(amount)
    .then(
        Self::ext(env::current_account_id())
            .with_static_gas(BURN_CALLBACK_GAS)
            .burn_tokens_callback(transfer_message, storage_owner),
    );
```

The callback should check `env::promise_result_checked(0, ...)` and, on failure, call `remove_transfer_message_without_refund` and return the full amount to the sender (refund via `ft_on_transfer` return value).

Similarly, the detached mint calls for fees in `fin_transfer_send_tokens_callback` should be awaited and their failures handled gracefully rather than silently dropped.

### Proof of Concept

1. Deploy a bridge token (`OmniToken`) with the NEAR bridge as controller.
2. Register a cross-chain transfer destination (e.g., EVM chain).
3. Call `ft_transfer_call` on the bridge token, sending `N` tokens to the bridge with a valid transfer message.
4. Arrange for the detached `burn` call to fail — e.g., by ensuring `BURN_TOKEN_GAS` is at the boundary where the `internal_withdraw` storage read/write exhausts the gas budget.
5. Observe: `InitTransferEvent` is emitted on NEAR; the bridge holds `N` tokens; the relayer mints `N` tokens on the destination chain.
6. Result: `N` tokens exist on the destination chain with no corresponding burned supply on NEAR — unbacked supply created. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** near/omni-bridge/src/lib.rs (L1702-1718)
```rust
        if Self::is_refund_required(is_ft_transfer_call) {
            self.burn_tokens_if_needed(
                token.clone(),
                U128(
                    transfer_message
                        .amount_without_fee()
                        .near_expect(BridgeError::InvalidFee),
                ),
            );

            self.revert_lock_actions(&lock_actions);

            self.remove_fin_transfer(&transfer_message.get_transfer_id(), storage_owner);

            env::log_str(
                &OmniBridgeEvent::FailedFinTransferEvent { transfer_message }.to_log_string(),
            );
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

**File:** near/omni-token/src/lib.rs (L146-151)
```rust
    fn burn(&mut self, amount: U128) {
        self.assert_controller();

        self.token
            .internal_withdraw(&env::predecessor_account_id(), amount.into());
    }
```
