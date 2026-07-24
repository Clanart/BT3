### Title
Missing movetx input-outpoint binding in `send_move_to_vault_tx` allows deposit-state corruption and potential double-spend — (File: `core/src/rpc/aggregator.rs`)

---

### Summary

`send_move_to_vault_tx` accepts an arbitrary caller-supplied raw transaction and a `deposit_outpoint` parameter, validates the transaction's *outputs* (count, value, script pubkeys), but **never checks that the transaction's single input actually spends `deposit_outpoint`**. Because the aggregator enforces no client-certificate requirement, any party that can reach the aggregator's gRPC port can submit a structurally valid movetx that spends a completely different UTXO, causing the bridge to record the wrong move-tx for a deposit and leaving the real deposit UTXO permanently unspent.

---

### Finding Description

`send_move_to_vault_tx` performs three structural checks on the submitted transaction: [1](#0-0) 

1. Input/output count (`input.len() == 1 && output.len() == 2`)
2. Output values (`output[0].value == bridge_amount && output[1].value == 0`)
3. Output script pubkeys (vault taproot address and anchor) [2](#0-1) 

After passing those checks the transaction is inserted into the tx-sender queue tagged with the caller-supplied `deposit_outpoint`: [3](#0-2) 

**The missing check**: nowhere does the function assert

```rust
movetx.input[0].previous_output == deposit_outpoint
```

The `deposit_outpoint` value is used only as DB metadata, never to validate the transaction's actual input. An attacker can therefore supply a movetx whose single input spends any UTXO they control (with `bridge_amount` value), while claiming it belongs to a victim's `deposit_outpoint`.

The legitimate movetx is produced by `create_move_to_vault_txhandler`, which hard-wires the deposit outpoint as the input: [4](#0-3) 

`new_deposit` returns this correctly-bound, N-of-N–signed transaction. `send_move_to_vault_tx` is the separate broadcast step, but it accepts *any* raw transaction, not only the one produced by `new_deposit`.

---

### Impact Explanation

**Deposit-state corruption (confirmed):** The bridge DB records the attacker-supplied movetx as the canonical move-tx for `deposit_outpoint`. The real deposit UTXO is never spent. The bridge state machine believes the deposit is finalised; the actual deposit UTXO remains live and claimable by the depositor after the `user_takes_after` timelock.

**Potential double-spend (conditional):** If Citrea's bridge contract validates only that `bridge_amount` arrived at the vault address (and not that the movetx input equals `deposit_outpoint`), a depositor can:

1. Deposit `bridge_amount` to the deposit address (`deposit_outpoint`).
2. Construct a movetx spending a separate `bridge_amount` UTXO they own, with correct vault outputs.
3. Submit it via `send_move_to_vault_tx` referencing `deposit_outpoint`.
4. Obtain Citrea credit from the fake movetx.
5. Reclaim `deposit_outpoint` after the timelock.

Net result: the depositor holds `bridge_amount` on Citrea **and** recovers `bridge_amount` on Bitcoin — a full bridge_amount theft.

---

### Likelihood Explanation

The aggregator enforces **no client-certificate requirement** on its gRPC endpoints: [5](#0-4) 

> "The aggregator does not enforce client certificates but does use TLS for encryption." [6](#0-5) 

Any party that can open a TLS connection to the aggregator port can call `send_move_to_vault_tx`. The attacker needs only:

- A UTXO they control with value ≥ `bridge_amount`.
- Knowledge of a pending `deposit_outpoint` (observable on-chain).
- The ability to construct a Bitcoin transaction with the correct vault output script (derivable from the public verifier keys, which are returned by `get_nofn_aggregated_xonly_pk`).

All three are available to an unprivileged external party.

---

### Recommendation

Add an explicit binding check immediately after deserialising the movetx:

```rust
// Verify the movetx actually spends the claimed deposit outpoint
if movetx.input[0].previous_output != deposit_outpoint {
    return Err(Status::invalid_argument(format!(
        "movetx input {:?} does not spend the claimed deposit outpoint {:?}",
        movetx.input[0].previous_output,
        deposit_outpoint,
    )));
}
```

This mirrors the pattern already used in `is_deposit_valid` (verifier-side), which checks both the amount and the script pubkey of the deposit outpoint on-chain: [7](#0-6) 

---

### Proof of Concept

```
1. Observe a pending deposit_outpoint D on Bitcoin (bridge_amount = B).

2. Query aggregator: GetNofnAggregatedXonlyPk → derive vault script pubkey V.

3. Construct movetx_fake:
     input[0]  = attacker_utxo (value = B, controlled by attacker)
     output[0] = TxOut { value: B, script_pubkey: V }   // vault
     output[1] = TxOut { value: 0, script_pubkey: OP_RETURN anchor }
   Sign with attacker's key.

4. Call aggregator.SendMoveToVaultTx({
       raw_tx:          movetx_fake,
       deposit_outpoint: D
   })

   → Passes all three checks (count, values, script pubkeys).
   → Inserted into tx_sender queue tagged with D.

5. TxSender broadcasts movetx_fake; it confirms on Bitcoin.

6. Bridge DB now maps D → movetx_fake.txid.
   D is still unspent.

7. After user_takes_after blocks, depositor reclaims D.

8. If Citrea accepts movetx_fake as proof of deposit D,
   depositor also holds bridge_amount on Citrea → double-spend.
```

The root cause is the same class as the external report: a "transfer" function that validates the *destination* (vault outputs) but not the *source* (which UTXO is actually being moved), directly analogous to calling `transferFrom` without verifying the token actually came from the expected address.

### Citations

**File:** core/src/rpc/aggregator.rs (L2019-2036)
```rust
            // check if transaction is a movetx
            if movetx.input.len() != 1 || movetx.output.len() != 2 {
                return Err(Status::invalid_argument(
                    "Transaction is not a movetx, input or output lengths are not correct",
                ));
            }
            // check output values
            // movetx always has 0 sat anchor output
            if !(movetx.output[0].value == self.config.protocol_paramset().bridge_amount
                && movetx.output[1].value == Amount::from_sat(0))
            {
                return Err(Status::invalid_argument(format!(
                    "Transaction is not a movetx, output sat values are not correct, should be ({}, 0), got ({}, {})",
                    self.config.protocol_paramset().bridge_amount,
                    movetx.output[0].value,
                    movetx.output[1].value,
                )));
            }
```

**File:** core/src/rpc/aggregator.rs (L2062-2073)
```rust
            if !(movetx.output[1].script_pubkey
                == anchor_output(self.config.protocol_paramset().anchor_amount()).script_pubkey
                && movetx.output[0].script_pubkey == bridge_script_pubkey)
            {
                return Err(Status::invalid_argument(
                    format!("Transaction is not a movetx, output scriptpubkeys are not correct, expected: (vault: {:?}, anchor: {:?}), got: (vault: {:?}, anchor: {:?})",
                    bridge_script_pubkey,
                    anchor_output(self.config.protocol_paramset().anchor_amount()).script_pubkey,
                    movetx.output[0].script_pubkey,
                    movetx.output[1].script_pubkey,
                )));
            }
```

**File:** core/src/rpc/aggregator.rs (L2075-2095)
```rust
            let mut dbtx = self.db.begin_transaction().await?;
            self.tx_sender
                .insert_try_to_send(
                    &mut dbtx,
                    Some(TxMetadata {
                        deposit_outpoint: Some(deposit_outpoint),
                        operator_xonly_pk: None,
                        round_idx: None,
                        kickoff_idx: None,
                        tx_type: TransactionType::MoveToVault,
                    }),
                    &movetx,
                    FeePayingType::CPFP,
                    None,
                    &[],
                    &[],
                    &[],
                    &[],
                )
                .await
                .map_to_status()?;
```

**File:** core/src/builder/transaction/mod.rs (L319-343)
```rust
    Ok(TxHandlerBuilder::new(TransactionType::MoveToVault)
        .with_version(NON_STANDARD_V3)
        .add_input(
            NormalSignatureKind::NotStored,
            SpendableTxIn::from_scripts(
                deposit_outpoint,
                paramset.bridge_amount,
                deposit_scripts,
                None,
                paramset.network,
            ),
            SpendPath::ScriptSpend(0),
            DEFAULT_SEQUENCE,
        )
        .add_output(UnspentTxOut::from_scripts(
            paramset.bridge_amount,
            vec![nofn_script, security_council_script],
            None,
            paramset.network,
        ))
        // always use 0 sat anchor for move_tx, this will keep the amount in move to vault tx exactly the bridge amount
        .add_output(UnspentTxOut::from_partial(anchor_output(Amount::from_sat(
            0,
        ))))
        .finalize())
```

**File:** docs/usage.md (L203-203)
```markdown
The aggregator does not enforce client certificates but does use TLS for encryption.
```

**File:** core/src/servers.rs (L306-308)
```rust
    if config.client_verification {
        tracing::warn!("Client verification is enabled on aggregator gRPC server",);
    }
```

**File:** core/src/verifier.rs (L688-705)
```rust
        if deposit_txout_in_chain.value != self.config.protocol_paramset().bridge_amount {
            let reason = format!(
                "Deposit amount is not correct, expected {}, got {}",
                self.config.protocol_paramset().bridge_amount,
                deposit_txout_in_chain.value
            );
            tracing::error!("{reason}");
            return Err(BridgeError::InvalidDeposit(reason));
        }
        if deposit_txout_in_chain.script_pubkey != expected_scriptpubkey {
            let reason = format!(
                "Deposit script pubkey in deposit outpoint does not match the deposit data, expected {:?}, got {:?}",
                expected_scriptpubkey,
                deposit_txout_in_chain.script_pubkey
            );
            tracing::error!("{reason}");
            return Err(BridgeError::InvalidDeposit(reason));
        }
```
