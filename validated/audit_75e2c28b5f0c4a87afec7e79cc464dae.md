### Title
UTXO→NEAR Finalization Sends Full Amount to Recipient Without Deducting Relayer Fee — (`near/omni-bridge/src/lib.rs`)

### Summary

`utxo_fin_transfer_to_near_callback` forwards the entire `amount` (inclusive of `relayer_fee`) to the recipient and never pays the relayer. Every other finalization path in the bridge correctly deducts the fee before crediting the recipient. The inconsistency is a direct accounting error that misdirects value on every UTXO→NEAR settlement.

### Finding Description

**Root cause — `utxo_fin_transfer_to_near_callback`:**

```rust
// near/omni-bridge/src/lib.rs  line ~994
self.send_tokens(
    token_id.clone(),
    recipient,
    amount,                          // ← full amount, fee never subtracted
    &utxo_fin_transfer_msg.msg,
)
```

`amount` is the gross amount received from the UTXO connector (i.e. `amount_without_fee + relayer_fee`). The `utxo_fin_transfer_msg.relayer_fee` field is carried through the call chain but is never used to split the payment.

**Contrast with every other finalization path:**

| Path | Recipient receives | Relayer receives |
|---|---|---|
| EVM → NEAR (`process_fin_transfer_to_near`) | `amount_without_fee` | `fee.fee` via `fin_transfer_send_tokens_callback` |
| Fast transfer → NEAR (`fast_fin_transfer_to_near_callback`) | `amount_without_fee` | reimbursed later via `process_fin_transfer_to_near` |
| UTXO → other chain (`utxo_fin_transfer_to_other_chain`) | `normalize(amount - relayer_fee)` on destination | `relayer_fee` via `claim_fee` |
| **UTXO → NEAR (`utxo_fin_transfer_to_near_callback`)** | **full `amount`** | **nothing** |

`utxo_fin_transfer_to_other_chain` explicitly stores `relayer_fee` as the transfer fee and locks the full `amount` for the destination chain, so the fee is later claimable. The NEAR-destination branch has no equivalent logic.

The test fixture in `near/omni-tests/src/utxo_fin_transfer.rs` already exercises a non-zero `relayer_fee = U128(1000)` for a NEAR recipient, confirming the field is intended to be meaningful in this path.

### Impact Explanation

Every UTXO→NEAR settlement overpays the recipient by exactly `relayer_fee` tokens and underpays the relayer by the same amount. Because the UTXO connector sends the gross amount to the bridge expecting the bridge to split it, the relayer's fee revenue is permanently lost on each such transfer. This is a fee-accounting corruption that misdirects value — matching the "High. Balance, decimal, fee, token-mapping, or accounting corruption that breaks bridge collateralization or misdirects value" impact class.

### Likelihood Explanation

The bug fires on every successful UTXO→NEAR finalization where `relayer_fee > 0`. No special attacker capability is required; any ordinary user whose BTC/UTXO transfer is routed to a NEAR recipient triggers the path. The test suite already covers this case with a non-zero fee, so the scenario is part of the intended protocol surface.

### Recommendation

In `utxo_fin_transfer_to_near_callback`, mirror the EVM→NEAR pattern:

1. Send `amount - relayer_fee` to the recipient.
2. Send `relayer_fee` to the relayer (the `storage_owner` / UTXO connector, or an explicit `fee_recipient` parameter).

Alternatively, construct a `TransferMessage` with `fee = relayer_fee` and reuse `process_fin_transfer_to_near` so the fee-split logic is shared across all paths.

### Proof of Concept

```
1. User sends 100_000_000 satoshi-equivalent tokens on the UTXO chain to a NEAR address.
2. UTXO connector calls ft_transfer_call on the bridge:
     amount = 100_000_000
     UtxoFinTransferMsg { relayer_fee: U128(1_000), recipient: Near("alice.near"), ... }
3. Bridge routes to utxo_fin_transfer_to_near_callback.
4. Bridge calls send_tokens(token_id, "alice.near", U128(100_000_000), "").
   → alice.near receives 100_000_000 (should receive 99_999_000).
5. relayer_fee = 1_000 is never transferred to the relayer.
   → Relayer loses 1_000 tokens per transfer.
```

**Relevant code locations:** [1](#0-0) 

`utxo_fin_transfer_to_near_callback` sends `amount` (full gross) to recipient with no fee deduction. [2](#0-1) 

`process_fin_transfer_to_near` (EVM path) correctly sends `amount_without_fee` to recipient. [3](#0-2) 

`fin_transfer_send_tokens_callback` pays `fee.fee` to the relayer in the EVM path. [4](#0-3) 

`utxo_fin_transfer_to_other_chain` stores `relayer_fee` as the transfer fee and locks the full amount, enabling later fee claim — the NEAR-destination branch has no equivalent. [5](#0-4) 

Test fixture confirms non-zero `relayer_fee` is expected for UTXO→NEAR transfers.

### Citations

**File:** near/omni-bridge/src/lib.rs (L975-1011)
```rust
    #[private]
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

**File:** near/omni-bridge/src/lib.rs (L1719-1733)
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

**File:** near/omni-bridge/src/lib.rs (L2606-2629)
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
            sender: OmniAddress::Near(env::predecessor_account_id()),
            msg: utxo_fin_transfer_msg.msg.clone(),
            destination_nonce: self
                .get_next_destination_nonce(utxo_fin_transfer_msg.get_destination_chain()),
            origin_transfer_id: Some(origin_transfer_id),
        };

        let required_storage_balance =
            self.add_transfer_message(transfer_message.clone(), storage_owner.clone());

        self.lock_tokens_if_needed(
            transfer_message.get_destination_chain(),
            &token_id,
            transfer_message.amount.0,
        );
```

**File:** near/omni-tests/src/utxo_fin_transfer.rs (L369-382)
```rust
    // Succeeds after fast transfer to Near
    #[case(
        UtxoFinTransferCase {
            amount: 100_000_000,
            utxo_msg: UtxoFinTransferMsg {
                utxo_id: default_utxo_id(),
                recipient: OmniAddress::Near(account_n(1)),
                relayer_fee: U128(1000),
                msg: String::default(),
            },
            is_fast_transfer: true,
            error: None,
        }
    )]
```
