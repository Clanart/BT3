Now I have all the information needed. Let me analyze the exact condition.

### Title
Inverted NEP-141 Refund-Amount Success Check in `submit_transfer_to_btc_connector_callback` Causes Fee Loss and Transfer Re-insertion on Every Successful BTC Withdrawal — (`near/omni-bridge/src/btc.rs`)

---

### Summary

`submit_transfer_to_btc_connector_callback` interprets the NEP-141 `ft_transfer_call` callback result backwards. In NEP-141, `ft_on_transfer` returns the **refund amount** (tokens to send back). Returning `0` means the receiver kept all tokens (success); returning `> 0` means tokens were partially or fully rejected (failure). The callback checks `result.0 > 0` as the success condition, which is the exact opposite of the standard. As a result, every normally-functioning BTC connector call (which returns `0` on acceptance) is treated as a failure: the fee is never distributed and the transfer is re-inserted into `pending_transfers` even though the connector has already accepted the tokens.

---

### Finding Description

In `submit_transfer_to_utxo_chain_connector`, the bridge:
1. Removes the transfer from `pending_transfers` (line 84)
2. Calls `ft_transfer_call` on the nBTC token, sending `amount` tokens to the connector (line 91)
3. Chains `submit_transfer_to_btc_connector_callback` to handle the result [1](#0-0) 

In the callback, the success/failure branch is:

```rust
if matches!(call_result, Ok(result) if result.0 > 0) {
    // send fee to relayer  ← only reached when connector REFUNDS tokens (failure)
} else {
    // re-insert transfer   ← reached when connector ACCEPTS tokens (success)
}
``` [2](#0-1) 

Per NEP-141, `ft_on_transfer` returns the amount to refund. A connector that successfully accepts a withdrawal returns `U128(0)` (no refund). The condition `result.0 > 0` is therefore `false` on success, causing the `else` branch to execute: the transfer is re-inserted and `send_fee_internal` is never called. [3](#0-2) 

---

### Impact Explanation

Every successful BTC outbound withdrawal produces two invariant violations simultaneously:

1. **Fee accounting corruption**: `send_fee_internal` is never called. The relayer's token fee (and any native fee) is permanently lost — it is neither minted nor transferred. This breaks the bridge's fee collateralization for every BTC withdrawal.

2. **Transfer double-finalization / re-submission**: The transfer is re-inserted into `pending_transfers` after the connector has already accepted the tokens. Any trusted relayer can call `submit_transfer_to_utxo_chain_connector` again on the same `transfer_id`. If the bridge holds sufficient nBTC balance (from other users' deposits), this sends a second batch of tokens to the connector for the same withdrawal — a direct double-spend of bridged BTC tokens.

---

### Likelihood Explanation

This triggers on **every** normal BTC withdrawal. The BTC connector's `ft_on_transfer` must return `0` to accept tokens per NEP-141. There is no configuration or edge case required; the bug fires unconditionally on the happy path. Any account that stakes the required NEAR and waits the activation period can become a trusted relayer via the permissionless `apply_for_trusted_relayer` function. [4](#0-3) 

---

### Recommendation

Invert the success condition to match NEP-141 semantics. A return value of `0` from `ft_on_transfer` means all tokens were consumed (success); `> 0` means tokens were refunded (failure):

```rust
// BEFORE (inverted):
if matches!(call_result, Ok(result) if result.0 > 0) {

// AFTER (correct):
if matches!(call_result, Ok(result) if result.0 == 0) {
```

Alternatively, check that the refunded amount equals zero, i.e., the connector kept the full `amount`:

```rust
if matches!(call_result, Ok(result) if result.0 < amount.0) {
```

---

### Proof of Concept

1. A user initiates a BTC outbound transfer via `ft_on_transfer` → `init_transfer`. Transfer is stored in `pending_transfers` with `fee.fee > 0`.
2. A trusted relayer calls `submit_transfer_to_utxo_chain_connector(transfer_id, msg, fee_recipient, fee)`.
3. The bridge removes the transfer (line 84) and calls `ft_transfer_call` to the connector.
4. The real BTC connector's `ft_on_transfer` accepts the withdrawal and returns `U128(0)`.
5. `submit_transfer_to_btc_connector_callback` receives `Ok(U128(0))`.
6. `result.0 > 0` → `false` → `else` branch executes.
7. `add_transfer_message` re-inserts the transfer into `pending_transfers`.
8. `send_fee_internal` is **never called** — the relayer receives no fee.
9. The connector holds the tokens and will process the BTC withdrawal.
10. A second call to `submit_transfer_to_utxo_chain_connector` with the same `transfer_id` succeeds (transfer is back in `pending_transfers`), sending another `amount` of nBTC to the connector. [5](#0-4)

### Citations

**File:** near/omni-bridge/src/btc.rs (L84-101)
```rust
        self.remove_transfer_message(transfer_id);

        let fee_recipient = fee_recipient.unwrap_or(env::predecessor_account_id());

        ext_token::ext(btc_account_id)
            .with_attached_deposit(ONE_YOCTO)
            .with_static_gas(FT_TRANSFER_CALL_GAS)
            .ft_transfer_call(self.get_utxo_chain_connector(chain_kind), amount, None, msg)
            .then(
                Self::ext(env::current_account_id())
                    .with_static_gas(SUBMIT_TRANSFER_TO_BTC_CONNECTOR_CALLBACK_GAS)
                    .submit_transfer_to_btc_connector_callback(
                        transfer.message,
                        transfer.owner,
                        fee_recipient,
                    ),
            )
    }
```

**File:** near/omni-bridge/src/btc.rs (L103-126)
```rust
    #[private]
    pub fn submit_transfer_to_btc_connector_callback(
        &mut self,
        transfer_msg: TransferMessage,
        transfer_owner: AccountId,
        fee_recipient: AccountId,
        #[callback_result] call_result: &Result<U128, PromiseError>,
    ) -> PromiseOrValue<()> {
        if matches!(call_result, Ok(result) if result.0 > 0) {
            let token_fee = transfer_msg.fee.fee.0;
            self.send_fee_internal(&transfer_msg, fee_recipient, token_fee)
        } else {
            let required_storage_balance =
                self.add_transfer_message(transfer_msg, transfer_owner.clone());

            self.update_storage_balance(
                transfer_owner,
                required_storage_balance,
                NearToken::from_yoctonear(0),
            );

            PromiseOrValue::Value(())
        }
    }
```

**File:** near/omni-bridge/src/lib.rs (L245-249)
```rust
#[trusted_relayer(
    bypass_roles(Role::DAO, Role::UnrestrictedRelayer),
    manager_roles(Role::DAO, Role::RelayerManager),
    config_roles(Role::DAO)
)]
```

**File:** near/omni-bridge/src/lib.rs (L2650-2701)
```rust
    fn send_fee_internal(
        &mut self,
        transfer_message: &TransferMessage,
        fee_recipient: AccountId,
        token_fee: u128,
    ) -> PromiseOrValue<()> {
        if transfer_message.fee.native_fee.0 != 0 {
            let origin_chain = transfer_message.origin_transfer_id.as_ref().map_or_else(
                || transfer_message.get_origin_chain(),
                |origin_transfer_id| origin_transfer_id.origin_chain,
            );

            if origin_chain.is_utxo_chain() {
                env::panic_str(BridgeError::NativeFeeForUtxoChain.to_string().as_str())
            } else if origin_chain == ChainKind::Near {
                Promise::new(fee_recipient.clone())
                    .transfer(NearToken::from_yoctonear(transfer_message.fee.native_fee.0))
                    .detach();
            } else {
                ext_token::ext(self.get_native_token_id(origin_chain))
                    .with_static_gas(MINT_TOKEN_GAS)
                    .mint(fee_recipient.clone(), transfer_message.fee.native_fee, None)
                    .detach();
            }
        }

        let token = self.get_token_id(&transfer_message.token);
        env::log_str(
            &OmniBridgeEvent::ClaimFeeEvent {
                transfer_message: transfer_message.clone(),
            }
            .to_log_string(),
        );

        self.unlock_tokens_if_needed(transfer_message.get_destination_chain(), &token, token_fee);

        if token_fee > 0 {
            if self.is_deployed_token(&token) {
                ext_token::ext(token)
                    .with_static_gas(MINT_TOKEN_GAS)
                    .mint(fee_recipient, U128(token_fee), None)
                    .into()
            } else {
                ext_token::ext(token)
                    .with_static_gas(FT_TRANSFER_GAS)
                    .with_attached_deposit(ONE_YOCTO)
                    .ft_transfer(fee_recipient, U128(token_fee), None)
                    .into()
            }
        } else {
            PromiseOrValue::Value(())
        }
```
