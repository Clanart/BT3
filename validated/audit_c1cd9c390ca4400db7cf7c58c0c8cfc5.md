### Title
Missing binding between `ReplacementDepositData::old_move_txid` and an actual prior move-to-vault spend allows N-of-N signatures for a fabricated replacement deposit - (File: core/src/deposit.rs)

### Summary
`DepositData::get_deposit_scripts` derives the `ReplacementDepositScript` purely from the publicly-known `nofn_xonly_pk` and the attacker-supplied `old_move_txid` field, with no check that `old_move_txid` corresponds to a real, previously-existing move-to-vault transaction that was actually spent via the security council path. `Verifier::is_deposit_valid` only checks that the funded outpoint's value and scriptPubkey match the script computed from this attacker-controlled `old_move_txid`, so a self-funded UTXO built against an arbitrary/fictitious `old_move_txid` passes validation and gets N-of-N signed.

### Finding Description
The binding that should hold is: `ReplacementDepositData::old_move_txid == txid of a real move-to-vault transaction that was actually consumed via the `Multisig::from_security_council` unlocking path`.

Tracing `DepositData::get_deposit_scripts` for the `ReplacementDeposit` branch: [1](#0-0) 
the scripts are built only from `nofn_xonly_pk` (public verifier aggregate key) and `replacement_deposit_data.old_move_txid` (fully attacker-chosen), plus `self.security_council` (public config). No lookup against the database, no on-chain check that `old_move_txid` exists, was ever a move-to-vault output, or was spent by the security council multisig.

`Verifier::is_deposit_valid` consumes this: [2](#0-1) 
It computes `expected_scriptpubkey` from `get_deposit_scripts` (which embeds the attacker's fabricated `old_move_txid`), then only verifies that the funded UTXO's `value` equals `bridge_amount` and its `script_pubkey` matches this expected script, followed by a block-height confirmation check: [3](#0-2) 
At no point does it query the DB or chain for whether `old_move_txid` is a real, previously issued move-to-vault txid, or whether it was ever unlocked by the security council. Since the taproot address is fully computable offline from public data (`nofn_xonly_pk`, `security_council`) plus an arbitrary txid, an attacker can:
1. Pick any 32-byte value as `old_move_txid` (random, or a real but already-completed/nonexistent txid).
2. Compute the `ReplacementDepositScript` + `Multisig::from_security_council` taproot address.
3. Fund a UTXO `R` with exactly `bridge_amount` to that address.
4. Submit `DepositParams{deposit_type: ReplacementDeposit{old_move_txid}}` to the aggregator, which forwards to `deposit_sign` on each verifier.
5. Each verifier's `is_deposit_valid` passes because value/script/height checks succeed — the fabricated `old_move_txid` is never cross-checked against reality.
6. N-of-N signs a fresh move-to-vault transaction for `R`, effectively minting a new bridge-recognized vault entry unconnected to any genuine prior deposit or replacement event.

Existing guards do not catch this: `is_deposit_valid` checks operator uniqueness/collateral, script/value/scriptPubkey match, and block height, but never resolves or validates `old_move_txid` against chain state or the verifier database.

### Impact Explanation
This produces N-of-N partial signatures for a move-to-vault transaction that is not tied to any legitimate prior deposit replacement, matching the Critical category "N-of-N partial signatures for an unauthorised spend." The attacker's self-funded `bridge_amount` BTC becomes an officially bridge-recognized vault UTXO through a path meant only for security-council-authorized bug fixes/migrations, and could subsequently be used to claim a Citrea-side mint or withdrawal credit without ever having gone through the legitimate `BaseDeposit` flow or a genuine council-authorized replacement — i.e., without the checks/semantics tied to `ReplacementDeposit` (e.g., ensuring exactly one live replacement per old deposit, or preventing arbitrary "conjuring" of vault entries). This is repeatable per attacker-funded UTXO and does not depend on any specific operator.

### Likelihood Explanation
The attacker needs only to fund a taproot output with exactly `bridge_amount` BTC (protocol-defined constant) plus fees, and to know the public `nofn_xonly_pk` and `security_council` config — both public. No privileged role, key share, or verifier/aggregator access is required beyond the standard public gRPC deposit-request interface. This is straightforward and cheap (cost equals `bridge_amount` + transaction fees, which the attacker recovers as soon as the move-to-vault occurs), and repeatable for each new `old_move_txid` value chosen.

### Recommendation
In `get_deposit_scripts` or `Verifier::is_deposit_valid`, require that `old_move_txid` refers to a real, existing move-to-vault transaction previously known to the verifier (e.g., persisted in the DB from a prior `BaseDeposit`/`ReplacementDeposit`), and additionally verify that this specific `old_move_txid`'s output was already spent via the security-council multisig unlocking path (or via an authorized aggregator-only replacement-issuance API) before signing a new `ReplacementDeposit`. Do not accept `ReplacementDeposit` requests whose `old_move_txid` cannot be resolved to a bridge-tracked deposit.

### Proof of Concept
```rust
// cargo test in core/src/verifier tests (new, not in excluded test dirs)
// 1. Construct a DepositData with DepositType::ReplacementDeposit { old_move_txid: <random Txid> }
//    using a real nofn_xonly_pk/security_council from a running verifier's config.
// 2. Call deposit_data.get_deposit_scripts(paramset) and build the expected taproot scriptPubkey.
// 3. Fund (in regtest) a fresh UTXO with value == bridge_amount to that scriptPubkey,
//    where old_move_txid was never actually broadcast/confirmed on-chain.
// 4. Call Verifier::is_deposit_valid(&deposit_data) directly.
// Assertion (binding check):
//    assert!(chain_contains_txid(old_move_txid) == false);   // left side of the binding: fabricated
//    // currently: is_deposit_valid returns Ok(()) -- expected: Err(BridgeError::InvalidDeposit(_))
//    assert!(result.is_err(), "is_deposit_valid should reject a ReplacementDeposit whose old_move_txid is not a real prior move-to-vault txid spent via the security council path");
```

### Citations

**File:** core/src/deposit.rs (L206-218)
```rust
            DepositType::ReplacementDeposit(replacement_deposit_data) => {
                let deposit_script: Arc<dyn SpendableScript> =
                    Arc::new(ReplacementDepositScript::new(
                        nofn_xonly_pk,
                        replacement_deposit_data.old_move_txid,
                    ));
                let security_council_script: Arc<dyn SpendableScript> = Arc::new(
                    Multisig::from_security_council(self.security_council.clone()),
                );

                Ok(vec![deposit_script, security_council_script])
            }
        }
```

**File:** core/src/verifier.rs (L659-705)
```rust
        // check if deposit script in deposit_outpoint is valid
        let deposit_scripts: Vec<ScriptBuf> = deposit_data
            .get_deposit_scripts(self.config.protocol_paramset())?
            .into_iter()
            .map(|s| s.to_script_buf())
            .collect();
        // what the deposit scriptpubkey is in the deposit_outpoint should be according to the deposit data
        let expected_scriptpubkey = create_taproot_address(
            &deposit_scripts,
            None,
            self.config.protocol_paramset().network,
        )
        .0
        .script_pubkey();
        let deposit_outpoint = deposit_data.get_deposit_outpoint();
        let deposit_txid = deposit_outpoint.txid;
        let deposit_tx = self
            .rpc
            .get_tx_of_txid(&deposit_txid)
            .await
            .wrap_err("Deposit tx could not be found on chain")?;
        let deposit_txout_in_chain = deposit_tx
            .output
            .get(deposit_outpoint.vout as usize)
            .ok_or(eyre::eyre!(
                "Deposit vout not found in tx {}, vout: {}",
                deposit_txid,
                deposit_outpoint.vout
            ))?;
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

**File:** core/src/verifier.rs (L706-731)
```rust
        // check if deposit outpoint is included in a block with height >= start_height
        let tx_info = self
            .rpc
            .get_raw_transaction_info(&deposit_txid, None)
            .await
            .wrap_err("Failed to get deposit transaction info")?;
        let blockhash = tx_info.blockhash.ok_or_else(|| {
            BridgeError::InvalidDeposit("Deposit transaction is not confirmed".to_string())
        })?;
        let block_height = self
            .rpc
            .get_block_info(&blockhash)
            .await
            .wrap_err(format!(
                "Failed to get block info for deposit tx block hash: {blockhash}",
            ))?
            .height;
        let start_height = self.config.protocol_paramset().start_height;
        if (block_height as u32) < start_height {
            let reason = format!(
                "Deposit transaction is included in a block with height {block_height} which is less than start_height {start_height}",
            );
            tracing::error!("{reason}");
            return Err(BridgeError::InvalidDeposit(reason));
        }
        Ok(())
```
