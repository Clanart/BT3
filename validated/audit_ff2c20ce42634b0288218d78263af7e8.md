Confirmed: `insert_deposit_data_if_not_exists` (core/src/database/operator.rs:359-430) only enforces uniqueness on `deposit_outpoint`, and dedupes by whether the *exact* `DepositData` matches for that outpoint — it never checks `old_move_txid` for prior use, nor whether the transaction containing `old_move_txid`'s output has been spent. Combined with `is_deposit_valid` only checking script/amount/height of the attacker's own `deposit_outpoint` (never the state or spend of `old_move_txid`), this confirms the exploit path is real and unguarded.

### Title
`ReplacementDeposit` accepted by `is_deposit_valid` without verifying that `old_move_txid`'s move-to-vault output is actually spent by the cited replacement transaction, allowing a forged parallel deposit lineage - (File: `core/src/verifier.rs`)

### Summary
`Verifier::is_deposit_valid` validates a `ReplacementDeposit` purely by checking that the attacker-supplied `deposit_outpoint`'s script-pubkey and amount match `ReplacementDepositScript(nofn_xonly_pk, old_move_txid) + Multisig(security_council)`, and that the outpoint is confirmed above `start_height`. It never checks that `old_move_txid`'s move-to-vault output has been spent, nor that the transaction funding `deposit_outpoint` actually consumed that output. An unprivileged user can therefore fund a brand-new UTXO of their own with a script that merely *commits to* a real, live, unspent `old_move_txid`, and get full N-of-N signing for a "replacement" move-to-vault transaction while the original deposit remains completely untouched and reimbursable.

### Finding Description
The broken binding: the protocol intends `deposit_data.get_deposit_outpoint()` for a `ReplacementDeposit` to be the output of a `replacement_deposit_tx` (built by `create_replacement_deposit_txhandler`, `core/src/builder/transaction/mod.rs:404-446`) whose **input** is `OutPoint{ old_move_txid, DepositInMove }`, spent via `CheckSig(old_nofn_xonly_pk) OR Multisig(security_council)`. I.e. the binding should be:
`deposit_outpoint.txid's funding transaction.input[0].previous_output == (old_move_txid, DepositInMove)`.

`Verifier::is_deposit_valid` (`core/src/verifier.rs:659-705`) never checks this binding. It only:
1. Rebuilds expected scripts from `deposit_data.get_deposit_scripts()` (`core/src/deposit.rs:206-217`), which for `ReplacementDeposit` produces `ReplacementDepositScript::new(nofn_xonly_pk, old_move_txid)` + `Multisig(security_council)` — this script only *embeds* `old_move_txid` as opcode-pushed data inside an `OP_FALSE OP_IF ... OP_ENDIF` unspendable branch (`core/src/builder/script.rs:526-538`), it does not constrain the funding transaction's inputs at all.
2. Confirms `deposit_txout_in_chain.script_pubkey == expected_scriptpubkey` and `deposit_txout_in_chain.value == bridge_amount` (`core/src/verifier.rs:688-705`).
3. Confirms the deposit tx is confirmed above `start_height` (`core/src/verifier.rs:706-730`).

None of these checks touch `old_move_txid`'s outpoint state (spent/unspent) or the deposit transaction's inputs. `insert_deposit_data_if_not_exists` (`core/src/database/operator.rs:359-430`) also only dedupes on `deposit_outpoint`, and never checks whether `old_move_txid` has already been cited/consumed by a prior replacement.

Exploit flow: an attacker observes any real, live, unspent `move_to_vault_txid` (their own from a prior `BaseDeposit`, or any public one) via `run_single_deposit`/on-chain scan. They construct a fresh Taproot output funded with their own BTC using scripts `[ReplacementDepositScript(current_nofn_xonly_pk, old_move_txid), Multisig(security_council)]`, broadcast it, and call the aggregator's `new_deposit` gRPC (`core/src/rpc/aggregator.rs:1449` → `deposit_sign`/`deposit_finalize` → `Verifier::is_deposit_valid`) citing `DepositType::ReplacementDeposit{old_move_txid}` and this new self-funded outpoint. `is_deposit_valid` passes since script/amount/height all check out for the attacker-controlled outpoint. Verifiers proceed to N-of-N sign a legitimate move-to-vault transaction for this new deposit (per `deposit_finalize`, `core/src/verifier.rs:982-1018`), all while `old_move_txid`'s original vault output is never touched.

Existing guards fail because: `is_deposit_valid` is script/amount/height-only and has no cross-reference to `old_move_txid`'s outpoint state; `insert_deposit_data_if_not_exists` dedupes by `deposit_outpoint` (a fresh outpoint per attack) not by `old_move_txid`; and no RPC or DB layer enforces that a given `old_move_txid` can only be cited once by a spend that actually consumes it.

