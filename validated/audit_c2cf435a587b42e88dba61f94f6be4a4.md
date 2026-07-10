### Title
Silent Burn Failure in `burn_tokens_if_needed` Enables Unbacked Cross-Chain Token Minting — (`near/omni-bridge/src/lib.rs`)

### Summary

`burn_tokens_if_needed` fires a cross-contract `burn` call with `.detach()`, discarding the promise result entirely. Every call site that invokes this function before emitting an `InitTransferEvent` or updating locked-token accounting proceeds as if the burn succeeded, even when it silently failed. This allows the bridge to record a valid outbound transfer and have MPC sign it while the source tokens remain unburned in the bridge's balance, creating unbacked supply on the destination chain.

### Finding Description

`burn_tokens_if_needed` is defined as:

```rust
fn burn_tokens_if_needed(&self, token: AccountId, amount: U128) {
    if self.is_deployed_token(&token) {
        ext_token::ext(token)
            .with_static_gas(BURN_TOKEN_GAS)
            .burn(amount)
            .detach();   // ← result never observed
    }
}
``` [1](#0-0) 

The critical call site is `init_transfer_internal`, which is the terminal handler for every NEAR-originated `ft_transfer_call` into the bridge:

```rust
fn init_transfer_internal(...) -> U128 {
    // 1. Transfer message stored (accounting updated)
    self.add_transfer_message(transfer_message.clone(), storage_owner.clone());
    ...
    if let OmniAddress::Near(token_id) = transfer_message.token.clone() {
        // 2. Burn fired-and-forgotten — failure is invisible
        self.burn_tokens_if_needed(token_id.clone(), transfer_message.amount);
        // 3. Locked-token accounting updated regardless
        self.lock_tokens_if_needed(...);
    }
    // 4. InitTransferEvent emitted regardless
    env::log_str(&OmniBridgeEvent::InitTransferEvent { transfer_message }.to_log_string());
    U128(0)   // ← 0 means "keep all tokens in bridge"
}
``` [2](#0-1) 

Because `ft_on_transfer` returns `U128(0)`, the NEP-141 token contract keeps all tokens in the bridge's balance. The burn is the only mechanism that destroys them. If the burn promise fails (e.g., `BURN_TOKEN_GAS` is insufficient, or the token contract panics during burn), the tokens remain in the bridge's balance, but:

- The transfer message is stored and will be signed by MPC.
- `locked_tokens` is incremented for the destination chain.
- `InitTransferEvent` is emitted, which is the sole trigger for MPC signing.

The same pattern appears in `fin_transfer_send_tokens_callback` (refund path), `resolve_fast_transfer`, and `fast_fin_transfer_to_other_chain`: [3](#0-2) [4](#0-3) [5](#0-4) 

The project's own security checklist acknowledges this risk:

> **4. Check .detach() usage**: Detached promises should only be used for non-critical operations [6](#0-5) 

The burn of a deployed token during `init_transfer_internal` is unambiguously a critical operation.

### Impact Explanation

For a deployed (bridge) token whose origin chain is NEAR:

1. User calls `ft_transfer_call` → tokens land in bridge's balance.
2. `init_transfer_internal` runs: burn is detached and fails silently; `locked_tokens[ETH] += amount`; `InitTransferEvent` emitted.
3. MPC signs the payload; destination chain (e.g., EVM) mints `amount` tokens to the user.
4. User now holds destination-chain tokens backed by nothing — the NEAR tokens were never destroyed.
5. If the user bridges back (ETH → NEAR), `unlock_tokens_if_needed` decrements `locked_tokens[ETH]` and the bridge mints fresh NEAR tokens for the user.
6. The original tokens remain stranded in the bridge's balance, permanently inflating total supply.

This is a direct, unbacked cross-chain mint: the destination chain holds tokens with no corresponding burned supply on NEAR. The bridge's collateralization invariant is broken.

### Likelihood Explanation

The burn failure can be triggered by:

- **Gas exhaustion**: `BURN_TOKEN_GAS` is a static constant. If the token contract's `burn` implementation consumes more gas than allocated (e.g., due to a contract upgrade, additional storage writes, or a complex access-control check), every user bridging that token will silently skip the burn. No privileged access is required — any user calling `ft_transfer_call` on an affected deployed token triggers the path.
- **Token contract panic**: Any panic inside the token's `burn` function (e.g., an assertion failure, an arithmetic overflow in a non-standard implementation) causes the detached promise to fail silently.

The entry path is fully unprivileged: `ft_transfer_call` → `ft_on_transfer` → `init_transfer_internal` → `burn_tokens_if_needed`.

### Recommendation

Replace the fire-and-forget pattern with an awaited callback that reverts the transfer if the burn fails:

```rust
fn burn_tokens_if_needed(&self, token: AccountId, amount: U128) -> Option<Promise> {
    if self.is_deployed_token(&token) {
        Some(
            ext_token::ext(token)
                .with_static_gas(BURN_TOKEN_GAS)
                .burn(amount)
        )
    } else {
        None
    }
}
```

In `init_transfer_internal`, chain the burn promise and only emit `InitTransferEvent` (and update `locked_tokens`) in the burn's success callback. If the burn fails, refund the tokens to the sender (return `transfer_message.amount` from `ft_on_transfer`).

### Proof of Concept

1. Deploy a bridge token whose `burn` function consumes slightly more gas than `BURN_TOKEN_GAS` (or temporarily panics).
2. Call `ft_transfer_call` on that token, targeting the bridge, with a valid `InitTransferMsg` for an EVM destination.
3. Observe: `InitTransferEvent` is emitted, `locked_tokens[ETH]` is incremented, but the bridge's token balance is unchanged (tokens not burned).
4. MPC signs the payload; EVM `finTransfer` mints tokens to the recipient.
5. Recipient bridges back: EVM burns tokens, NEAR `fin_transfer` unlocks and mints fresh tokens.
6. Total supply on NEAR is now `original_supply + amount` (the stuck tokens + the newly minted tokens), while EVM supply is 0 — net inflation of `amount` tokens with no backing. [1](#0-0) [7](#0-6)

### Citations

**File:** near/omni-bridge/src/lib.rs (L903-912)
```rust
        // Burn the tokens to ensure the locked tokens are not double-minted
        self.burn_tokens_if_needed(token_id.clone(), amount);

        if Self::is_refund_required(is_ft_transfer_call) {
            self.remove_fast_transfer(fast_transfer_id);
            amount
        } else {
            U128(0)
        }
    }
```

**File:** near/omni-bridge/src/lib.rs (L932-938)
```rust
        self.burn_tokens_if_needed(fast_transfer.token_id.clone(), amount_without_fee.into());

        self.lock_tokens_if_needed(
            fast_transfer.get_destination_chain(),
            &fast_transfer.token_id,
            amount_without_fee,
        );
```

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

**File:** near/omni-bridge/src/lib.rs (L1829-1865)
```rust
    fn init_transfer_internal(
        &mut self,
        transfer_message: TransferMessage,
        storage_owner: AccountId,
    ) -> U128 {
        let required_storage_balance = self
            .add_transfer_message(transfer_message.clone(), storage_owner.clone())
            .saturating_add(NearToken::from_yoctonear(transfer_message.fee.native_fee.0));

        if self
            .try_update_storage_balance(
                storage_owner,
                required_storage_balance,
                NearToken::from_yoctonear(0),
            )
            .is_err()
        {
            self.remove_transfer_message_without_refund(transfer_message.get_transfer_id());
            return transfer_message.amount;
        }

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

**File:** near/CLAUDE.md (L228-228)
```markdown
4. **Check .detach() usage**: Detached promises should only be used for non-critical operations
```
