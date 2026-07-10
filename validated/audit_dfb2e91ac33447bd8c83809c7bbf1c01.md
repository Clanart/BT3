I have all the code needed to trace the full call path. Let me synthesize the findings.

**Key code locations traced:**

`process_fin_transfer_to_near` (lines 1881–1885): `unlock_tokens_if_needed` is called unconditionally, decrementing `locked_tokens` before the token send. [1](#0-0) 

`process_fin_transfer_to_near` (lines 1957–1977): `send_tokens` is called, then `.then(fin_transfer_send_tokens_callback(..., !msg.is_empty(), ..., lock_actions))`. When `msg` is empty, `is_ft_transfer_call = false`. [2](#0-1) 

`fin_transfer_send_tokens_callback` (lines 1702–1714): `revert_lock_actions` is only called when `is_refund_required(is_ft_transfer_call)` returns `true`. [3](#0-2) 

`is_refund_required` (lines 1784–1804): When `is_ft_transfer_call` is `false`, the function returns `false` **without inspecting the promise result at all**. [4](#0-3) 

`revert_lock_actions` in token_lock.rs: re-locks tokens on `LockAction::Unlocked`, restoring the counter. [5](#0-4) 

---

### Title
`locked_tokens` permanently under-decremented when plain `ft_transfer` fails in `fin_transfer` — (`near/omni-bridge/src/lib.rs`)

### Summary

When a `fin_transfer` targets a NEAR recipient with an empty `msg`, the bridge uses a plain `ft_transfer` (not `ft_transfer_call`). If that `ft_transfer` fails (e.g., recipient storage not registered, token contract panic), `fin_transfer_send_tokens_callback` is invoked with `is_ft_transfer_call = false`. `is_refund_required(false)` unconditionally returns `false` without reading the promise result, so `revert_lock_actions` is never called. The `locked_tokens` decrement performed by `unlock_tokens_if_needed` is never reversed, and the transfer remains marked as finalized.

### Finding Description

**Step 1 — `unlock_tokens_if_needed` decrements before delivery:** [1](#0-0) 

**Step 2 — callback flag is `false` for empty `msg`:** [6](#0-5) 

**Step 3 — `is_refund_required` short-circuits to `false` without checking the promise:** [7](#0-6) 

**Step 4 — `revert_lock_actions` is never reached:** [3](#0-2) 

**Step 5 — `add_fin_transfer` already marked the transfer finalized at line 1875, so no retry is possible:** [8](#0-7) 

The `ft_transfer_call` path is handled correctly: `is_refund_required` reads `env::promise_result_checked` and returns `true` on failure, triggering `revert_lock_actions`. The plain `ft_transfer` path has no equivalent check. [9](#0-8) 

### Impact Explanation

Two concrete impacts:

1. **Permanent freeze of user funds (Critical):** The transfer is finalized (replay-protected), the `ft_transfer` failed so the recipient received nothing, and the tokens remain in the bridge with no recovery path. The origin-chain proof is consumed; the user cannot re-submit.

2. **`locked_tokens` accounting corruption (High):** `locked_tokens[(origin_chain, token)]` is decremented by `amount` even though the tokens were not released. The counter now under-represents actual collateral held. Repeated occurrences drive the counter toward zero, causing subsequent legitimate `unlock_tokens_if_needed` calls to revert with `InsufficientLockedTokens`, permanently blocking all future `fin_transfer` completions for that token/chain pair — a protocol-wide DoS on that token's bridge path. [10](#0-9) 

### Likelihood Explanation

`ft_transfer` can fail when:
- The recipient's storage registration was removed between the storage-deposit check and the actual transfer (NEAR storage is unregisterable at any time).
- The token contract is paused or has a custom guard.
- Gas exhaustion in the token contract.

The storage check in `fin_transfer` happens in a prior promise step; the actual `ft_transfer` executes in a later step, leaving a window. This is not purely theoretical — any token with a pausable or custom `ft_transfer` makes this trivially triggerable. Even for standard NEP-141 tokens, storage deregistration between steps is a realistic race.

### Recommendation

In `is_refund_required`, check the promise result for the non-`ft_transfer_call` path as well:

```rust
fn is_refund_required(is_ft_transfer_call: bool) -> bool {
    if is_ft_transfer_call {
        // existing ft_transfer_call logic ...
    } else {
        // ft_transfer: treat any failure as requiring revert
        env::promise_result_checked(0, 0).is_err()
    }
}
```

This ensures `revert_lock_actions` is called and `locked_tokens` is restored whenever `ft_transfer` fails, mirroring the existing `ft_transfer_call` failure handling. [4](#0-3) 

### Proof of Concept

```
Initial state: locked_tokens[(Eth, token)] = 1000

1. Attacker submits valid fin_transfer proof (Eth→NEAR, amount=1000, recipient=R, msg="")
2. fin_transfer_callback → process_fin_transfer_to_near
3. unlock_tokens_if_needed(Eth, token, 1000) → locked_tokens[(Eth, token)] = 0
4. send_tokens → ft_transfer(R, 1000)  [R has no storage → ft_transfer panics]
5. fin_transfer_send_tokens_callback(is_ft_transfer_call=false)
6. is_refund_required(false) → false  [promise result never read]
7. revert_lock_actions NOT called
8. else branch: fee sent, FinTransferEvent logged

Final state:
  locked_tokens[(Eth, token)] = 0   ← should be 1000
  R received 0 tokens               ← tokens stuck in bridge
  transfer marked finalized          ← no retry possible
  
Repeat with amount=1 to drive locked_tokens to 0 from any starting value,
then all subsequent fin_transfers for this token revert with InsufficientLockedTokens.
```

### Citations

**File:** near/omni-bridge/src/lib.rs (L1702-1714)
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
```

**File:** near/omni-bridge/src/lib.rs (L1784-1804)
```rust
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

**File:** near/omni-bridge/src/lib.rs (L1881-1885)
```rust
        let lock_actions = vec![self.unlock_tokens_if_needed(
            transfer_message.get_origin_chain(),
            &token,
            transfer_message.amount.0,
        )];
```

**File:** near/omni-bridge/src/lib.rs (L1957-1977)
```rust
        self.send_tokens(
            token.clone(),
            recipient,
            U128(
                transfer_message
                    .amount_without_fee()
                    .near_expect(BridgeError::InvalidFee),
            ),
            &msg,
        )
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

**File:** near/omni-bridge/src/token_lock.rs (L81-84)
```rust
        require!(
            available >= amount,
            TokenLockError::InsufficientLockedTokens.as_ref()
        );
```

**File:** near/omni-bridge/src/token_lock.rs (L122-142)
```rust
    pub fn revert_lock_actions(&mut self, lock_actions: &[LockAction]) {
        for lock_action in lock_actions {
            match lock_action {
                LockAction::Locked {
                    chain_kind,
                    token_id,
                    amount,
                } => {
                    self.unlock_tokens(*chain_kind, token_id, *amount);
                }
                LockAction::Unlocked {
                    chain_kind,
                    token_id,
                    amount,
                } => {
                    self.lock_tokens(*chain_kind, token_id, *amount);
                }
                LockAction::Unchanged => {}
            }
        }
    }
```
