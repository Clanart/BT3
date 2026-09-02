Based on my investigation:

`Verifier::is_deposit_valid` (core/src/verifier.rs:541-732), when handling `DepositType::ReplacementDeposit`, calls `deposit_data.get_deposit_scripts(...)` [1](#0-0)  which builds a `ReplacementDepositScript::new(nofn_xonly_pk, replacement_deposit_data.old_move_txid)` purely from the attacker-supplied `old_move_txid` field [2](#0-1) . The only check performed afterward is that this self-declared script's taproot address matches the scriptPubkey of the deposit UTXO on-chain, that its value equals `bridge_amount`, and that it's confirmed above `start_height` [3](#0-2) . There is no check anywhere in `is_deposit_valid` that the deposit transaction's *input* actually spends the referenced `old_move_txid` output, nor any check that a security-council/old-nofn signature authorized spending that specific old move-to-vault UTXO.

`ReplacementDepositScript::to_script_buf` embeds the `old_move_txid` bytes inside an unexecuted `OP_FALSE OP_IF ... OP_ENDIF` branch [4](#0-3) , exactly like `BaseDepositScript` embeds an EVM address — this is pure commitment data for downstream (Citrea) consumption, not a script-enforced binding. The real security-council-authorized replacement flow, as constructed in `create_replacement_deposit_txhandler`, spends the actual old move-to-vault outpoint (`input_outpoint`) using either `CheckSig(old_nofn_xonly_pk)` or `Multisig(security_council)` [5](#0-4) . But this transaction-construction helper is `#[cfg(test)]`-only [6](#0-5) , and it is never invoked or its constraints enforced by verifier-side `is_deposit_valid` in the production `deposit_sign` path [7](#0-6) . The `deposit_outpoint` a caller supplies to `new_deposit`/`deposit_sign` is an arbitrary attacker-chosen UTXO — verifiers never fetch its previous_output/input to confirm it descends from `old_move_txid`.

This matches the binding claimed to be broken: `old_move_txid` (attacker's field, arbitrary) is asserted equal to "the actual move tx the security council spent to authorize this specific replacement," but no code path checks this equality — `is_deposit_valid` only checks the *output* script/amount/confirmation of the new self-funded deposit UTXO, never the *provenance* of that UTXO or the state (spent/unspent) of the txid it claims to replace.

I was not able to fully trace the Citrea-side contract logic (`setReplaceScript`/`update_nofn_aggregated_key` consumer) that ultimately interprets `old_move_txid` to determine "double counting" impact, since that logic lives in the Citrea contract (out of scope per the rules — "Citrea contract... defects with no path through this repository" are excluded unless the Clementine-repo path itself causes the divergence). However, within this repo, the missing cross-check is real and reachable by an unprivileged depositor: any attacker can self-fund a `ReplacementDeposit`-shaped taproot output citing any confirmed foreign move-to-vault txid, and verifiers will sign it as a valid N-of-N deposit since `is_deposit_valid` never rejects it.

### Title
Missing verification that `old_move_txid` corresponds to a spent, security-council-authorized move-to-vault - (core/src/verifier.rs:541-732, core/src/deposit.rs:206-217)

### Summary
`Verifier::is_deposit_valid` validates a `ReplacementDeposit` solely by checking that the self-funded deposit UTXO's scriptPubkey/amount matches a script built from the attacker-supplied `old_move_txid`, never verifying that the referenced move-to-vault transaction is real, unspent-until-replacement, or that the security council actually authorized spending it to create this specific replacement. An attacker can therefore fabricate a `ReplacementDeposit` citing any confirmed foreign move-to-vault txid and get verifiers to sign it via the N-of-N `deposit_sign` flow.

### Finding Description
The claimed binding is: `ReplacementDepositData::old_move_txid == txid of the move-to-vault transaction that the security council specifically authorized spending to create this replacement deposit`. Tracing `deposit_sign` → `is_deposit_valid` (core/src/verifier.rs:866-732), the only checks are: security council config match, watchtower/verifier/operator uniqueness and DB membership, and finally that `deposit_data.get_deposit_scripts()` (built from the caller-supplied `old_move_txid`) produces a taproot scriptPubkey equal to the actual on-chain scriptPubkey/value of `deposit_data.get_deposit_outpoint()`, confirmed above `start_height`. Nothing dereferences `old_move_txid` itself to fetch that transaction, check it exists, check it is unspent (about to be replaced) versus already-replaced/spent elsewhere, or check that the deposit outpoint's own input actually consumes that old move-to-vault output under the `CheckSig(old_nofn)/Multisig(security_council)` script from `create_replacement_deposit_txhandler`. Since `ReplacementDepositScript` merely embeds the txid bytes in an unexecuted `OP_FALSE OP_IF` branch (a data commitment, not a spending constraint), any self-funded taproot output with the correct script structure and bridge_amount value passes `is_deposit_valid` regardless of what `old_move_txid` value is chosen.

### Impact Explanation
Root-cause-wise, this is a repo-local verification gap: verifiers will co-sign (N-of-N partial signatures) a move-to-vault for a `ReplacementDeposit` whose `old_move_txid` binding is fabricated. Per the stated severity taxonomy, an "N-of-N partial signature for an unauthorised spend" categorization would require the resulting spend itself to be unauthorized — here the *new* deposit output is spent honestly (attacker funded it), so no bridge UTXO is drained by this alone within this repo's logic. The downstream double-counting/credit-manipulation impact described in the prompt occurs on the Citrea contract side interpreting `old_move_txid`, which is out of scope per the audit rules (defects requiring a Citrea-contract path with no in-repo enforcement are excluded).

### Likelihood Explanation
Trivial precondition: attacker needs only to know any confirmed move-to-vault txid (public data) and be able to fund a taproot output of `bridge_amount` with the correctly-shaped `ReplacementDepositScript`/`Multisig` script — both within reach of an unprivileged depositor with BTC and fee capability. The call sequence (`new_deposit` → `send_move_to_vault_tx` → `deposit_sign`) is fully exposed via the aggregator's gRPC surface.

### Recommendation
In `is_deposit_valid` (core/src/verifier.rs), for `DepositType::ReplacementDeposit`, fetch the transaction referenced by `old_move_txid`, verify it was a legitimate prior move-to-vault (tracked in the verifier's own DB/deposit history), verify it is unspent or spent exactly by the transaction whose output is `deposit_data.get_deposit_outpoint()` (i.e., check that outpoint's `previous_output`/input actually consumes `old_move_txid`'s vault output), and require the spending witness to satisfy the `Multisig(security_council)` leaf, not just the `CheckSig` leaf.

### Proof of Concept
`cargo test` (regtest, no automation/Citrea feature) plan: create a legitimate deposit/move-to-vault for depositor A; separately, as attacker B, construct a self-funded UTXO matching `generate_replacement_deposit_address(old_move_txid = A's move_txid, ...)`; call `new_deposit`/`deposit_sign` for B's outpoint; assert that verifiers currently return success (proving the gap), then assert the fix should reject with `BridgeError::InvalidDeposit` because B's deposit outpoint's input does not spend A's move-to-vault UTXO.

### Citations

**File:** core/src/verifier.rs (L659-664)
```rust
        // check if deposit script in deposit_outpoint is valid
        let deposit_scripts: Vec<ScriptBuf> = deposit_data
            .get_deposit_scripts(self.config.protocol_paramset())?
            .into_iter()
            .map(|s| s.to_script_buf())
            .collect();
```

**File:** core/src/verifier.rs (L665-731)
```rust
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

**File:** core/src/verifier.rs (L866-886)
```rust
    pub async fn deposit_sign(
        &self,
        mut deposit_data: DepositData,
        session_id: u128,
        mut agg_nonce_rx: mpsc::Receiver<AggregatedNonce>,
    ) -> Result<mpsc::Receiver<Result<PartialSignature, BridgeError>>, BridgeError> {
        self.citrea_client
            .check_nofn_correctness(deposit_data.get_nofn_xonly_pk()?)
            .await?;

        self.is_deposit_valid(&mut deposit_data).await?;

        // set deposit data to db before starting to sign, ensures that if the deposit data already exists in db, it matches the one
        // given by the aggregator currently. We do not want to sign 2 different deposits for same deposit_outpoint
        self.db
            .insert_deposit_data_if_not_exists(
                None,
                &mut deposit_data,
                self.config.protocol_paramset(),
            )
            .await?;
```

**File:** core/src/deposit.rs (L206-217)
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
```

**File:** core/src/builder/script.rs (L526-538)
```rust
    fn to_script_buf(&self) -> ScriptBuf {
        let citrea_replace: [u8; 13] = "citreaReplace".as_bytes().try_into().expect("length == 13");

        Builder::new()
            .push_x_only_key(&self.0)
            .push_opcode(OP_CHECKSIG)
            .push_opcode(OP_FALSE)
            .push_opcode(OP_IF)
            .push_slice(citrea_replace)
            .push_slice(self.1.as_byte_array())
            .push_opcode(OP_ENDIF)
            .into_script()
    }
```

**File:** core/src/builder/transaction/mod.rs (L403-440)
```rust
#[cfg(test)]
pub fn create_replacement_deposit_txhandler(
    old_move_txid: Txid,
    input_outpoint: OutPoint,
    old_nofn_xonly_pk: XOnlyPublicKey,
    new_nofn_xonly_pk: XOnlyPublicKey,
    paramset: &'static ProtocolParamset,
    security_council: SecurityCouncil,
) -> Result<TxHandler, BridgeError> {
    Ok(TxHandlerBuilder::new(TransactionType::ReplacementDeposit)
        .with_version(NON_STANDARD_V3)
        .add_input(
            NormalSignatureKind::NoSignature,
            SpendableTxIn::from_scripts(
                input_outpoint,
                paramset.bridge_amount,
                vec![
                    Arc::new(CheckSig::new(old_nofn_xonly_pk)),
                    Arc::new(Multisig::from_security_council(security_council.clone())),
                ],
                None,
                paramset.network,
            ),
            crate::builder::script::SpendPath::ScriptSpend(0),
            DEFAULT_SEQUENCE,
        )
        .add_output(UnspentTxOut::from_scripts(
            paramset.bridge_amount,
            vec![
                Arc::new(ReplacementDepositScript::new(
                    new_nofn_xonly_pk,
                    old_move_txid,
                )),
                Arc::new(Multisig::from_security_council(security_council)),
            ],
            None,
            paramset.network,
        ))
```
