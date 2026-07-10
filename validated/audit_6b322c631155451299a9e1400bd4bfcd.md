### Title
Missing Finalization Record in UTXO Fast-Transfer Path Enables Double-Spend Replay - (File: `near/omni-bridge/src/lib.rs`)

### Summary

In `utxo_fin_transfer`, when a fast transfer exists for a NEAR-destination UTXO transfer, the function dispatches to `utxo_fin_transfer_fast`, which removes the fast-transfer entry via `remove_fast_transfer` but **never calls `add_fin_utxo_transfer`**. This leaves the UTXO transfer ID unrecorded in `finalised_utxo_transfers`. After the fast-transfer entry is erased, the bridge has no memory that this UTXO transfer was ever processed, making it fully replayable.

### Finding Description

The normal (non-fast) path in `utxo_fin_transfer` always calls `add_fin_utxo_transfer` before doing anything else: [1](#0-0) 

But when a fast-transfer status is found, the function short-circuits and returns immediately without ever recording the UTXO transfer ID: [2](#0-1) 

Inside `utxo_fin_transfer_fast`, for a NEAR-destination transfer, `remove_fast_transfer` is called, which **deletes** the only state entry that could block a replay: [3](#0-2) 

After this returns, the bridge state is identical to a state where the UTXO transfer was never seen:
- `finalised_utxo_transfers` does not contain the UTXO transfer ID.
- `fast_transfers` no longer contains the fast-transfer entry.

**Replay attack path:**

1. Fast relayer calls `fast_fin_transfer` (via `ft_transfer_call` → `ft_on_transfer`) for a UTXO transfer with a NEAR recipient. A `FastTransferStatus` entry is created in `fast_transfers`. The fast relayer pays the recipient from their own funds.

2. The UTXO connector calls `verify_deposit` (a public function callable by any relayer), which triggers `ft_transfer_call` on the token contract → `ft_on_transfer` on the bridge → `utxo_fin_transfer`. The fast-transfer entry is found, `utxo_fin_transfer_fast` runs, the fast relayer is reimbursed, and the entry is removed. **The UTXO ID is never written to `finalised_utxo_transfers`.**

3. Any relayer calls `verify_deposit` again on the connector with the **same UTXO transfer**. The connector has no authoritative replay protection — the bridge is supposed to provide it. The connector sends tokens to the bridge again.

4. `utxo_fin_transfer` is called again. `get_fast_transfer_status` returns `None` (entry was deleted). The code falls through to `add_fin_utxo_transfer`, which succeeds because the UTXO ID was never recorded. The transfer is processed as a fresh transfer and tokens are sent to the original recipient a second time. [4](#0-3) 

A secondary consequence: because `is_unified_transfer_finalised` checks `finalised_utxo_transfers` (which is never updated in the fast path), a second fast transfer can also be created for the same UTXO ID after the first is finalized: [5](#0-4) [6](#0-5) 

This allows the cycle to repeat: create fast transfer → connector submits → fast relayer reimbursed → entry removed → repeat.

The contrast with the non-NEAR destination path is instructive: when `get_destination_chain() != Near`, `mark_fast_transfer_as_finalised` is called instead of `remove_fast_transfer`, leaving the entry with `finalised = true`. A second call to `utxo_fin_transfer` would then find the entry and panic with `FastTransferAlreadyFinalised`. Only the NEAR-destination branch is unprotected. [7](#0-6) 

### Impact Explanation

**Critical — double-finalization enabling double-spend and unbacked token supply.**

Each replay of the same UTXO transfer causes the bridge to release tokens to the recipient a second time from the bridge's locked token pool. The connector's token balance is drained proportionally. Because `finalised_utxo_transfers` is never updated, the replay can be repeated indefinitely (bounded only by the connector's token balance), draining the bridge's collateral and creating unbacked token supply on NEAR.

### Likelihood Explanation

The `verify_deposit` function on the UTXO connector is callable by any trusted relayer (as shown in the integration tests). The bridge is the authoritative replay-protection layer; the connector is not expected to deduplicate UTXO IDs independently. Any trusted relayer who observes that a UTXO fast transfer has been finalized (and the entry removed) can immediately re-submit the same UTXO transfer to the connector and collect a second payout. The precondition — a NEAR-destination UTXO fast transfer having been finalized — is a normal operational state, not an edge case.

### Recommendation

In `utxo_fin_transfer_fast`, unconditionally call `add_fin_utxo_transfer` before branching on the destination chain, mirroring the normal path. Specifically, add the following before the `remove_fast_transfer` / `mark_fast_transfer_as_finalised` branch:

```rust
// Record the UTXO transfer as finalised to prevent replay,
// regardless of whether a fast transfer was involved.
self.add_fin_utxo_transfer(&utxo_fin_transfer_msg.get_transfer_id(origin_chain));
```

Additionally, the `fast_fin_transfer` function's guard (`is_unified_transfer_finalised`) will then correctly block creation of a second fast transfer for an already-finalized UTXO ID.

### Proof of Concept

```
// State before attack:
// finalised_utxo_transfers = {}
// fast_transfers = {}

// Step 1: Fast relayer pre-pays recipient
fast_fin_transfer(utxo_id=X, recipient=alice.near, amount=100)
// State: fast_transfers = {X -> {relayer: fast_relayer, finalised: false}}
// alice.near receives 100 tokens from fast_relayer

// Step 2: Connector submits UTXO transfer (normal finalization)
connector.verify_deposit(utxo_id=X, recipient=alice.near, amount=100)
// -> utxo_fin_transfer called
// -> fast transfer found -> utxo_fin_transfer_fast called
// -> remove_fast_transfer(X)  [entry deleted]
// -> send_tokens(fast_relayer, 100)  [fast relayer reimbursed]
// -> add_fin_utxo_transfer NOT called
// State: fast_transfers = {}
//        finalised_utxo_transfers = {}  <-- BUG: X not recorded

// Step 3: Attacker re-submits same UTXO transfer
connector.verify_deposit(utxo_id=X, recipient=alice.near, amount=100)
// -> utxo_fin_transfer called
// -> get_fast_transfer_status(X) = None  (entry was deleted)
// -> add_fin_utxo_transfer(X) succeeds  (X not in set)
// -> utxo_fin_transfer_to_near called
// -> send_tokens(alice.near, 100)  [alice receives tokens AGAIN]
// State: finalised_utxo_transfers = {X}

// Net result: alice received 200 tokens for a 100-token UTXO deposit.
// Bridge's locked token pool is short by 100 tokens.
// Repeat Step 3 is now blocked, but Step 1+2+3 can be cycled
// with a fresh fast transfer (since finalised_utxo_transfers was
// empty during Step 1, a second fast transfer could have been
// created before Step 3).
```

### Citations

**File:** near/omni-bridge/src/lib.rs (L778-780)
```rust
        if self.is_unified_transfer_finalised(&fast_fin_transfer_msg.transfer_id) {
            env::panic_str(BridgeError::TransferAlreadyFinalised.to_string().as_str());
        }
```

**File:** near/omni-bridge/src/lib.rs (L2226-2244)
```rust
    fn add_fin_transfer(&mut self, transfer_id: &TransferId) -> NearToken {
        let storage_usage = env::storage_usage();
        require!(
            self.finalised_transfers.insert(transfer_id),
            BridgeError::TransferAlreadyFinalised.as_ref()
        );
        env::storage_byte_cost()
            .saturating_mul((env::storage_usage().saturating_sub(storage_usage)).into())
    }

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

**File:** near/omni-bridge/src/lib.rs (L2456-2516)
```rust
    fn utxo_fin_transfer(
        &mut self,
        token_id: AccountId,
        amount: U128,
        signer_id: &AccountId,
        sender_id: &AccountId,
        utxo_fin_transfer_msg: UtxoFinTransferMsg,
    ) -> PromiseOrPromiseIndexOrValue<U128> {
        let origin_chain = self
            .get_utxo_chain_by_token(&token_id)
            .near_expect(BridgeError::UtxoConfigMissing);
        let config = self
            .utxo_chain_connectors
            .get(&origin_chain)
            .near_expect(BridgeError::UtxoConfigMissing);
        require!(
            sender_id == &config.connector,
            BridgeError::SenderIsNotConnector.as_ref()
        );

        let fast_transfer = FastTransfer::from_utxo_transfer(
            utxo_fin_transfer_msg.clone(),
            token_id.clone(),
            amount,
            origin_chain,
        );

        if let Some(status) = self.get_fast_transfer_status(&fast_transfer.id()) {
            // TODO: check how to deal with failed send_tokens
            return self.utxo_fin_transfer_fast(fast_transfer, status, utxo_fin_transfer_msg);
        }

        let required_storage_balance =
            self.add_fin_utxo_transfer(&utxo_fin_transfer_msg.get_transfer_id(origin_chain));

        self.update_storage_balance(
            signer_id.clone(),
            required_storage_balance,
            NearToken::from_yoctonear(0),
        );

        if let OmniAddress::Near(recipient) = utxo_fin_transfer_msg.recipient.clone() {
            Self::utxo_fin_transfer_to_near(
                recipient,
                token_id,
                amount,
                utxo_fin_transfer_msg,
                origin_chain,
                signer_id,
            )
            .into()
        } else {
            self.utxo_fin_transfer_to_other_chain(
                token_id,
                amount,
                utxo_fin_transfer_msg,
                origin_chain,
                signer_id,
            )
        }
    }
```

**File:** near/omni-bridge/src/lib.rs (L2529-2531)
```rust
        let amount = if fast_transfer.get_destination_chain() == ChainKind::Near {
            self.remove_fast_transfer(&fast_transfer.id());
            fast_transfer.amount
```

**File:** near/omni-bridge/src/lib.rs (L2532-2540)
```rust
        } else {
            self.mark_fast_transfer_as_finalised(&fast_transfer.id());
            // With transfers to other chain the fee will be claimed after finalization on the destination chain
            U128(
                fast_transfer
                    .amount_without_fee()
                    .near_expect(BridgeError::InvalidFee),
            )
        };
```