### Impact Explanation
The result is two parallel, fully N-of-N-signed move-to-vault lineages both nominally tied (via script commitment only) to the same `old_move_txid` identity: the original (untouched, still fundable/reimbursable by its rightful operator) and the forged "replacement" (attacker-funded, freshly signed). If this forged replacement is registered on the Citrea Bridge contract via `replaceDeposit` (as in the legitimate flow, `core/src/test/common/citrea/mod.rs:497-595`) citing the same `deposit_id`, Citrea's move_to_vault_txid mapping for that deposit can be overwritten/duplicated while the original vault UTXO remains live — risking incorrect operator reimbursement routing or the original deposit's owner losing their ability to have withdrawals correctly tied to the correct vault UTXO. This breaks the intended single-claim guarantee that a `ReplacementDeposit` can only exist if the cited `old_move_txid` output was actually consumed via the authorized (nofn + security council) path. This is repeatable for every live deposit and does not require any privileged role — only broadcasting a self-funded output with the right script and calling the public aggregator gRPC.

### Likelihood Explanation
No special preconditions beyond a running bridge with any live `BaseDeposit`/move-to-vault transaction (trivially obtainable, e.g., via `run_single_deposit`). Attacker cost is only the `bridge_amount` BTC needed to fund their own new deposit outpoint plus fees — funds they retain control of the *script* structure for, since they choose `old_move_txid` freely and are not required to prove any spend of it. Fully feasible via standard Bitcoin transaction construction and the public aggregator `new_deposit` gRPC; no verifier, operator, or security-council collusion required. Repeatable against every deposit on the bridge.

### Recommendation
In `Verifier::is_deposit_valid` (`core/src/verifier.rs`), for `DepositType::ReplacementDeposit`, additionally fetch the transaction that funds `deposit_data.get_deposit_outpoint().txid` and verify that its `input[0].previous_output` equals `OutPoint{ txid: old_move_txid, vout: UtxoVout::DepositInMove.get_vout() }` (i.e., the replacement deposit output must literally be created by spending the cited old move-to-vault output). Additionally verify that `old_move_txid`'s output was spent via the expected `CheckSig(old_nofn_xonly_pk)`/`Multisig(security_council)` script paths, and enforce (at the DB layer) that each `old_move_txid` can be cited by at most one successfully finalized `ReplacementDeposit`.

### Proof of Concept
```rust
// cargo test --features automation replacement_deposit_without_spending_old_move_output

#[tokio::test(flavor = "multi_thread")]
#[cfg(feature = "automation")]
async fn replacement_deposit_without_spending_old_move_output() {
    let mut config = create_test_config_with_thread_name().await;
    let WithProcessCleanup(_, ref rpc, _, _) = create_regtest_rpc(&mut config).await;
    let actors = create_actors::<MockCitreaClient>(&config).await;

    // 1. Get a live, unspent move_to_vault_txid via a normal base deposit.
    let (_deposit_info, old_move_txid, _blockhash, _) =
        run_single_deposit::<MockCitreaClient>(&mut config, rpc.clone(), None, &actors, None)
            .await
            .unwrap();

    // BINDING (before): old_move_txid's DepositInMove output is unspent.
    assert!(!rpc.is_utxo_spent(&OutPoint {
        txid: old_move_txid,
        vout: UtxoVout::DepositInMove.get_vout(),
    }).await.unwrap());

    let nofn_xonly_pk = actors.get_nofn_aggregated_xonly_pk().unwrap();

    // 2. Attacker funds a FRESH outpoint themselves (not spending old_move_txid)
    //    with a taproot address matching [ReplacementDepositScript(nofn_pk, old_move_txid), Multisig(sec_council)].
    let scripts: Vec<Arc<dyn SpendableScript>> = vec![
        Arc::new(ReplacementDepositScript::new(nofn_xonly_pk, old_move_txid)),
        Arc::new(Multisig::from_security_council(config.security_council.clone())),
    ];
    let (address, _) = create_taproot_address(
        &scripts.iter().map(|s| s.to_script_buf()).collect::<Vec<_>>(),
        None,
        config.protocol_paramset().network,
    );
    let forged_outpoint = rpc
        .send_to_address(&address, config.protocol_paramset().bridge_amount)
        .await
        .unwrap();
    rpc.mine_blocks(18).await.unwrap();

    // 3. Submit as ReplacementDeposit citing the still-unspent old_move_txid.
    let deposit_info = DepositInfo {
        deposit_outpoint: forged_outpoint,
        deposit_type: DepositType::ReplacementDeposit(ReplacementDepositData { old_move_txid }),
    };
    let mut aggregator = actors.get_aggregator();
    aggregator.setup(Request::new(Empty {})).await.unwrap();

    let result = aggregator
        .new_deposit(clementine::Deposit::from(deposit_info))
        .await;

    // BINDING (after, expected fix): old_move_txid's output still unspent =>
    // is_deposit_valid MUST reject this ReplacementDeposit.
    assert!(
        result.is_err(),
        "is_deposit_valid accepted a ReplacementDeposit citing a live, unspent old_move_txid \
         without ever spending its output — forged parallel deposit lineage created"
    );

    // Confirm old_move_txid output is STILL unspent after the "replacement" was signed,
    // proving no actual replacement occurred.
    assert!(!rpc.is_utxo_spent(&OutPoint {
        txid: old_move_txid,
        vout: UtxoVout::DepositInMove.get_vout(),
    }).await.unwrap());
}
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

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

**File:** core/src/builder/script.rs (L512-538)
```rust
/// Struct for deposit script that replaces an old move tx with a replacement deposit (to update bridge design on chain)
/// It commits to the old move txid inside the script.
#[derive(Debug, Clone)]
pub struct ReplacementDepositScript(pub(crate) XOnlyPublicKey, Txid);

