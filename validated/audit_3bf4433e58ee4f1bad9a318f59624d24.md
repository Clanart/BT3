### Title
Unchecked `ft_transfer` Result in `fin_transfer_send_tokens_callback` Causes Permanent Fund Lock — (File: near/omni-bridge/src/lib.rs)

---

### Summary

When finalizing a cross-chain transfer to a NEAR recipient for a **non-deployed (native) token with no message**, the NEAR bridge calls `ft_transfer` and chains a callback (`fin_transfer_send_tokens_callback`). The callback has **no `#[callback_result]` parameter** and does not inspect the promise result for the `ft_transfer` path. If `ft_transfer` panics (e.g., recipient lacks storage registration), the callback still executes the success branch: it emits `FinTransferEvent` and pays fees, while the destination nonce is already permanently consumed. The user's tokens are irrecoverably locked.

---

### Finding Description

In `send_tokens` (lib.rs:2102–2106), when the token is a non-deployed native token and `msg` is empty, the bridge issues a plain `ft_transfer`:

```rust
} else if msg.is_empty() {
    ext_token::ext(token)
        .with_attached_deposit(ONE_YOCTO)
        .with_static_gas(FT_TRANSFER_GAS)
        .ft_transfer(recipient, amount, None)
```

This promise is chained to `fin_transfer_send_tokens_callback` (lib.rs:1967–1977):

```rust
.then(
    Self::ext(env::current_account_id())
        .with_static_gas(SEND_TOKENS_CALLBACK_GAS)
        .fin_transfer_send_tokens_callback(
            transfer_message,
            &fee_recipient,
            !msg.is_empty(),   // ← false for ft_transfer path
            predecessor_account_id,
            lock_actions,
        ),
)
```

The callback signature (lib.rs:1692–1699) carries **no `#[callback_result]`**:

```rust
pub fn fin_transfer_send_tokens_callback(
    &mut self,
    #[serializer(borsh)] transfer_message: TransferMessage,
    #[serializer(borsh)] fee_recipient: &AccountId,
    #[serializer(borsh)] is_ft_transfer_call: bool,   // false here
    #[serializer(borsh)] storage_owner: &AccountId,
    #[serializer(borsh)] lock_actions: Vec<LockAction>,
) {
```

Inside the callback (lib.rs:1702), the only branch guard is:

```rust
if Self::is_refund_required(is_ft_transfer_call) {
    // refund / revert path
} else {
    // success path: send fees, emit FinTransferEvent
}
```

`is_ft_transfer_call` is `false` for the `ft_transfer` path, so `is_refund_required(false)` returns `false` and the **success branch always executes**, regardless of whether `ft_transfer` actually succeeded or panicked.

In NEAR's async execution model, when a cross-contract call panics:
- The callee's state changes are rolled back.
- The **caller's state changes committed before the call are NOT rolled back** — the destination nonce was already marked used.
- The callback is still invoked.

Because the callback does not read `env::promise_result(0)`, it cannot distinguish success from failure and unconditionally emits `FinTransferEvent` and pays fees.

---

### Impact Explanation

**Permanent freezing / irrecoverable lock of user funds.**

- The destination nonce is marked used before `send_tokens` is called; it cannot be replayed.
- If `ft_transfer` panics, the recipient receives nothing.
- The `FinTransferEvent` is emitted, signalling to off-chain indexers that the transfer completed.
- There is no recovery path: the nonce is consumed, the transfer is considered finalized, and the locked tokens on the origin chain cannot be reclaimed.

---

### Likelihood Explanation

`ft_transfer` in NEAR panics when the recipient account has not registered storage for the token. This is a realistic condition for:
- First-time recipients of a given token who have not pre-registered storage.
- Tokens whose storage deposit was not included or was insufficient in the `storage_deposit_actions` array passed to `fin_transfer`.
- Any token contract with custom rejection logic.

A relayer submitting `fin_transfer` with an under-funded or missing storage deposit action triggers this path for any native (non-deployed) token transfer with no message.

---

### Recommendation

Add `#[callback_result]` to `fin_transfer_send_tokens_callback` and check the promise result for **all** transfer paths, not only `ft_transfer_call`. On failure, revert the nonce (or store the transfer for retry) and restore any lock actions, mirroring the existing refund logic:

```rust
pub fn fin_transfer_send_tokens_callback(
    &mut self,
    #[callback_result] call_result: Result<(), PromiseError>,  // add this
    #[serializer(borsh)] transfer_message: TransferMessage,
    ...
) {
    if call_result.is_err() || Self::is_refund_required(is_ft_transfer_call) {
        // existing refund / revert path
    } else {
        // success path
    }
}
```

---

### Proof of Concept

1. User on EVM initiates a transfer of a native ERC-20 (e.g., USDC) to a NEAR account `alice.near` that has never registered storage for the NEAR-side USDC token.
2. Relayer calls `fin_transfer` on the NEAR bridge with a valid MPC signature. The destination nonce `N` is marked used.
3. `send_tokens` issues `ft_transfer(alice.near, amount, None)`. The USDC token contract panics because `alice.near` has no storage deposit.
4. `fin_transfer_send_tokens_callback` is invoked. `is_ft_transfer_call = false`, so `is_refund_required(false)` returns `false`.
5. The callback emits `FinTransferEvent` and pays the fee recipient. No error is detected.
6. Nonce `N` is permanently consumed. The transfer cannot be re-submitted. `alice.near` receives nothing. The EVM-side tokens are gone.

---

**Root cause lines:** [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** near/omni-bridge/src/lib.rs (L1692-1703)
```rust
    pub fn fin_transfer_send_tokens_callback(
        &mut self,
        #[serializer(borsh)] transfer_message: TransferMessage,
        #[serializer(borsh)] fee_recipient: &AccountId,
        #[serializer(borsh)] is_ft_transfer_call: bool,
        #[serializer(borsh)] storage_owner: &AccountId,
        #[serializer(borsh)] lock_actions: Vec<LockAction>,
    ) {
        let token = self.get_token_id(&transfer_message.token);

        if Self::is_refund_required(is_ft_transfer_call) {
            self.burn_tokens_if_needed(
```

**File:** near/omni-bridge/src/lib.rs (L1967-1977)
```rust
        .then(
            Self::ext(env::current_account_id())
                .with_static_gas(SEND_TOKENS_CALLBACK_GAS)
                .fin_transfer_send_tokens_callback(
                    transfer_message,
                    &fee_recipient,
                    !msg.is_empty(),
                    predecessor_account_id,
                    lock_actions,
                ),
        )
```

**File:** near/omni-bridge/src/lib.rs (L2102-2107)
```rust
        } else if msg.is_empty() {
            ext_token::ext(token)
                .with_attached_deposit(ONE_YOCTO)
                .with_static_gas(FT_TRANSFER_GAS)
                .ft_transfer(recipient, amount, None)
        } else {
```
