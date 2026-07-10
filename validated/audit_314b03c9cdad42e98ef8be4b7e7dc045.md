### Title
Unchecked Detached `send_tokens` Promise in Fast-Transfer Repayment Paths Causes Permanent Fund Lock - (File: near/omni-bridge/src/lib.rs)

### Summary
In two fast-transfer repayment paths — `utxo_fin_transfer_fast` and `process_fin_transfer_to_other_chain` — the bridge mutates critical state (removing or finalising the fast-transfer record) and then calls `send_tokens(...).detach()` to repay the relayer. Because the promise is detached (fire-and-forget), any failure of `send_tokens` is silently swallowed. The state mutation is already committed and irreversible, so the relayer's fronted tokens and the UTXO connector's deposited tokens are permanently locked in the bridge with no recovery path. The codebase itself acknowledges this gap with an explicit `// TODO: check how to deal with failed send_tokens` comment.

### Finding Description

**Path 1 — `utxo_fin_transfer_fast`**

When a UTXO deposit arrives and a matching fast-transfer record exists, the bridge:

1. Irrevocably mutates state — either `remove_fast_transfer` (destination = NEAR) or `mark_fast_transfer_as_finalised` (destination = other chain).
2. Calls `send_tokens(...).detach()` to repay the relayer.
3. Returns `U128(0)` to the UTXO connector, signalling that all tokens were consumed. [1](#0-0) 

The `TODO` comment at the call-site explicitly flags this as unresolved: [2](#0-1) 

If `send_tokens` fails after `remove_fast_transfer` has already been called, the fast-transfer record is gone, the UTXO connector has already been told all tokens were consumed (no refund), and the relayer receives nothing. The tokens are permanently locked inside the bridge contract.

**Path 2 — `process_fin_transfer_to_other_chain`**

When an EVM→other-chain transfer is finalised and a fast-transfer record exists, the bridge:

1. Calls `send_tokens(...).detach()` to repay the relayer.
2. Immediately calls `mark_fast_transfer_as_finalised` regardless of the promise outcome. [3](#0-2) 

If `send_tokens` fails, the fast-transfer is marked finalised (preventing any retry), the relayer is not repaid, and the unlocked tokens remain stuck in the bridge.

**Why `send_tokens` can fail**

`send_tokens` dispatches either `ft_transfer` (for non-deployed tokens) or `mint` (for deployed tokens): [4](#0-3) 

Failure conditions include: the token contract being paused, the relayer's storage registration having been removed from the token contract between the fast-transfer and the UTXO deposit, or any panic inside the token contract. None of these require privileged access to trigger.

### Impact Explanation
If `send_tokens` fails in either path, the UTXO connector's deposited tokens (representing the user's BTC/UTXO deposit) and the relayer's fronted tokens are permanently locked inside the bridge contract. There is no admin function, no retry mechanism, and no refund path — the fast-transfer record has already been removed or finalised. This constitutes an irrecoverable lock of user and protocol funds in the bridge flow.

### Likelihood Explanation
The likelihood is low-to-medium. Normal operation succeeds, but the failure surface is real: token contracts can be paused, storage registrations can lapse, or transient panics can occur. The developer-placed `// TODO: check how to deal with failed send_tokens` comment at line 2484 is direct evidence that the team recognises this as an open, unresolved risk rather than an intentional design choice.

### Recommendation
Replace `.detach()` with a chained callback that checks the `send_tokens` result. On failure, the callback should:
- Re-insert the fast-transfer record (or un-finalise it) so the repayment can be retried.
- Return the full token amount to the UTXO connector (i.e., return `amount` instead of `U128(0)`) so the connector can refund the depositor.

This mirrors the existing `resolve_fast_transfer` and `resolve_utxo_fin_transfer` patterns already used in the to-NEAR paths.

### Proof of Concept

1. Relayer calls `ft_transfer_call` on the token contract with `FastFinTransferMsg` for a BTC→NEAR transfer. Bridge records the fast transfer and sends tokens to the recipient. Relayer's tokens are now in the bridge.
2. BTC deposit is confirmed. UTXO connector calls `ft_transfer_call` on the token contract, which triggers `ft_on_transfer` → `utxo_fin_transfer` → `utxo_fin_transfer_fast`.
3. Inside `utxo_fin_transfer_fast`, `remove_fast_transfer` is called (state mutated, no rollback possible).
4. `send_tokens(...).detach()` is dispatched to repay the relayer. The function returns `U128(0)` to the UTXO connector immediately.
5. The token contract's `ft_transfer` panics (e.g., relayer unregistered storage, or contract is paused). The detached promise fails silently.
6. Result: UTXO connector's tokens are in the bridge, fast-transfer record is gone, relayer is not repaid. Tokens are permanently locked with no recovery path. [5](#0-4) [2](#0-1)

### Citations

**File:** near/omni-bridge/src/lib.rs (L2028-2041)
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
        } else {
```

**File:** near/omni-bridge/src/lib.rs (L2056-2118)
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
    }
```

**File:** near/omni-bridge/src/lib.rs (L2483-2486)
```rust
        if let Some(status) = self.get_fast_transfer_status(&fast_transfer.id()) {
            // TODO: check how to deal with failed send_tokens
            return self.utxo_fin_transfer_fast(fast_transfer, status, utxo_fin_transfer_msg);
        }
```

**File:** near/omni-bridge/src/lib.rs (L2518-2560)
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
```
