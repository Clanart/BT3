Now I have all the information needed. Let me trace the complete exploit path precisely.

### Title
UTXO Finalization Set Cleared on Storage Failure Enables Double-Settlement — (`near/omni-bridge/src/lib.rs`)

### Summary

`utxo_fin_transfer` inserts a UTXO into `finalised_utxo_transfers` as its replay-prevention guard, but `utxo_fin_transfer_to_near_callback` unconditionally calls `remove_fin_utxo_transfer` when the recipient's storage-balance check fails. This erases the guard before any tokens are delivered, leaving the bridge in a state where the same UTXO can be re-submitted and settled a second time.

### Finding Description

The settlement flow for a BTC-to-NEAR transfer is:

**Step 1 — guard inserted** (`utxo_fin_transfer`, line 2488-2489):

```rust
let required_storage_balance =
    self.add_fin_utxo_transfer(&utxo_fin_transfer_msg.get_transfer_id(origin_chain));
```

`add_fin_utxo_transfer` calls `self.finalised_utxo_transfers.insert(transfer_id)` and panics with `UtxoTransferAlreadyFinalised` if the key already exists — this is the sole replay guard. [1](#0-0) 

**Step 2 — async storage check** (`utxo_fin_transfer_to_near`, line 2577):

```rust
Self::check_or_pay_ft_storage(&deposit_action, &mut NearToken::from_yoctonear(0)).then(
    Self::ext(env::current_account_id())
        .utxo_fin_transfer_to_near_callback(...)
)
```

A cross-contract call to `storage_balance_of` is made; the result is inspected in the callback. [2](#0-1) 

**Step 3 — guard silently removed on failure** (`utxo_fin_transfer_to_near_callback`, lines 985-991):

```rust
if !Self::check_storage_balance_result(0) {
    env::log_str(BridgeError::StorageRecipientOmitted.to_string().as_str());
    self.remove_fin_utxo_transfer(
        &utxo_fin_transfer_msg.get_transfer_id(origin_chain),
        storage_owner,
    );
    return PromiseOrValue::Value(amount);   // full refund to connector
}
```

`remove_fin_utxo_transfer` deletes the entry from `finalised_utxo_transfers` and credits the storage refund back to the storage owner. The full token amount is returned to the connector via the NEP-141 `ft_on_transfer` return value. [3](#0-2) [4](#0-3) 

**Step 4 — second path: guard also removed on token-transfer failure** (`resolve_utxo_fin_transfer`, lines 1025-1030):

```rust
if Self::is_refund_required(is_ft_transfer_call) {
    self.remove_fin_utxo_transfer(...);
    amount
}
```

Even when the storage check passes, if the downstream `send_tokens` call fails (e.g., recipient's `ft_on_transfer` panics), the guard is again removed and the full amount refunded. [5](#0-4) 

After either removal path, `finalised_utxo_transfers` no longer contains the UTXO. A subsequent call to `utxo_fin_transfer` with the identical `UtxoFinTransferMsg` passes `add_fin_utxo_transfer`'s `require!` check and is processed as a fresh settlement. [6](#0-5) 

### Impact Explanation

Each successful re-submission mints or transfers tokens to the recipient without a corresponding new BTC deposit. The connector holds the refunded tokens and can re-invoke the bridge with the same UTXO. This creates unbacked token supply — a direct double-spend of the bridged asset.

### Likelihood Explanation

The trigger condition (unregistered recipient storage) is trivially reachable: any recipient account that has not called `storage_deposit` on the token contract will cause the failure. The refund-and-retry design is the intended recovery path (the bridge returns the full amount to the connector precisely so it can retry). The mock connector (`near/mock/mock-utxo-connector/src/lib.rs`) has no deduplication and `verify_deposit` is a public, permissionless function, confirming the retry path is expected. [7](#0-6) 

The only external dependency is the production connector's own deduplication logic, which is not present in this repository. If the production connector tracks processed UTXOs independently, the impact is mitigated at the connector layer — but the bridge itself provides no such guarantee.

### Recommendation

Do **not** remove the UTXO from `finalised_utxo_transfers` on failure. Instead:

- Keep the entry in `finalised_utxo_transfers` permanently once inserted.
- Introduce a separate "claimable" or "pending" state for UTXOs whose delivery failed, allowing the recipient to register storage and claim tokens without re-running the finalization check.
- Alternatively, require the recipient to have registered storage **before** the connector submits the UTXO, making the storage check a pre-condition rather than an async gate.

### Proof of Concept

```
1. Relayer calls connector.verify_deposit(utxo_id=X, recipient=unregistered_account, amount=N)
2. Connector calls token.ft_transfer_call(bridge, N, UtxoFinTransfer{utxo_id=X, ...})
3. Bridge: add_fin_utxo_transfer(X) → finalised_utxo_transfers = {X}
4. Bridge: check_or_pay_ft_storage(unregistered_account) → storage_balance_of returns null
5. Callback: check_storage_balance_result(0) == false
           → remove_fin_utxo_transfer(X) → finalised_utxo_transfers = {}
           → return amount N  (connector receives N back)
6. Assert: finalised_utxo_transfers.contains(X) == false  ✓
7. Relayer calls connector.verify_deposit(utxo_id=X, recipient=registered_account, amount=N)
8. Bridge: add_fin_utxo_transfer(X) succeeds (set is empty) → processes second settlement
9. Assert: recipient receives N tokens for a UTXO that was already "finalized" in step 3
```

### Citations

**File:** near/omni-bridge/src/lib.rs (L985-992)
```rust
        if !Self::check_storage_balance_result(0) {
            env::log_str(BridgeError::StorageRecipientOmitted.to_string().as_str());
            self.remove_fin_utxo_transfer(
                &utxo_fin_transfer_msg.get_transfer_id(origin_chain),
                storage_owner,
            );
            return PromiseOrValue::Value(amount);
        }
```

**File:** near/omni-bridge/src/lib.rs (L1024-1030)
```rust
        let is_ft_transfer_call = !utxo_fin_transfer_msg.msg.is_empty();
        if Self::is_refund_required(is_ft_transfer_call) {
            self.remove_fin_utxo_transfer(
                &utxo_fin_transfer_msg.get_transfer_id(origin_chain),
                storage_owner,
            );
            amount
```

**File:** near/omni-bridge/src/lib.rs (L2236-2244)
```rust
    fn add_fin_utxo_transfer(&mut self, transfer_id: &UnifiedTransferId) -> NearToken {
        let storage_usage = env::storage_usage();
        require!(
            self.finalised_utxo_transfers.insert(transfer_id),
            BridgeError::UtxoTransferAlreadyFinalised.as_ref()
        );
        env::storage_byte_cost()
            .saturating_mul((env::storage_usage().saturating_sub(storage_usage)).into())
    }
```

**File:** near/omni-bridge/src/lib.rs (L2335-2351)
```rust
    fn remove_fin_utxo_transfer(
        &mut self,
        transfer_id: &UnifiedTransferId,
        storage_owner: &AccountId,
    ) {
        let storage_usage = env::storage_usage();

        self.finalised_utxo_transfers.remove(transfer_id);

        let refund =
            env::storage_byte_cost().saturating_mul((storage_usage - env::storage_usage()).into());

        if let Some(mut storage) = self.accounts_balances.get(storage_owner) {
            storage.available = storage.available.saturating_add(refund);
            self.accounts_balances.insert(storage_owner, &storage);
        }
    }
```

**File:** near/omni-bridge/src/lib.rs (L2488-2489)
```rust
        let required_storage_balance =
            self.add_fin_utxo_transfer(&utxo_fin_transfer_msg.get_transfer_id(origin_chain));
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

**File:** near/mock/mock-utxo-connector/src/lib.rs (L38-47)
```rust
    pub fn verify_deposit(&mut self, amount: U128, msg: UtxoFinTransferMsg) -> Promise {
        ext_token::ext(self.token_account.clone())
            .with_attached_deposit(ONE_YOCTO)
            .ft_transfer_call(
                self.bridge_account.clone(),
                amount,
                None,
                serde_json::to_string(&BridgeOnTransferMsg::UtxoFinTransfer(msg)).unwrap(),
            )
    }
```
