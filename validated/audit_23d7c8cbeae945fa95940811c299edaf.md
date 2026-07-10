### Title
Partial `ft_transfer_call` Rejection in `OmniToken::mint` Permanently Locks Bridged Tokens in Bridge Contract — (`near/omni-token/src/lib.rs`)

---

### Summary

When `OmniToken::mint` is called with a non-empty `msg`, it first mints tokens to `env::predecessor_account_id()` (the bridge contract) rather than to the intended `account_id` recipient, then forwards them via `ft_transfer_call`. The bridge's callback (`fin_transfer_send_tokens_callback`) only handles the **all-or-nothing** case: it burns tokens only when `used_amount == 0` (full rejection). If the recipient's `ft_on_transfer` returns a **partial** refund (`0 < refund_amount < amount`), the refunded tokens are silently stranded in the bridge contract with no recovery path, permanently breaking bridge collateralization for deployed tokens.

---

### Finding Description

**Step 1 — Wrong initial deposit target in `OmniToken::mint`** [1](#0-0) 

When `msg` is `Some`, the token contract mints `amount` to `env::predecessor_account_id()` (the bridge contract), not to `account_id`. It then calls `ft_transfer_call(account_id, amount, None, msg)` to forward the tokens. This is the direct analog of the external report's pattern: the accounting state is updated for the wrong account (bridge contract instead of the intended recipient), and a subsequent action is expected to correct it.

**Step 2 — `send_tokens` triggers this path for any non-empty message** [2](#0-1) 

The bridge calls `mint(recipient, amount, Some(msg))` whenever the transfer message carries a non-empty `msg`. The `msg` field is fully user-controlled (set by the original sender on the source chain).

**Step 3 — `is_refund_required` only detects full rejection** [3](#0-2) 

`is_refund_required` returns `true` only when `used_amount == 0`. A partial refund (`used_amount > 0` but `< amount`) returns `false`, so the callback treats the transfer as fully successful.

**Step 4 — Callback does not burn or re-lock the partially refunded tokens** [4](#0-3) 

When `is_refund_required` is `false`, neither `burn_tokens_if_needed` nor `revert_lock_actions` is called. The `refund_amount` tokens that were returned to the bridge contract by `ft_resolve_transfer` are never burned and never re-locked. They accumulate silently in the bridge contract's token balance.

---

### Impact Explanation

**Critical — Permanent freezing of bridged assets and broken collateralization.**

For **deployed (bridged) tokens**: `refund_amount` tokens are minted but never burned. The on-chain total supply exceeds the amount locked on the origin chain by exactly `refund_amount`. This is unbacked supply — a direct break of bridge collateralization. The stuck tokens have no recovery path (no admin sweep function exists).

For **non-locked (native NEAR) tokens**: `refund_amount` tokens are transferred out of the bridge's custody but returned without updating `locked_tokens`. The bridge's accounting permanently under-counts its own holdings for that token.

In both cases the user who initiated the cross-chain transfer loses the `refund_amount` portion of their funds irrecoverably.

---

### Likelihood Explanation

- The `msg` field in `InitTransferMsg` is freely set by any user on the source chain.
- Any NEAR contract recipient that implements `ft_on_transfer` and returns a non-zero partial refund (e.g., a DEX/AMM that only accepts up to a liquidity cap, a vault with a deposit limit, or any contract that conditionally accepts tokens) triggers this path.
- No privileged access is required; any bridge user initiating a transfer with a message to a contract recipient is sufficient.

---

### Recommendation

In `fin_transfer_send_tokens_callback`, extend the refund/burn logic to handle partial refunds. Read the actual `used_amount` from the promise result and compute `refund_amount = amount_without_fee - used_amount`. If `refund_amount > 0`, call `burn_tokens_if_needed(token, refund_amount)` (for deployed tokens) and restore `locked_tokens` proportionally (for non-deployed tokens). The `is_refund_required` helper should be replaced with a function that returns the actual refunded amount rather than a boolean.

Alternatively, mirror the fix suggested in the external report: change `OmniToken::mint` to deposit directly to `account_id` when `msg` is `Some`, and use a separate internal mechanism to initiate `ft_transfer_call` from `account_id`'s balance — though this requires storage registration for `account_id` to already exist.

---

### Proof of Concept

1. Alice initiates a transfer on Ethereum of 1000 USDC to NEAR, with `msg = '{"action":"swap"}'` targeting a DEX contract `dex.near` as recipient.
2. The bridge finalizes the transfer: `send_tokens("usdc.bridge.near", "dex.near", 1000, '{"action":"swap"}')`.
3. `send_tokens` calls `mint("dex.near", 1000, Some('{"action":"swap"}'))` on the token contract.
4. `OmniToken::mint` executes `internal_deposit(&bridge_contract, 1000)` then `ft_transfer_call("dex.near", 1000, None, '{"action":"swap"}')`.
5. `dex.near`'s `ft_on_transfer` accepts 600 tokens (within its liquidity cap) and returns `400` as refund.
6. `ft_resolve_transfer` refunds 400 tokens to the bridge contract and returns `used_amount = 600`.
7. `fin_transfer_send_tokens_callback` is called. `is_refund_required` returns `false` (600 ≠ 0).
8. No burn is triggered. The bridge contract now holds 400 USDC tokens permanently.
9. Alice received only 600 USDC instead of 1000. The 400 USDC are irrecoverable. The deployed token's total supply is inflated by 400 with no corresponding locked collateral on Ethereum.

### Citations

**File:** near/omni-token/src/lib.rs (L135-143)
```rust
        if let Some(msg) = msg {
            self.token
                .internal_deposit(&env::predecessor_account_id(), amount.into());

            self.ft_transfer_call(account_id, amount, None, msg)
        } else {
            self.token.internal_deposit(&account_id, amount.into());
            PromiseOrValue::Value(amount)
        }
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

**File:** near/omni-bridge/src/lib.rs (L1784-1803)
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
```

**File:** near/omni-bridge/src/lib.rs (L2094-2101)
```rust
            ext_token::ext(token)
                .with_attached_deposit(deposit)
                .with_static_gas(MINT_TOKEN_GAS.saturating_add(ft_transfer_call_gas))
                .mint(
                    recipient,
                    amount,
                    (!msg.is_empty()).then(|| msg.to_string()),
                )
```
