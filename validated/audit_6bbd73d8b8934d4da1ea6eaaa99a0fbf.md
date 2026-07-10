### Title
UTXO-to-NEAR Transfer Does Not Deduct or Pay Relayer Fee — (`near/omni-bridge/src/lib.rs`)

### Summary
In the non-fast-transfer UTXO-to-NEAR finalization path, the `relayer_fee` field of `UtxoFinTransferMsg` is accepted and stored but never deducted from the recipient's payout and never transferred to the relayer. The recipient receives the full bridged `amount` (including the relayer fee portion), while the relayer receives nothing. This is the direct analog of M-13: a fee that the protocol specifies and records is silently omitted from execution.

### Finding Description
When a UTXO chain (e.g., Bitcoin) transfer finalizes to a NEAR recipient without a prior fast-transfer, the flow is:

1. `utxo_fin_transfer` dispatches to `utxo_fin_transfer_to_near` passing the full `amount`.
2. `utxo_fin_transfer_to_near` passes the same full `amount` to `utxo_fin_transfer_to_near_callback`.
3. `utxo_fin_transfer_to_near_callback` calls `send_tokens(token_id, recipient, amount, ...)` — sending the **full** `amount` to the recipient with no deduction. [1](#0-0) 

The `utxo_fin_transfer_msg.relayer_fee` field is carried through the entire call chain but is never subtracted from `amount` before the `send_tokens` call, and no subsequent payment to the relayer is made.

Contrast this with the EVM→NEAR finalization path in `process_fin_transfer_to_near`, which correctly uses `amount_without_fee()` for the recipient and then pays the fee to the relayer in `fin_transfer_send_tokens_callback`: [2](#0-1) [3](#0-2) 

Also contrast with `utxo_fin_transfer_to_other_chain`, which correctly records the `relayer_fee` in the `TransferMessage.fee` field so it can be claimed later: [4](#0-3) 

The test suite itself confirms the broken behavior — for the non-fast, to-NEAR case, the expected `relayer_change` is explicitly `0`: [5](#0-4) 

### Impact Explanation
Every non-fast UTXO-to-NEAR transfer with a non-zero `relayer_fee` results in:
- The recipient receiving `amount` tokens instead of the correct `amount - relayer_fee`.
- The relayer receiving zero compensation despite specifying a fee.

This is fee accounting corruption that misdirects value: the relayer fee portion is silently transferred to the recipient rather than to the relayer. The bridge's fee incentive mechanism for UTXO chain relayers is broken for the direct-to-NEAR path, and the recipient receives an unbacked surplus relative to what the protocol intends to release.

### Likelihood Explanation
This triggers on every ordinary (non-fast-transfer) UTXO-to-NEAR bridge finalization where `relayer_fee > 0`. The connector is the only caller of `utxo_fin_transfer`, but the bug is in the bridge contract's own logic and fires unconditionally on every such call. No special attacker action is required — the normal protocol flow produces the incorrect outcome.

### Recommendation
In `utxo_fin_transfer_to_near_callback`, deduct the `relayer_fee` from `amount` before calling `send_tokens`, and add a subsequent payment of `relayer_fee` to the relayer, mirroring the EVM path:

```rust
let amount_without_fee = U128(
    amount.0
        .checked_sub(utxo_fin_transfer_msg.relayer_fee.0)
        .expect("fee exceeds amount"),
);
self.send_tokens(token_id.clone(), recipient, amount_without_fee, &utxo_fin_transfer_msg.msg)
    .then(/* pay relayer_fee to relayer */)
```

Alternatively, construct a `TransferMessage` with the fee field populated (as `utxo_fin_transfer_to_other_chain` does) and use the existing `fin_transfer_send_tokens_callback` fee-payment logic.

### Proof of Concept
1. A UTXO connector calls `ft_transfer_call` on the bridge with `amount = 100_000_000` and `UtxoFinTransferMsg { relayer_fee: U128(1000), recipient: OmniAddress::Near(...), ... }`.
2. No prior fast-transfer exists for this UTXO ID.
3. `utxo_fin_transfer_to_near_callback` fires and calls `send_tokens(token_id, recipient, U128(100_000_000), ...)`.
4. The recipient receives 100,000,000 tokens — 1,000 more than they are entitled to.
5. The relayer receives 0 tokens despite specifying a 1,000-token fee.
6. The test fixture at `near/omni-tests/src/utxo_fin_transfer.rs` line 218–222 explicitly encodes this outcome as the expected result, confirming the bug is present in production logic. [6](#0-5) [7](#0-6) [5](#0-4)

### Citations

**File:** near/omni-bridge/src/lib.rs (L976-1011)
```rust
    pub fn utxo_fin_transfer_to_near_callback(
        &mut self,
        token_id: AccountId,
        recipient: AccountId,
        amount: U128,
        utxo_fin_transfer_msg: UtxoFinTransferMsg,
        origin_chain: ChainKind,
        storage_owner: &AccountId,
    ) -> PromiseOrValue<U128> {
        if !Self::check_storage_balance_result(0) {
            env::log_str(BridgeError::StorageRecipientOmitted.to_string().as_str());
            self.remove_fin_utxo_transfer(
                &utxo_fin_transfer_msg.get_transfer_id(origin_chain),
                storage_owner,
            );
            return PromiseOrValue::Value(amount);
        }

        self.send_tokens(
            token_id.clone(),
            recipient,
            amount,
            &utxo_fin_transfer_msg.msg,
        )
        .then(
            Self::ext(env::current_account_id())
                .with_static_gas(RESOLVE_UTXO_FIN_TRANSFER_GAS)
                .resolve_utxo_fin_transfer(
                    token_id,
                    amount,
                    utxo_fin_transfer_msg,
                    origin_chain,
                    storage_owner,
                ),
        )
        .into()
```

**File:** near/omni-bridge/src/lib.rs (L1720-1733)
```rust
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
```

**File:** near/omni-bridge/src/lib.rs (L1957-1966)
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
```

**File:** near/omni-bridge/src/lib.rs (L2563-2593)
```rust
    fn utxo_fin_transfer_to_near(
        recipient: AccountId,
        token_id: AccountId,
        amount: U128,
        utxo_fin_transfer_msg: UtxoFinTransferMsg,
        origin_chain: ChainKind,
        storage_owner: &AccountId,
    ) -> Promise {
        let deposit_action = StorageDepositAction {
            account_id: recipient.clone(),
            token_id: token_id.clone(),
            storage_deposit_amount: None,
        };

        Self::check_or_pay_ft_storage(&deposit_action, &mut NearToken::from_yoctonear(0)).then(
            Self::ext(env::current_account_id())
                .with_static_gas(
                    env::prepaid_gas()
                        .saturating_sub(env::used_gas())
                        .saturating_sub(UTXO_FIN_TRANSFER_CALLBACK_GAS),
                )
                .utxo_fin_transfer_to_near_callback(
                    token_id,
                    recipient,
                    amount,
                    utxo_fin_transfer_msg,
                    origin_chain,
                    storage_owner,
                ),
        )
    }
```

**File:** near/omni-bridge/src/lib.rs (L2606-2614)
```rust
        let transfer_message = TransferMessage {
            origin_nonce: self.current_origin_nonce,
            token: OmniAddress::Near(token_id.clone()),
            amount,
            recipient: utxo_fin_transfer_msg.recipient.clone(),
            fee: Fee {
                fee: utxo_fin_transfer_msg.relayer_fee,
                native_fee: U128(0),
            },
```

**File:** near/omni-tests/src/utxo_fin_transfer.rs (L218-223)
```rust
            let (recipient_change, relayer_change) = match (is_fast_transfer, is_transfer_to_near) {
                (true, true) => (0, amount),
                (true, false) => (0, amount - utxo_msg.relayer_fee.0),
                (false, true) => (amount, 0),
                (false, false) => (0, 0),
            };
```