impl SpendableScript for ReplacementDepositScript {
    fn as_any(&self) -> &dyn Any {
        self
    }

    fn kind(&self) -> ScriptKind {
        ScriptKind::ReplacementDepositScript(self)
    }

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

**File:** core/src/builder/transaction/mod.rs (L404-446)
```rust
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
        // always use 0 sat anchor for replacement deposit tx, this will keep the amount in replacement deposit tx exactly the bridge amount
        .add_output(UnspentTxOut::from_partial(anchor_output(Amount::from_sat(
            0,
        ))))
        .finalize())
}
```

**File:** core/src/database/operator.rs (L359-430)
```rust
    pub async fn insert_deposit_data_if_not_exists(
        &self,
        mut tx: Option<DatabaseTransaction<'_>>,
        deposit_data: &mut DepositData,
        paramset: &'static ProtocolParamset,
    ) -> Result<u32, BridgeError> {
        // compute move to vault txid
        let move_to_vault_txid = create_move_to_vault_txhandler(deposit_data, paramset)?
            .get_cached_tx()
            .compute_txid();

        let query = sqlx::query_as::<_, (i32,)>(
            "INSERT INTO deposits (deposit_outpoint, deposit_params, move_to_vault_txid)
                VALUES ($1, $2, $3)
                ON CONFLICT (deposit_outpoint) DO NOTHING
                RETURNING deposit_id",
        )
        .bind(OutPointDB(deposit_data.get_deposit_outpoint()))
        .bind(DepositParamsDB(deposit_data.clone().into()))
        .bind(TxidDB(move_to_vault_txid));

        let result =
            execute_query_with_tx!(self.connection, tx.as_deref_mut(), query, fetch_optional)?;

        // If we got a deposit_id back, that means we successfully inserted new data
        if let Some((deposit_id,)) = result {
            return Ok(u32::try_from(deposit_id).wrap_err("Failed to convert deposit id to u32")?);
        }

        // If no rows were returned, data already exists - check if it matches
        let existing_query = sqlx::query_as::<_, (i32, DepositParamsDB, TxidDB)>(
            "SELECT deposit_id, deposit_params, move_to_vault_txid FROM deposits WHERE deposit_outpoint = $1"
        )
        .bind(OutPointDB(deposit_data.get_deposit_outpoint()));

        let (existing_deposit_id, existing_deposit_params, existing_move_txid): (
            i32,
            DepositParamsDB,
            TxidDB,
        ) = execute_query_with_tx!(self.connection, tx, existing_query, fetch_one)?;

        let existing_deposit_data: DepositData = existing_deposit_params
            .0
            .try_into()
            .map_err(|e| eyre::eyre!("Invalid deposit params {e}"))?;

        if existing_deposit_data != *deposit_data {
            tracing::error!(
                "Deposit data mismatch: Existing {:?}, New {:?}",
                existing_deposit_data,
                deposit_data
            );
            return Err(BridgeError::DepositDataMismatch(
                deposit_data.get_deposit_outpoint(),
            ));
        }

        if existing_move_txid.0 != move_to_vault_txid {
            // This should never happen, only a sanity check
            tracing::error!(
                "Move to vault txid mismatch in set_deposit_data: Existing {:?}, New {:?}",
                existing_move_txid.0,
                move_to_vault_txid
            );
            return Err(BridgeError::DepositDataMismatch(
                deposit_data.get_deposit_outpoint(),
            ));
        }

        // If data matches, return the existing deposit_id
        Ok(u32::try_from(existing_deposit_id).wrap_err("Failed to convert deposit id to u32")?)
    }
```
