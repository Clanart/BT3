### Title
Optimistic State Update Before Fire-and-Forget Token Transfer in Fast Transfer Finalization Corrupts Bridge Accounting - (File: near/omni-bridge/src/lib.rs)

### Summary
In `process_fin_transfer_to_other_chain` and `utxo_fin_transfer_fast`, the bridge's locked-token accounting and fast-transfer status are updated **before** the token transfer to the relayer is confirmed. The `send_tokens` call is detached (fire-and-forget) with no callback to verify success. If the transfer fails, the state is permanently corrupted with no recovery path. The codebase itself acknowledges this with an explicit `// TODO: check how to deal with failed send_tokens` comment.

### Finding Description

**Case 1 — `process_fin_transfer_to_other_chain`**

When a fast transfer exists for a cross-chain finalization, the function executes the following sequence:

1. `unlock_tokens_if_needed(origin_chain, token, transfer_message.amount.0)` — decrements locked tokens on the origin chain.
2. `lock_tokens_if_needed(destination_chain, token, transfer_message.fee.fee)` — increments locked tokens on the destination chain.
3. `send_tokens(token, relayer, amount_without_fee, "").detach()` — fire-and-forget token transfer to the relayer.
4. `mark_fast_transfer_as_finalised(...)` — permanently marks the fast transfer as finalised. [1](#0-0) 

Steps 1, 2, and 4 mutate state optimistically, assuming step 3 will succeed. If `send_tokens` fails (token contract paused, insufficient gas, relayer storage not registered for the token), the state is permanently corrupted:
- Origin chain locked tokens are decremented (but tokens remain in the bridge).
- Destination chain locked tokens are incremented (but the transfer did not complete).
- Fast transfer is marked finalised and cannot be retried.
- Relayer loses their advance payment with no recovery path.

**Case 2 — `utxo_fin_transfer_fast`**

The same pattern appears in the UTXO fast-transfer path. The fast transfer is removed or marked finalised **before** `send_tokens(...).detach()` is called: [2](#0-1) 

The call site in `utxo_fin_transfer` contains an explicit developer acknowledgment of the unhandled failure: [3](#0-2) 

```rust
if let Some(status) = self.get_fast_transfer_status(&fast_transfer.id()) {
    // TODO: check how to deal with failed send_tokens
    return self.utxo_fin_transfer_fast(fast_transfer, status, utxo_fin_transfer_msg);
}
```

**Contrast with the correctly handled path**

The `process_fin_transfer_to_near` path correctly uses a chained callback (`fin_transfer_send_tokens_callback`) that checks the result of `send_tokens` and reverts lock actions on failure: [4](#0-3) [5](#0-4) 

The `revert_lock_actions` mechanism exists and works correctly for the NEAR-recipient path, but is never invoked for the other-chain fast-transfer path. [6](#0-5) 

### Impact Explanation

If `send_tokens` fails in either function:

1. **Bridge collateralization is broken**: The `locked_tokens` map no longer matches actual token balances held by the bridge. Subsequent `unlock_tokens` calls may succeed against an inflated counter, allowing more withdrawals than the bridge can back — a direct analog to the external report's inflated payout.
2. **Permanent loss of relayer funds**: The relayer who advanced tokens to the user loses their advance payment. The fast transfer is marked finalised and cannot be retried.
3. **Tokens permanently frozen in bridge**: The tokens that should have been sent to the relayer are stuck in the bridge contract with no admin recovery function for this specific case.

This matches: **High — Balance, decimal, fee, token-mapping, or accounting corruption that breaks bridge collateralization or misdirects value**, and potentially **Critical — Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds**.

### Likelihood Explanation

`send_tokens` for a non-deployed token calls `ft_transfer` on the token contract: [7](#0-6) 

This call can fail if:
- The relayer's account does not have storage registered for the specific token (a new token added after the relayer registered).
- The token contract is paused or has a bug.
- Gas is exhausted (the gas budget for `process_fin_transfer_to_other_chain` is shared across multiple operations).

The TODO comment at line 2484 is a direct developer acknowledgment that this failure path is unhandled, making this a known-but-unmitigated design gap.

### Recommendation

Apply the same callback pattern used in `process_fin_transfer_to_near` to both affected paths:

1. In `process_fin_transfer_to_other_chain`, replace `.detach()` with a chained callback that, on failure, re-locks the origin tokens, un-locks the destination fee tokens, and un-marks the fast transfer as finalised (or removes it from the finalised set).
2. In `utxo_fin_transfer_fast`, similarly chain a callback that reverts the fast-transfer state change if `send_tokens` fails.
3. Resolve the `// TODO` comment at line 2484.

### Proof of Concept

**Scenario for `process_fin_transfer_to_other_chain`:**

1. User initiates a transfer from Ethereum → Solana for token `T`, amount `A`, fee `F`.
2. A trusted relayer performs a fast transfer: sends `A - F` of token `T` to the Solana recipient immediately.
3. The bridge records the fast transfer with `relayer = R`.
4. Later, a relayer submits the Ethereum proof via `fin_transfer`.
5. `fin_transfer_callback` → `process_fin_transfer_to_other_chain` is called.
6. `unlock_tokens(Eth, T, A)` executes — Ethereum locked balance decremented.
7. `lock_tokens(Sol, T, F)` executes — Solana locked balance incremented.
8. `send_tokens(T, R, A-F, "").detach()` is called — but the token contract for `T` is paused at this moment (or `R` lacks storage registration).
9. The `ft_transfer` inside `send_tokens` fails silently (detached promise).
10. `mark_fast_transfer_as_finalised(...)` executes — fast transfer permanently finalised.

**Result:**
- Relayer `R` never receives `A - F` tokens; their advance is lost.
- `locked_tokens[Eth][T]` is `A` less than the actual bridge balance — the bridge will over-report available liquidity for future withdrawals.
- `locked_tokens[Sol][T]` is `F` more than it should be.
- No admin function exists to correct this specific accounting desync. [8](#0-7) [9](#0-8)

### Citations

**File:** near/omni-bridge/src/lib.rs (L1700-1718)
```rust
        let token = self.get_token_id(&transfer_message.token);

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

**File:** near/omni-bridge/src/lib.rs (L1980-2054)
```rust
    fn process_fin_transfer_to_other_chain(
        &mut self,
        predecessor_account_id: AccountId,
        transfer_message: TransferMessage,
    ) {
        let mut required_balance = self.add_fin_transfer(&transfer_message.get_transfer_id());
        let token = self.get_token_id(&transfer_message.token);

        if transfer_message.recipient.is_utxo_chain() {
            let btc_account_id =
                self.get_utxo_chain_token(transfer_message.get_destination_chain());
            require!(
                token == btc_account_id,
                BridgeError::NativeTokenRequiredForChain.as_ref()
            );
        }

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

        let fast_transfer = FastTransfer::from_transfer(transfer_message.clone(), token.clone());
        let recipient = if let Some(status) = self.get_fast_transfer_status(&fast_transfer.id()) {
            require!(
                !status.finalised,
                BridgeError::FastTransferAlreadyFinalised.as_ref()
            );
            Some(status.relayer)
        } else {
            self.lock_tokens_if_needed(
                transfer_message.get_destination_chain(),
                &token,
                transfer_message
                    .amount_without_fee()
                    .near_expect(BridgeError::InvalidFee),
            );

            None
        };

        // If fast transfer happened, send tokens to the relayer that executed fast transfer
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
        } else {
            required_balance = self
                .add_transfer_message(transfer_message.clone(), predecessor_account_id.clone())
                .saturating_add(required_balance);
        }

        self.update_storage_balance(
            predecessor_account_id,
            required_balance,
            env::attached_deposit(),
        );

        env::log_str(&OmniBridgeEvent::FinTransferEvent { transfer_message }.to_log_string());
    }
```

**File:** near/omni-bridge/src/lib.rs (L2102-2106)
```rust
        } else if msg.is_empty() {
            ext_token::ext(token)
                .with_attached_deposit(ONE_YOCTO)
                .with_static_gas(FT_TRANSFER_GAS)
                .ft_transfer(recipient, amount, None)
```

**File:** near/omni-bridge/src/lib.rs (L2483-2486)
```rust
        if let Some(status) = self.get_fast_transfer_status(&fast_transfer.id()) {
            // TODO: check how to deal with failed send_tokens
            return self.utxo_fin_transfer_fast(fast_transfer, status, utxo_fin_transfer_msg);
        }
```

**File:** near/omni-bridge/src/lib.rs (L2518-2561)
```rust
    fn utxo_fin_transfer_fast(
        &mut self,
        fast_transfer: FastTransfer,
        fast_transfer_status: FastTransferStatus,
        utxo_fin_transfer_msg: UtxoFinTransferMsg,
    ) -> PromiseOrPromiseIndexOrValue<U128> {
        require!(
            !fast_transfer_status.finalised,
            BridgeError::FastTransferAlreadyFinalised.as_ref()
        );

        let amount = if fast_transfer.get_destination_chain() == ChainKind::Near {
            self.remove_fast_transfer(&fast_transfer.id());
            fast_transfer.amount
        } else {
            self.mark_fast_transfer_as_finalised(&fast_transfer.id());
            // With transfers to other chain the fee will be claimed after finalization on the destination chain
            U128(
                fast_transfer
                    .amount_without_fee()
                    .near_expect(BridgeError::InvalidFee),
            )
        };

        self.send_tokens(
            fast_transfer.token_id.clone(),
            fast_transfer_status.relayer,
            amount,
            "",
        )
        .detach();

        env::log_str(
            &OmniBridgeEvent::UtxoTransferEvent {
                token_id: fast_transfer.token_id,
                amount,
                utxo_transfer_message: utxo_fin_transfer_msg,
                new_transfer_id: None,
            }
            .to_log_string(),
        );

        PromiseOrPromiseIndexOrValue::Value(U128(0))
    }
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
