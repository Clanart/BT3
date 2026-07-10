Looking at the external report's vulnerability class — **callback/state desync**: a transfer enters a pending/retriable state, but subsequent state changes (pause, ban, suspension) are not rechecked when the transfer is retried/resumed — I need to find the analog in the Omni Bridge NEAR contract.

The key finding is in `near/omni-bridge/src/lib.rs`: the `init_transfer_resume` yield-callback function does not check the contract's pause state, while the entry point `ft_on_transfer` (which calls `init_transfer`) does. [1](#0-0) [2](#0-1) [3](#0-2) 

---

### Title
`init_transfer_resume` Bypasses Pause State Check, Allowing Transfer Initiation During Emergency Pause — (File: `near/omni-bridge/src/lib.rs`)

---

### Summary

The `init_transfer_resume` yield-callback in the NEAR omni-bridge contract does not check the contract's pause state before executing. When a transfer is placed in a yield state (waiting for storage) and the contract is subsequently paused, the resume function still executes and initiates the transfer, bypassing the pause mechanism and locking user funds in the bridge.

---

### Finding Description

`ft_on_transfer` is decorated with `#[pause(except(roles(Role::DAO, Role::UnrestrictedDeposit)))]`, preventing new transfers from being initiated when the contract is paused: [1](#0-0) 

However, when storage is insufficient, `init_transfer` creates a yield promise via `env::promise_yield_create("init_transfer_resume", ...)`: [2](#0-1) 

The `init_transfer_resume` callback is only decorated with `#[private]` — it has **no pause check**: [3](#0-2) 

If the contract is paused after the yield is created but before `init_transfer_resume` is called, the resume function still executes and calls `init_transfer_internal`, initiating the transfer and locking the tokens in the bridge. The correct behavior when paused would be to return `transfer_message.amount` (refunding the tokens to the user), but this check is absent.

This is the direct structural analog to the external report: just as `retryMessage()` fails to recheck the suspension/ban state when retrying a RETRIABLE message, `init_transfer_resume` fails to recheck the pause state when resuming a yielded transfer.

---

### Impact Explanation

- Transfer initiation proceeds even when the contract is paused, bypassing the emergency pause mechanism.
- User tokens are locked in the bridge instead of being refunded during a pause.
- The `InitTransferEvent` is emitted, triggering off-chain relayer actions even during an emergency.
- If the pause is indefinite (e.g., due to a critical vulnerability requiring contract replacement), user funds are permanently locked in the bridge with no recovery path, since the yield-resume path is the only refund mechanism for this flow.

This fits: **Critical — Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows.**

---

### Likelihood Explanation

- The yield/resume pattern is triggered whenever a user initiates a transfer without sufficient pre-deposited storage balance — a common scenario.
- An attacker can deliberately trigger the yield state by not pre-depositing storage, then wait for any pause event (emergency or otherwise).
- The attacker (or any third party) can then trigger the resume by depositing storage to the `message_storage_account_id` account, causing `init_transfer_resume` to execute despite the pause.
- This is fully reachable by an unprivileged bridge user with no special permissions.

---

### Recommendation

Add an explicit pause check at the beginning of `init_transfer_resume` that returns the full token amount (triggering a refund) when the contract is paused:

```rust
#[private]
pub fn init_transfer_resume(
    &mut self,
    transfer_message: TransferMessage,
    message_storage_account_id: AccountId,
    storage_owner: AccountId,
    #[callback_result] response: Result<(), PromiseError>,
) -> U128 {
    self.remove_promise(&message_storage_account_id);

    // Recheck pause state — refund tokens if contract is now paused
    if self.pa_is_paused() {
        env::log_str("Init transfer resume aborted: contract is paused");
        return transfer_message.amount; // triggers refund via ft_on_transfer return value
    }

    if response.is_err() {
        env::log_str("Init transfer resume timeout");
    }

    if let Err(err) = self.try_to_transfer_balance_from_message_account(...) {
        ...
        return transfer_message.amount;
    }

    self.init_transfer_internal(transfer_message, storage_owner)
}
```

---

### Proof of Concept

1. User calls `ft_on_transfer` with a transfer message when the contract is **not** paused.
2. `init_transfer` is called, but storage is insufficient — a yield promise is created via `env::promise_yield_create("init_transfer_resume", ...)`. [2](#0-1) 
3. The DAO pauses the contract (e.g., due to a security incident discovered in the bridge).
4. The attacker (or any party) deposits storage to the `message_storage_account_id` account, resolving the yield and triggering `init_transfer_resume`.
5. `init_transfer_resume` executes **without checking the pause state**: [3](#0-2) 
6. `init_transfer_internal` is called, locking the tokens in the bridge and emitting `InitTransferEvent`.
7. The transfer is initiated despite the contract being paused. User tokens are locked in the bridge with no refund path while the contract remains paused.
8. If the pause is indefinite, the funds are permanently irrecoverable.

### Citations

**File:** near/omni-bridge/src/lib.rs (L252-253)
```rust
    #[pause(except(roles(Role::DAO, Role::UnrestrictedDeposit)))]
    pub fn ft_on_transfer(&mut self, sender_id: AccountId, amount: U128, msg: String) {
```

**File:** near/omni-bridge/src/lib.rs (L586-617)
```rust
            let promise_index = env::promise_yield_create(
                "init_transfer_resume",
                json!({
                    "transfer_message": transfer_message,
                    "message_storage_account_id": message_storage_account_id,
                    "storage_owner": signer_id,
                })
                .to_string()
                .as_bytes(),
                INIT_TRANSFER_RESUME_GAS,
                GasWeight(0),
                PROMISE_REGISTER_ID,
            );

            let yield_id: CryptoHash = env::read_register(PROMISE_REGISTER_ID)
                .near_expect(BridgeError::ReadPromiseRegister)
                .try_into()
                .near_expect(BridgeError::ReadPromiseYieldId);

            let required_storage_balance = self.add_promise(&message_storage_account_id, &yield_id);

            self.update_storage_balance(
                env::current_account_id(),
                required_storage_balance,
                NearToken::from_yoctonear(0),
            );

            env::log_str(&format!(
                "Yield init transfer until storage is available at {message_storage_account_id}"
            ));

            PromiseOrPromiseIndexOrValue::PromiseIndex(promise_index)
```

**File:** near/omni-bridge/src/lib.rs (L621-646)
```rust
    #[private]
    #[allow(clippy::needless_pass_by_value)]
    pub fn init_transfer_resume(
        &mut self,
        transfer_message: TransferMessage,
        message_storage_account_id: AccountId,
        storage_owner: AccountId,
        #[callback_result] response: Result<(), PromiseError>,
    ) -> U128 {
        self.remove_promise(&message_storage_account_id);
        if response.is_err() {
            env::log_str("Init transfer resume timeout");
        }

        if let Err(err) = self.try_to_transfer_balance_from_message_account(
            &message_storage_account_id,
            NearToken::from_yoctonear(transfer_message.fee.native_fee.0),
            &storage_owner,
            self.required_balance_for_init_transfer_message(transfer_message.clone()),
        ) {
            env::log_str(&format!("Error paying native fee and storage: {err}"));
            return transfer_message.amount;
        }

        self.init_transfer_internal(transfer_message, storage_owner)
    }
```
