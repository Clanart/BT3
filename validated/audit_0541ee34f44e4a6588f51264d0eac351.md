### Title
Silent MPC Signing Failure in `sign_transfer_callback` Leaves User Funds Permanently Locked - (File: `near/omni-bridge/src/lib.rs`)

### Summary

`sign_transfer_callback` silently discards MPC signing errors. When the MPC call fails, the callback does nothing: no error is logged, no event is emitted, and the transfer message is never removed from `pending_transfers`. The user's locked tokens have no user-callable recovery path.

### Finding Description

The `sign_transfer` flow is:

1. User deposits tokens via `ft_on_transfer` → `init_transfer` → tokens locked, `TransferMessage` inserted into `pending_transfers`.
2. A trusted relayer calls `sign_transfer`, which fires an MPC cross-contract call and chains `sign_transfer_callback`.

`sign_transfer_callback` handles only the success branch:

```rust
// near/omni-bridge/src/lib.rs:648-668
#[private]
pub fn sign_transfer_callback(
    &mut self,
    #[callback_result] call_result: Result<SignatureResponse, PromiseError>,
    #[serializer(borsh)] message_payload: TransferMessagePayload,
    #[serializer(borsh)] fee: &Fee,
) {
    if let Ok(signature) = call_result {          // Err branch: nothing happens
        if fee.is_zero() {
            self.remove_transfer_message(message_payload.transfer_id);
        }
        env::log_str(
            &OmniBridgeEvent::SignTransferEvent { signature, message_payload }
                .to_log_string(),
        );
    }
}
```

When `call_result` is `Err`:
- No error is logged.
- No `SignTransferEvent` is emitted (off-chain relayers/indexers receive no signal).
- `remove_transfer_message` is never called, so the `TransferMessage` stays in `pending_transfers` indefinitely.
- The user's tokens remain locked in the bridge contract.

The only recovery path is for a trusted relayer to retry `sign_transfer` with the same `transfer_id`. The user has no callable function to cancel or refund a stuck transfer. [1](#0-0) 

The MPC call is dispatched here: [2](#0-1) 

### Impact Explanation

If the MPC signing call fails persistently for a given transfer (e.g., due to a malformed payload for a specific token/chain combination, MPC node unavailability, or gas exhaustion in the callback), the user's tokens are permanently locked. There is no user-accessible `cancel_transfer` or refund function. The `sign_transfer` entry point is `#[trusted_relayer]`-gated, so the user cannot self-rescue.

This matches: **Critical/High — Permanent freezing or irrecoverable lock of user funds in bridge flows.** [3](#0-2) 

### Likelihood Explanation

MPC signing calls can fail transiently (network, gas) or permanently (payload encoding edge cases, key rotation). Because the error is fully silent — no event, no log — the failure is invisible to off-chain monitoring. A relayer that does not actively poll `pending_transfers` will never know a retry is needed. For any transfer where the MPC call fails and no relayer retries, the user's funds are locked with no on-chain recourse.

### Recommendation

Handle the `Err` branch explicitly in `sign_transfer_callback`:

```rust
if let Ok(signature) = call_result {
    // existing success logic
} else {
    // Log the failure so off-chain systems can detect and retry
    env::log_str(&format!(
        "MPC signing failed for transfer {:?}; relayer must retry sign_transfer",
        message_payload.transfer_id
    ));
    // Optionally emit a structured event for indexers
}
```

Additionally, consider adding a user-callable `cancel_transfer` function that allows the original sender to reclaim locked tokens when a transfer has been pending beyond a timeout threshold, providing a trustless recovery path independent of relayer liveness.

### Proof of Concept

1. User calls `ft_transfer_call` on a NEP-141 token, routing to `ft_on_transfer` → `init_transfer`. Tokens are locked; a `TransferMessage` is stored in `pending_transfers` with `transfer_id = T`.
2. Trusted relayer calls `sign_transfer(T, ...)`. The MPC cross-contract call to `ext_signer::sign(...)` fails (e.g., MPC node returns an error, or gas in the callback is insufficient).
3. `sign_transfer_callback` receives `call_result = Err(...)`. The `if let Ok` branch is skipped entirely. No log, no event, no state change.
4. `pending_transfers` still contains the entry for `T`. The user's tokens remain locked.
5. No user-callable function exists to remove the entry or reclaim tokens. The user is permanently locked out unless a trusted relayer retries — with no on-chain signal that a retry is needed. [4](#0-3)

### Citations

**File:** near/omni-bridge/src/lib.rs (L444-452)
```rust
    #[payable]
    #[trusted_relayer]
    #[pause(except(roles(Role::DAO)))]
    pub fn sign_transfer(
        &mut self,
        transfer_id: TransferId,
        fee_recipient: Option<AccountId>,
        fee: &Option<Fee>,
    ) -> Promise {
```

**File:** near/omni-bridge/src/lib.rs (L508-521)
```rust
        ext_signer::ext(self.mpc_signer.clone())
            .with_static_gas(MPC_SIGNING_GAS)
            .with_attached_deposit(env::attached_deposit())
            .sign(SignRequest {
                payload,
                path: SIGN_PATH.to_owned(),
                key_version: 0,
            })
            .then(
                Self::ext(env::current_account_id())
                    .with_static_gas(SIGN_TRANSFER_CALLBACK_GAS)
                    .sign_transfer_callback(transfer_payload, &transfer_message.fee),
            )
    }
```

**File:** near/omni-bridge/src/lib.rs (L648-668)
```rust
    #[private]
    pub fn sign_transfer_callback(
        &mut self,
        #[callback_result] call_result: Result<SignatureResponse, PromiseError>,
        #[serializer(borsh)] message_payload: TransferMessagePayload,
        #[serializer(borsh)] fee: &Fee,
    ) {
        if let Ok(signature) = call_result {
            if fee.is_zero() {
                self.remove_transfer_message(message_payload.transfer_id);
            }

            env::log_str(
                &OmniBridgeEvent::SignTransferEvent {
                    signature,
                    message_payload,
                }
                .to_log_string(),
            );
        }
    }
```
