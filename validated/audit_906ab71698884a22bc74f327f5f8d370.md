### Title
`ft_transfer_call` Failure Silently Finalizes Transfer Without Token Delivery, Permanently Locking Funds — (File: `near/omni-bridge/src/lib.rs`)

### Summary
In the NEAR bridge's `fin_transfer_send_tokens_callback`, when a `ft_transfer_call` promise fails entirely (returns `Err`), the helper `is_refund_required` returns `false`, causing the transfer to be permanently finalized even though the recipient never received the tokens. The transfer ID is irrevocably inserted into `finalised_transfers`, preventing any retry, while the tokens remain locked inside the bridge contract.

### Finding Description
`process_fin_transfer_to_near` first marks the transfer as finalized via `add_fin_transfer`, then dispatches `send_tokens`. When the transfer carries a non-empty `msg` field, `send_tokens` issues an `ft_transfer_call` (not a plain `ft_transfer`). The result is handled in `fin_transfer_send_tokens_callback`, which calls `is_refund_required` to decide whether to revert the finalization. [1](#0-0) 

The critical flaw is in `is_refund_required`:

```rust
Err(_) => false,  // Unexpected case: don't refund
```

When `ft_transfer_call` fails entirely (the receiver's `ft_on_transfer` panics, or the token contract itself panics), NEAR's runtime returns the tokens to the bridge (the sender), but the promise result is `Err`. `is_refund_required(true)` returns `false` for this `Err` branch, so the `else` branch of `fin_transfer_send_tokens_callback` executes: [2](#0-1) 

The `FinTransferEvent` is emitted and the transfer remains in `finalised_transfers`. The `add_fin_transfer` call that inserted the transfer ID is never undone: [3](#0-2) 

The result is a permanent state desync: the bridge's accounting says the transfer is complete, but the recipient received nothing, and the tokens are stranded inside the bridge with no recovery path.

This is the direct analog of the external report's pattern: state (finalization record) is updated as if the action succeeded, but the action (token delivery) was not executed.

### Impact Explanation
User funds are permanently frozen in the bridge. The transfer ID is irrevocably finalized — there is no admin function to remove a `finalised_transfers` entry — so the transfer cannot be retried. The tokens remain in the bridge contract but are unclaimable by anyone. This matches the allowed impact: **permanent freezing / irrecoverable lock of user funds in bridge flows**.

### Likelihood Explanation
The trigger condition is that `ft_transfer_call` returns a failed promise. This occurs when:
- The recipient contract's `ft_on_transfer` panics (e.g., a contract that rejects the call, or one that is upgraded to panic after the source-chain transfer was initiated).
- The token contract itself panics during the transfer (e.g., due to a transient state or a bug).

Any user who specifies a non-empty `msg` in their cross-chain transfer is exposed. A user targeting a contract that panics in `ft_on_transfer` will have their funds permanently locked. Because the `msg` field is user-supplied on the source chain and the NEAR side has no way to validate the recipient contract's behavior before finalizing, this is reachable by any bridge user.

### Recommendation
In `is_refund_required`, treat a failed `ft_transfer_call` promise (`Err`) as a refund case, not a success case:

```rust
Err(_) => true,  // ft_transfer_call failed: revert finalization so transfer can be retried
```

This ensures that if token delivery fails, `fin_transfer_send_tokens_callback` takes the refund branch, removes the finalization record, and allows the relayer to retry the transfer after the underlying issue is resolved.

### Proof of Concept
1. User initiates a transfer from EVM → NEAR with a non-empty `msg` field, targeting a recipient contract `R`.
2. Relayer calls `fin_transfer` on the NEAR bridge; `fin_transfer_callback` calls `process_fin_transfer_to_near`.
3. `add_fin_transfer` inserts the transfer ID into `finalised_transfers`. [4](#0-3) 
4. `send_tokens` issues `ft_transfer_call(R, amount, msg)`.
5. `R.ft_on_transfer` panics; NEAR runtime returns tokens to the bridge and marks the promise as `Failed`.
6. `fin_transfer_send_tokens_callback` is invoked; `is_refund_required(true)` hits the `Err(_) => false` branch. [5](#0-4) 
7. The `else` branch emits `FinTransferEvent` and returns without removing the finalization record. [6](#0-5) 
8. Transfer ID is permanently finalized; recipient received nothing; tokens are locked in the bridge with no recovery mechanism.

### Citations

**File:** near/omni-bridge/src/lib.rs (L1719-1746)
```rust
        } else {
            // Send fee to the fee recipient
            if transfer_message.fee.fee.0 > 0 {
                if self.is_deployed_token(&token) {
                    ext_token::ext(token)
                        .with_static_gas(MINT_TOKEN_GAS)
                        .mint(fee_recipient.clone(), transfer_message.fee.fee, None)
                        .detach();
                } else {
                    ext_token::ext(token)
                        .with_attached_deposit(ONE_YOCTO)
                        .with_static_gas(FT_TRANSFER_GAS)
                        .ft_transfer(fee_recipient.clone(), transfer_message.fee.fee, None)
                        .detach();
                }
            }

            if transfer_message.fee.native_fee.0 > 0 {
                let native_token_id = self.get_native_token_id(transfer_message.get_origin_chain());

                ext_token::ext(native_token_id)
                    .with_static_gas(MINT_TOKEN_GAS)
                    .mint(fee_recipient.clone(), transfer_message.fee.native_fee, None)
                    .detach();
            }

            env::log_str(&OmniBridgeEvent::FinTransferEvent { transfer_message }.to_log_string());
        }
```

**File:** near/omni-bridge/src/lib.rs (L1783-1804)
```rust
impl Contract {
    fn is_refund_required(is_ft_transfer_call: bool) -> bool {
        if is_ft_transfer_call {
            match env::promise_result_checked(0, MAX_FT_TRANSFER_CALL_RESULT) {
                Ok(value) => {
                    if let Ok(amount) = near_sdk::serde_json::from_slice::<U128>(&value) {
                        // Normal case: refund if the used token amount is zero
                        // The amount can be zero if the `ft_on_transfer` in the receiver contract returns an amount instead of `0`, or if it panics.
                        amount.0 == 0
                    } else {
                        // Unexpected case: don't refund
                        false
                    }
                }
                // Unexpected case: don't refund
                Err(_) => false,
            }
        } else {
            // Not ft_transfer_call: don't refund
            false
        }
    }
```

**File:** near/omni-bridge/src/lib.rs (L1875-1875)
```rust
        let mut required_balance = self.add_fin_transfer(&transfer_message.get_transfer_id());
```

**File:** near/omni-bridge/src/lib.rs (L2226-2234)
```rust
    fn add_fin_transfer(&mut self, transfer_id: &TransferId) -> NearToken {
        let storage_usage = env::storage_usage();
        require!(
            self.finalised_transfers.insert(transfer_id),
            BridgeError::TransferAlreadyFinalised.as_ref()
        );
        env::storage_byte_cost()
            .saturating_mul((env::storage_usage().saturating_sub(storage_usage)).into())
    }
```
