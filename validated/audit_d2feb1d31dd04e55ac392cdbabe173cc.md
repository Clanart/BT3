### Title
`is_deposit_valid` accepts a `ReplacementDeposit` whose `old_move_txid` is never checked against a real, security-council-spent move-to-vault UTXO - (File: core/src/verifier.rs, core/src/builder/transaction/input.rs)

### Summary
`Verifier::is_deposit_valid` validates a `ReplacementDeposit` purely by re-deriving the expected `scriptPubkey` from the claimed `old_move_txid` and comparing it against the on-chain `deposit_outpoint` output — it never checks that `old_move_txid` names a move-to-vault transaction that actually existed and was actually spent via the `Multisig::from_security_council` / old-nofn path into that `deposit_outpoint`. An unprivileged depositor can therefore self-fund a fresh P2TR output built with `ReplacementDepositScript::new(nofn_xonly_pk, old_move_txid)` for any `old_move_txid` of their choosing, submit it through the aggregator's `NewDeposit` with `DepositType::ReplacementDeposit`, and have verifiers happily produce N-of-N partial signatures for its `move_to_vault` transaction.

### Finding Description
The invariant that should hold is: **for an accepted `ReplacementDeposit`, `old_move_txid` == the txid of a move-to-vault tx that previously existed and whose vault output was spent by a transaction using `CheckSig(old_nofn_xonly_pk)` or `Multisig::from_security_council(...)` into the new `deposit_outpoint`.**

Tracing the code:

- `ReplacementDepositScript` only *commits* to an arbitrary `old_move_txid` bytes inside an `OP_IF ... OP_ENDIF` branch; nothing about the script itself proves that any real prior vault existed or was spent: [1](#0-0) 
- `Deposit::get_deposit_scripts` for `DepositType::ReplacementDeposit` builds exactly `[ReplacementDepositScript::new(nofn_xonly_pk, replacement_deposit_data.old_move_txid), Multisig::from_security_council(...)]` directly from attacker-supplied `old_move_txid`: [2](#0-1) 
- `Verifier::is_deposit_valid` computes `expected_scriptpubkey` from these same scripts and only checks that the on-chain `deposit_outpoint`'s value equals `bridge_amount` and its `script_pubkey` matches this derived taproot address: [3](#0-2)  It never fetches, decodes, or checks the *input* of `deposit_outpoint`'s transaction, never queries whether `old_move_txid` was ever a real move-to-vault txid, and never checks that it was spent by the security-council/old-nofn script path.
- The RPC parser only validates that `old_move_txid` decodes to a syntactically valid `Txid`, with no semantic linkage check: [4](#0-3) 
- A grep across `core/src/verifier.rs` confirms `old_move_txid`/`ReplacementDeposit` is referenced only once (in `get_deposit_scripts`'s output check), with no separate lineage validation anywhere in the verifier.
- `deposit_sign`/`deposit_finalize` call `is_deposit_valid` and then proceed straight to producing N-of-N partial signatures for the `move_to_vault` tx handler built from this deposit: [5](#0-4) [6](#0-5) 

Attack flow: the attacker builds and broadcasts a normal Bitcoin transaction whose output N is a P2TR address for `[ReplacementDepositScript(nofn_xonly_pk, X), Multisig(security_council)]`, where `X` is any txid they choose (including the txid of someone else's still-active move-to-vault transaction that was never replaced). They fund this output with `bridge_amount` of their own BTC — they never touch the real vault named by `X` nor need any security-council/old-nofn signature. They then call the aggregator's `NewDeposit` with `DepositType::ReplacementDeposit{old_move_txid: X}` and the outpoint of their self-funded output. All verifiers pass `is_deposit_valid` because the script/amount check trivially matches, and they proceed to sign the `move_to_vault` transaction with N-of-N.

Existing guards do not close this: `Verifier::is_profitable`, `only_aggregator_and_self`, `SPV::verify`, and `lc_proof_verifier` operate on different transactions/paths (payout/kickoff/light-client) and are not invoked here; `is_deposit_valid`'s block-height and script/amount checks are the only checks applied to a `ReplacementDeposit`, and none of them validate provenance of `old_move_txid`.

### Impact Explanation
By itself, obtaining an N-of-N signature over this self-funded vault does not directly move someone else's bridged BTC, since the attacker funded the deposit output themselves with real BTC. The actual bridge-value-moving impact is downstream and contract-dependent: this Clementine-signed, chain-valid `ReplacementDeposit`/`move_to_vault` transaction is exactly the artifact needed to satisfy an SPV/script-pattern check on the Citrea bridge contract's `replaceDeposit(replace_tx, tx_proof, deposit_id, sha_script_pubkeys)` call, which (per the test helper `register_replacement_deposit_to_citrea`) updates the recorded `move_txid` for an arbitrary existing `deposit_id` to point at the new (attacker-controlled, self-funded, possibly tiny) vault: [7](#0-6) . If the Citrea contract accepts this update without independently verifying that `replace_tx` actually spent the *current* vault UTXO for that `deposit_id` (a check outside this repo's scope to confirm), an attacker could redirect an existing depositor's backing pointer to a vault holding far less value, effectively orphaning/freezing the real BTC and/or breaking future reimbursement for that `deposit_id`. Whether this fully materializes depends on Citrea-contract-side logic that is out of scope to verify here, but the root-cause gap — missing lineage verification in `Verifier::is_deposit_valid`/`from_scripts`-derived script check — is squarely in this repo and is the necessary precondition for any such downstream exploitation. This is repeatable across every existing deposit_id and every operator, since `old_move_txid` is fully attacker-chosen.

### Likelihood Explanation
The precondition is only "be able to broadcast a Bitcoin transaction with `bridge_amount` and call the public aggregator gRPC," which any unprivileged user can do; cost is one `bridge_amount` UTXO plus fees, no protocol role or key is required. The verifier-side signing step is fully and cheaply reachable today given the code shown; the full "theft" (redirecting an unrelated deposit_id's backing) additionally requires that the Citrea contract's `replaceDeposit` not perform its own input-spend verification, which could not be confirmed from this repository alone.

### Recommendation
In `Verifier::is_deposit_valid` (and/or in `from_scripts`/`get_deposit_scripts`), for `DepositType::ReplacementDeposit`, additionally verify that:
1. `old_move_txid` corresponds to a move-to-vault transaction that verifiers/aggregator actually know about (e.g., cross-check against the verifier's DB of previously finalized deposits/move txids), and
2. `deposit_outpoint`'s funding transaction's input actually spends the vault output of `old_move_txid` (i.e., fetch the raw tx of `deposit_outpoint.txid`, confirm one of its inputs is `OutPoint{old_move_txid, DepositInMove-vout}`), so that only a transaction produced via the legitimate `CheckSig(old_nofn)`/`Multisig(security_council)` spend path is accepted as a replacement.

### Proof of Concept
```rust
// cargo test (mocked Citrea, no mainnet) — core/src/builder/transaction/creator.rs or a new test module
#[cfg(feature = "automation")]
#[tokio::test(flavor = "multi_thread")]
async fn test_replacement_deposit_with_fabricated_old_move_txid_is_rejected() {
    let mut config = create_test_config_with_thread_name().await;
    let WithProcessCleanup(_, ref rpc, _, _) = create_regtest_rpc(&mut config).await;
    let actors = create_actors::<MockCitreaClient>(&config).await;

    // Attacker self-funds an output whose script commits to a RANDOM, never-existing old_move_txid
    let fabricated_old_move_txid = Txid::from_byte_array([0x42u8; 32]);
    let nofn_xonly_pk = actors.get_nofn_aggregated_xonly_pk().unwrap();

    // Build & broadcast a plain funding tx to the replacement-deposit taproot address
    // (generate_replacement_deposit_address(fabricated_old_move_txid, nofn_xonly_pk, network, security_council))
    let funded_outpoint = fund_replacement_style_output(
        &rpc, fabricated_old_move_txid, nofn_xonly_pk, &config
    ).await.unwrap();

    let deposit_info = DepositInfo {
        deposit_outpoint: funded_outpoint,
        deposit_type: DepositType::ReplacementDeposit(ReplacementDepositData {
            old_move_txid: fabricated_old_move_txid,
        }),
    };
    let deposit: Deposit = deposit_info.into();

    let mut aggregator = actors.get_aggregator();
    aggregator.setup(Request::new(Empty {})).await.unwrap();

    // BINDING under test:
    //   LHS: fabricated_old_move_txid
    //   RHS: txid of an actual move-to-vault tx spent via security-council/old-nofn path
    // Expect rejection because LHS != any real RHS.
    let result = aggregator.new_deposit(deposit).await;
    assert!(
        result.is_err(),
        "Expected rejection: ReplacementDeposit with an old_move_txid that names no real, \
         security-council-spent move-to-vault transaction must not be signed"
    );
}
```
Currently this test would fail (the aggregator/verifiers accept and sign), demonstrating that `is_deposit_valid` performs no lineage check on `old_move_txid`.

### Citations

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

**File:** core/src/verifier.rs (L866-898)
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

        let verifier = self.clone();
        let (partial_sig_tx, partial_sig_rx) = mpsc::channel(constants::DEFAULT_CHANNEL_SIZE);
        let verifier_index = deposit_data.get_verifier_index(&self.signer.public_key)?;
        let verifiers_public_keys = deposit_data.get_verifiers();
        let monitor_sender = partial_sig_tx.clone();

        let deposit_blockhash = self
            .rpc
            .get_blockhash_of_tx(&deposit_data.get_deposit_outpoint().txid)
            .await?;

```

**File:** core/src/verifier.rs (L982-994)
```rust
    pub async fn deposit_finalize(
        &self,
        deposit_data: &mut DepositData,
        session_id: u128,
        mut sig_receiver: mpsc::Receiver<Signature>,
        mut agg_nonce_receiver: mpsc::Receiver<AggregatedNonce>,
        mut operator_sig_receiver: mpsc::Receiver<Signature>,
    ) -> Result<(PartialSignature, PartialSignature), BridgeError> {
        self.citrea_client
            .check_nofn_correctness(deposit_data.get_nofn_xonly_pk()?)
            .await?;

        self.is_deposit_valid(deposit_data).await?;
```

**File:** core/src/rpc/parser/mod.rs (L363-374)
```rust
            clementine::deposit::DepositData::ReplacementDeposit(data) => {
                Ok(DepositType::ReplacementDeposit(ReplacementDepositData {
                    old_move_txid: data
                        .old_move_txid
                        .ok_or(Status::invalid_argument("No move_txid received"))?
                        .try_into().map_err(|e| {
                            Status::invalid_argument(format!(
                                "Failed to convert replacement deposit move_txid to bitcoin::Txid: {e}",
                            ))
                        })?,
                }))
            }
```

**File:** core/src/test/common/citrea/mod.rs (L495-567)
```rust
/// After a replacement deposit is done, register this replacement on citrea
/// The move_txid for the corresponding deposit_id will be updated to replacement_move_txid
pub async fn register_replacement_deposit_to_citrea(
    e2e: &CitreaE2EData<'_>,
    replacement_move_txid: Txid,
    deposit_id: u32,
    actors: &TestActors<CitreaClient>,
) -> eyre::Result<()> {
    wait_until_lc_contract_updated(
        e2e.sequencer.client.http_client(),
        e2e,
        actors,
        Some(replacement_move_txid),
    )
    .await?;

    tracing::info!("Setting operator to our address");
    // first set our address as operator
    let set_operator_tx = e2e
        .citrea_client
        .contract
        .setOperator(e2e.citrea_client.wallet_address)
        .send()
        .await?;
    force_sequencer_to_commit(e2e.sequencer).await?;
    let receipt = set_operator_tx.get_receipt().await?;
    tracing::info!("Set operator tx receipt: {:?}", receipt);

    e2e.rpc
        .mine_blocks_while_synced(DEFAULT_FINALITY_DEPTH, actors, Some(e2e))
        .await
        .unwrap();

    let (replace_tx, block, block_height) =
        get_tx_information_for_citrea(e2e, replacement_move_txid).await?;

    tracing::info!("Replace transaction: {:?}", replace_tx);
    tracing::info!("Replace transaction block: {:?}", block);

    // wait for light client to sync until replacement deposit tx
    e2e.lc_prover
        .wait_for_l1_height(block_height, None)
        .await
        .map_err(|e| eyre::eyre!("Failed to wait for light client to sync: {:?}", e))?;

    let (replace_tx, tx_proof, sha_script_pubkeys) = get_citrea_deposit_params(
        e2e.rpc,
        replace_tx,
        block,
        block_height as u32,
        replacement_move_txid,
    )
    .await?;

    tracing::info!("Replace transaction block height: {:?}", block_height);
    tracing::info!(
        "Current chain height: {:?}",
        e2e.rpc.get_current_chain_height().await.unwrap()
    );
    tracing::info!("Replace transaction tx proof : {:?}", tx_proof);

    let replace_deposit_tx = e2e
        .citrea_client
        .contract
        .replaceDeposit(
            replace_tx,
            tx_proof,
            U256::from(deposit_id),
            sha_script_pubkeys,
        )
        .from(e2e.citrea_client.wallet_address)
        .send()
        .await?;
```
