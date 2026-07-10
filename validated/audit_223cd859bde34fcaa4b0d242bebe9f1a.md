Now I have all the information needed. Let me trace the exact code path carefully.### Title
Missing `finalised_utxo_transfers` insertion in Near-destination fast-path enables UTXO double-finalization — (`near/omni-bridge/src/lib.rs`)

---

### Summary

When `utxo_fin_transfer_fast` handles a UTXO whose destination is `ChainKind::Near`, it calls `remove_fast_transfer` (erasing the fast-transfer record entirely) but **never calls `add_fin_utxo_transfer`**. The UtxoId is therefore absent from `finalised_utxo_transfers`. A second connector-initiated `ft_transfer_call` with the identical `UtxoId` finds no fast-transfer record and no finalization record, falls through to the normal settlement path, inserts the UtxoId via `add_fin_utxo_transfer`, and delivers a second full token payout to the recipient — unbacked by any additional BTC UTXO.

---

### Finding Description

**Entry point** — `ft_on_transfer` (line 268) dispatches to the private `utxo_fin_transfer` when the message type is `BridgeOnTransferMsg::UtxoFinTransfer`. The only caller-identity guard is:

```rust
require!(
    sender_id == &config.connector,
    BridgeError::SenderIsNotConnector.as_ref()
);
``` [1](#0-0) 

`sender_id` is the account that called `ft_transfer_call` on the token contract — i.e., the UTXO connector contract.

**Fast-path branch** — when a fast-transfer record exists for the derived `FastTransferId`, `utxo_fin_transfer` immediately returns the result of `utxo_fin_transfer_fast` without ever touching `finalised_utxo_transfers`:

```rust
if let Some(status) = self.get_fast_transfer_status(&fast_transfer.id()) {
    return self.utxo_fin_transfer_fast(fast_transfer, status, utxo_fin_transfer_msg);
}
// ← add_fin_utxo_transfer is only reached here, in the normal path
let required_storage_balance =
    self.add_fin_utxo_transfer(&utxo_fin_transfer_msg.get_transfer_id(origin_chain));
``` [2](#0-1) 

**Asymmetric state mutation inside `utxo_fin_transfer_fast`** — the Near-destination branch calls `remove_fast_transfer` (completely deletes the record), while the other-chain branch calls `mark_fast_transfer_as_finalised` (keeps the record with `finalised = true`):

```rust
let amount = if fast_transfer.get_destination_chain() == ChainKind::Near {
    self.remove_fast_transfer(&fast_transfer.id());   // record gone
    fast_transfer.amount
} else {
    self.mark_fast_transfer_as_finalised(&fast_transfer.id()); // record kept, finalised=true
    ...
};
``` [3](#0-2) 

Neither branch calls `add_fin_utxo_transfer`. For the other-chain branch this is safe because the `finalised = true` flag blocks a second call at the `require!(!fast_transfer_status.finalised, ...)` guard: [4](#0-3) 

For the **Near-destination branch** there is no such residual guard: the record is gone, `finalised_utxo_transfers` was never written, so the second call sees a clean slate.

**`add_fin_utxo_transfer` is the sole deduplication gate for the normal path:**

```rust
fn add_fin_utxo_transfer(&mut self, transfer_id: &UnifiedTransferId) -> NearToken {
    require!(
        self.finalised_utxo_transfers.insert(transfer_id),
        BridgeError::UtxoTransferAlreadyFinalised.as_ref()
    );
    ...
}
``` [5](#0-4) 

Because the Near fast-path never populates this set, the second call's `insert` succeeds and the full token amount is transferred to the recipient a second time.

---

### Impact Explanation

A second settlement of the same UTXO mints/transfers tokens to the recipient that are not backed by any additional locked BTC. This is a direct unbacked-supply / double-spend: the bridge's collateralization invariant is broken for every Near-destination fast transfer that is subsequently re-submitted.

---

### Likelihood Explanation

The UTXO connector is the required `sender_id`. Whether an unprivileged relayer can cause the connector to submit the same `UtxoId` twice depends on the connector's own deduplication. The bridge contract is supposed to be the authoritative settlement layer and must not rely on the connector for replay protection. The missing `add_fin_utxo_transfer` call is a structural gap: any connector-side bug, retry logic, or deliberate replay by whoever can call the connector will trigger the double-payout. The existing test suite covers the other-chain double-finalization path (`fails_on_double_finalization` uses a non-Near recipient) but does **not** test the Near-recipient double-finalization scenario. [6](#0-5) 

---

### Recommendation

In `utxo_fin_transfer_fast`, call `add_fin_utxo_transfer` before branching on destination chain, so the UtxoId is always recorded regardless of whether the fast-transfer record is removed or merely marked finalised:

```rust
fn utxo_fin_transfer_fast(...) {
    require!(!fast_transfer_status.finalised, ...);

    // Always record the UTXO as finalised first
    self.add_fin_utxo_transfer(&utxo_fin_transfer_msg.get_transfer_id(origin_chain));

    let amount = if fast_transfer.get_destination_chain() == ChainKind::Near {
        self.remove_fast_transfer(&fast_transfer.id());
        fast_transfer.amount
    } else {
        self.mark_fast_transfer_as_finalised(&fast_transfer.id());
        ...
    };
    ...
}
```

Alternatively, add a `finalised_utxo_transfers` membership check at the top of `utxo_fin_transfer` before the fast-path branch, mirroring the guard already present in the normal path.

---

### Proof of Concept

State-level reproduction (no privileged role required beyond connector access):

1. **Setup**: configure a UTXO connector and a Near-recipient fast transfer for `utxo_id = X`.
2. **`do_fast_transfer`**: relayer pre-pays the recipient; `fast_transfers[id(X)] = {relayer, finalised: false}`.
3. **First `do_utxo_fin_transfer`** (connector sends tokens via `ft_transfer_call`):
   - `utxo_fin_transfer` finds the fast-transfer record → calls `utxo_fin_transfer_fast`.
   - Near-destination branch: `remove_fast_transfer` deletes `fast_transfers[id(X)]`.
   - `finalised_utxo_transfers` is **not** updated.
   - Relayer receives the full token amount. ✓
4. **Second `do_utxo_fin_transfer`** with the same `utxo_id = X`:
   - `get_fast_transfer_status` returns `None` (record deleted).
   - Falls through to `add_fin_utxo_transfer` — **succeeds** (set is empty for this id).
   - `utxo_fin_transfer_to_near` is called; recipient receives the full token amount again. ✓
5. **Assert**: recipient balance increased by `2 × amount`; bridge issued tokens unbacked by any second UTXO.

The second call succeeds and the recipient balance increases twice, violating the at-most-once settlement invariant. [7](#0-6)

### Citations

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

**File:** near/omni-bridge/src/lib.rs (L2471-2474)
```rust
        require!(
            sender_id == &config.connector,
            BridgeError::SenderIsNotConnector.as_ref()
        );
```

**File:** near/omni-bridge/src/lib.rs (L2483-2489)
```rust
        if let Some(status) = self.get_fast_transfer_status(&fast_transfer.id()) {
            // TODO: check how to deal with failed send_tokens
            return self.utxo_fin_transfer_fast(fast_transfer, status, utxo_fin_transfer_msg);
        }

        let required_storage_balance =
            self.add_fin_utxo_transfer(&utxo_fin_transfer_msg.get_transfer_id(origin_chain));
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

**File:** near/omni-tests/src/utxo_fin_transfer.rs (L468-493)
```rust
    #[rstest]
    #[tokio::test]
    async fn fails_on_double_finalization(build_artifacts: &BuildArtifacts) -> anyhow::Result<()> {
        let env = TestEnv::new(build_artifacts).await?;
        let amount = 100_000_000;
        let utxo_msg = UtxoFinTransferMsg {
            utxo_id: default_utxo_id(),
            recipient: base_eoa_address(),
            relayer_fee: U128(1000),
            msg: String::default(),
        };

        let _ = do_fast_transfer(&env, amount, utxo_msg.clone()).await?;

        let _ = do_utxo_fin_transfer(&env, amount, utxo_msg.clone(), true, None).await?;
        let _ = do_utxo_fin_transfer(
            &env,
            amount,
            utxo_msg,
            true,
            Some("ERR_FAST_TRANSFER_ALREADY_FINALISED"),
        )
        .await?;

        Ok(())
    }
```
