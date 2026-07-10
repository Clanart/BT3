### Title
Detached `send_tokens` in fast-transfer finalization paths causes irrecoverable loss of relayer funds - (File: near/omni-bridge/src/lib.rs)

### Summary
In `process_fin_transfer_to_other_chain` and `utxo_fin_transfer_fast`, the bridge finalizes all state (marks transfer as finalized, decrements locked tokens, marks fast transfer as finalized) and then calls `send_tokens(...).detach()` to repay the fast-transfer relayer. Because the promise is detached, a failure of the token delivery is silently ignored. The state is permanently committed with no recovery path, causing the relayer's bridged funds to be irrecoverably frozen. The developers themselves flag this with a `// TODO: check how to deal with failed send_tokens` comment directly above the call site.

### Finding Description

**Vulnerability class:** Callback/state desync — state is committed before the asset delivery is confirmed, with no rollback path if delivery fails.

**Root cause — `process_fin_transfer_to_other_chain`:** [1](#0-0) 

`add_fin_transfer` inserts the transfer ID into `finalised_transfers` (replay protection). Then locked-token accounting is mutated: [2](#0-1) 

When a fast transfer exists, the relayer repayment is fire-and-forget: [3](#0-2) 

`.detach()` means the promise result is never inspected. If `send_tokens` fails (e.g., the relayer's account lacks storage for the token, or the `mint` call on the deployed-token contract reverts), the main transaction has already committed with:
- Transfer ID permanently in `finalised_transfers` — cannot be re-submitted.
- `locked_tokens` already decremented — accounting is permanently off.
- Fast transfer permanently marked `finalised: true` — cannot be retried.

The relayer who pre-funded the fast transfer receives nothing and has no recourse.

**Root cause — `utxo_fin_transfer_fast` (developer-acknowledged):** [4](#0-3) 

The same pattern: fast-transfer state is mutated (removed or marked finalised), then: [5](#0-4) 

The `// TODO: check how to deal with failed send_tokens` comment is a developer acknowledgment that this path has no error handling.

**Contrast with the `process_fin_transfer_to_near` path**, which correctly chains a callback: [6](#0-5) 

`fin_transfer_send_tokens_callback` handles failure by calling `revert_lock_actions` and `remove_fin_transfer`. The "other chain" path has no equivalent safety net.

**`send_tokens` failure modes:** [7](#0-6) 

- For deployed tokens: `mint` is called. If the relayer's account has been closed or lacks storage, the NEP-141 `mint` call reverts.
- For non-deployed tokens: `ft_transfer` is called with `ONE_YOCTO`. If the relayer's account lacks storage for the token, the transfer reverts.
- For wNEAR: `near_withdraw` + `near_withdraw_callback` — if the callback panics, the NEAR is lost.

In all cases the detached promise failure is invisible to the main transaction.

### Impact Explanation

The fast-transfer relayer pre-funds the recipient out of their own balance, trusting that `fin_transfer` will repay them. If `send_tokens` fails silently:

- The relayer's tokens are permanently frozen inside the bridge contract with no withdrawal mechanism.
- The transfer ID is consumed from `finalised_transfers`, so the proof cannot be re-submitted.
- The fast-transfer record is permanently marked finalised, so no retry path exists.

This is an irrecoverable lock of user/protocol funds in the bridge flow, matching the **Critical** impact tier: "Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows."

### Likelihood Explanation

The `send_tokens` call can fail when:
1. The relayer's NEAR account is deleted or its storage balance for the bridged token is removed between the fast-transfer submission and the finalization (which can span multiple blocks/epochs across chains).
2. The deployed-token contract's `mint` reverts due to a storage or access-control edge case.
3. Gas exhaustion inside the detached promise causes the promise to fail silently.

Likelihood is **low-to-medium**: the window exists across cross-chain finalization latency (minutes to hours), and the developer TODO comment confirms the team is aware the failure case is unhandled.

### Recommendation

Replace the detached call with a chained callback that mirrors the `process_fin_transfer_to_near` pattern:

```rust
// Instead of:
self.send_tokens(token, relayer, amount, "").detach();
self.mark_fast_transfer_as_finalised(&fast_transfer.id());

// Do:
self.send_tokens(token, relayer, amount, "")
    .then(
        Self::ext(env::current_account_id())
            .with_static_gas(RESOLVE_FAST_TRANSFER_REPAY_GAS)
            .resolve_fast_transfer_repay(
                &fast_transfer.id(),
                &token,
                amount,
                &predecessor_account_id,
                lock_actions,
            ),
    );
// Mark finalised only inside the success branch of the callback.
```

The callback should:
- On success: mark the fast transfer as finalised.
- On failure: call `revert_lock_actions`, remove the transfer from `finalised_transfers`, and return the tokens to the relayer.

Apply the same fix to

### Citations

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

**File:** near/omni-bridge/src/lib.rs (L1985-1985)
```rust
        let mut required_balance = self.add_fin_transfer(&transfer_message.get_transfer_id());
```

**File:** near/omni-bridge/src/lib.rs (L1997-2006)
```rust
        self.unlock_tokens_if_needed(
            transfer_message.get_origin_chain(),
            &token,
            transfer_message.amount.0,
        );
        self.lock_tokens_if_needed(
            transfer_message.get_destination_chain(),
            &token,
            transfer_message.fee.fee.into(),
        );
```

**File:** near/omni-bridge/src/lib.rs (L2028-2040)
```rust
        if let Some(relayer) = recipient {
            self.send_tokens(
                token,
                relayer,
                U128(
                    transfer_message
                        .amount_without_fee()
                        .near_expect(BridgeError::InvalidFee),
                ),
                "",
            )
            .detach();
            self.mark_fast_transfer_as_finalised(&fast_transfer.id());
```

**File:** near/omni-bridge/src/lib.rs (L2056-2117)
```rust
    fn send_tokens(
        &self,
        token: AccountId,
        recipient: AccountId,
        amount: U128,
        msg: &str,
    ) -> Promise {
        let ft_transfer_call_gas = env::prepaid_gas()
            .saturating_sub(env::used_gas())
            .saturating_sub(SEND_TOKENS_CALLBACK_GAS) // TODO: not all send_tokens callbacks has the same gas.
            .saturating_sub(MINT_TOKEN_GAS)
            .min(FT_TRANSFER_CALL_GAS);

        let is_deployed_token = self.is_deployed_token(&token);

        if token == self.wnear_account_id && msg.is_empty() {
            // Unwrap wNEAR and transfer NEAR tokens
            ext_wnear_token::ext(self.wnear_account_id.clone())
                .with_static_gas(WNEAR_WITHDRAW_GAS)
                .with_attached_deposit(ONE_YOCTO)
                .near_withdraw(amount)
                .then(
                    Self::ext(env::current_account_id())
                        .with_static_gas(NEAR_WITHDRAW_CALLBACK_GAS)
                        .near_withdraw_callback(recipient, NearToken::from_yoctonear(amount.0)),
                )
        } else if is_deployed_token {
            let deposit = if msg.is_empty() {
                NO_DEPOSIT
            } else {
                ONE_YOCTO
            };

            require!(
                ft_transfer_call_gas >= MIN_FT_TRANSFER_CALL_GAS,
                BridgeError::NotEnoughGasForTokenTransfer(ft_transfer_call_gas).as_ref()
            );

            ext_token::ext(token)
                .with_attached_deposit(deposit)
                .with_static_gas(MINT_TOKEN_GAS.saturating_add(ft_transfer_call_gas))
                .mint(
                    recipient,
                    amount,
                    (!msg.is_empty()).then(|| msg.to_string()),
                )
        } else if msg.is_empty() {
            ext_token::ext(token)
                .with_attached_deposit(ONE_YOCTO)
                .with_static_gas(FT_TRANSFER_GAS)
                .ft_transfer(recipient, amount, None)
        } else {
            require!(
                ft_transfer_call_gas >= MIN_FT_TRANSFER_CALL_GAS,
                BridgeError::NotEnoughGasForTokenTransfer(ft_transfer_call_gas).as_ref()
            );

            ext_token::ext(token)
                .with_attached_deposit(ONE_YOCTO)
                .with_static_gas(ft_transfer_call_gas)
                .ft_transfer_call(recipient, amount, None, msg.to_string())
        }
```

**File:** near/omni-bridge/src/lib.rs (L2483-2485)
```rust
        if let Some(status) = self.get_fast_transfer_status(&fast_transfer.id()) {
            // TODO: check how to deal with failed send_tokens
            return self.utxo_fin_transfer_fast(fast_transfer, status, utxo_fin_transfer_msg);
```

**File:** near/omni-bridge/src/lib.rs (L2542-2548)
```rust
        self.send_tokens(
            fast_transfer.token_id.clone(),
            fast_transfer_status.relayer,
            amount,
            "",
        )
        .detach();
```
