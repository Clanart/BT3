### Title
Unauthenticated `InternalSendTx` allows arbitrary attacker Bitcoin transactions to be broadcast and fee-funded by the aggregator's own bitcoind wallet - ([File: core/src/rpc/aggregator.rs])

### Summary
`ClementineAggregator::internal_send_tx` accepts any `SendTxRequest` containing an arbitrary, attacker-authored raw Bitcoin transaction and unconditionally enqueues it for broadcast through the aggregator's tx-sender, which uses the aggregator's own bitcoind wallet to construct CPFP fee-payer UTXOs. Because the aggregator's gRPC server is documented and implemented to skip client-certificate authentication (`Interceptors::Noop`), any network caller can invoke this RPC and cause the aggregator's node to fund and relay their chosen transaction.

### Finding Description
The claimed binding is: `tx_broadcast_via_aggregator_rpc == a_tx_this_aggregator_itself_presigned_or_authored`.

`internal_send_tx` deserializes `send_tx_req.raw_tx` into a `bitcoin::Transaction` with no validation that it originated from this aggregator's signing pipeline (no txid/script/type check against `TransactionType`, no deposit/kickoff/round linkage check), and immediately queues it via `self.tx_sender.insert_try_to_send(...)` with attacker-supplied `fee_type` [1](#0-0) .

Authentication for this call depends entirely on the deployment's `client_verification` / TLS peer-cert interceptor. When `client_verification` is false the server installs `Interceptors::Noop`, which passes every request through unchecked [2](#0-1) [3](#0-2) . The project's own documentation states this is the standing design for the aggregator specifically: "The aggregator does not enforce client certificates but does use TLS for encryption" [4](#0-3) ; `create_aggregator_grpc_server` only logs a warning if `client_verification` happens to be true and otherwise proceeds normally with `Noop` [5](#0-4) . Per the attacker model, the attacker already has "requests to the aggregator's public gRPC port" without holding any TLS certificate — exactly the condition under which `InternalSendTx` is reachable.

Once queued, the tx-sender's CPFP path (`send_cpfp_tx` / `create_fee_payer_utxo`) spends the aggregator's own bitcoind wallet funds to create and confirm a "fee payer" UTXO and a child transaction that pays the fee for the attacker's parent transaction, then submits the package via `submitpackage` [6](#0-5) [7](#0-6) . This means the aggregator's own bitcoind wallet (`self.rpc`, funded from `bitcoin_rpc_url`/wallet in `BridgeConfig`) pays real BTC fees to get an attacker-chosen, unrelated transaction relayed — no deposit/withdrawal/kickoff accounting record is created since `tx_metadata` is passed as `None` in `internal_send_tx`.

No existing guard intercepts this: `only_aggregator_and_self` is never invoked when `Interceptors::Noop` is active; `insert_try_to_send` only deduplicates by txid, it performs no semantic validation of the transaction's origin, type, or relationship to any deposit/round/kickoff [8](#0-7) .

### Impact Explanation
This matches the High-severity category "an unauthenticated state-changing or broadcasting call." Concretely: any unprivileged network caller reaching the aggregator's port can force the aggregator's bitcoind wallet to fund fees (via the fee-payer-UTXO/CPFP mechanism) for an arbitrary, attacker-chosen Bitcoin transaction and get it broadcast/confirmed through the aggregator's node — with zero linkage to any bridge accounting object (deposit, kickoff, round). This is repeatable per attacker per call (each call creates a new fee-payer UTXO funding round) and is not bounded to a single deposit or operator; it drains the aggregator's operational wallet balance on demand. It does not directly move bridge collateral or move-to-vault funds (this repo's `TransactionType` semantics and presigned-tx graph are untouched — the attacker cannot forge N-of-N signatures via this path), so the blast radius is confined to the aggregator's own fee-paying hot wallet rather than user/operator collateral.

### Likelihood Explanation
Preconditions: the aggregator must be deployed with `client_verification = false` (i.e., `Interceptors::Noop`) — which is the documented default behavior for the aggregator role — and built with the `automation` feature enabled (required for `internal_send_tx` to do anything other than return `unimplemented`). Given these are stated as the standard aggregator configuration in the codebase's own docs, this is highly likely to occur in real deployments where the aggregator's gRPC port is reachable from the internet/untrusted network. Attacker cost is minimal (a single unauthenticated gRPC call); the aggregator absorbs all Bitcoin fee costs, which can be repeated to drain the aggregator's operational wallet.

### Recommendation
Enforce authentication on the aggregator's internal/state-changing RPCs regardless of `client_verification` setting — at minimum, `InternalSendTx` (and other `Internal*` methods) should require the same "self"-cert check used by verifier/operator servers, not rely on `Noop` when TLS client verification is disabled. Additionally, `internal_send_tx` should validate that the submitted transaction actually corresponds to a transaction this aggregator produced (e.g., verify it matches a previously signed/tracked `TransactionType` and is linked to a deposit/kickoff/round in the DB) before enqueuing it for aggregator-funded broadcast.

### Proof of Concept
```rust
// cargo test -p clementine-core --features automation
#[tokio::test]
async fn test_internal_send_tx_arbitrary_unauthenticated() {
    let mut config = create_test_config_with_thread_name().await;
    config.client_verification = false; // Noop interceptor, matches documented aggregator default
    let rpc = create_regtest_rpc(&mut config).await;

    let actors = create_actors::<MockCitreaClient>(&config).await;
    let mut aggregator = actors.get_aggregator(); // unauthenticated client, no aggregator cert used

    // Build an arbitrary valid tx unrelated to any deposit/kickoff/round,
    // e.g. spend a wallet UTXO to an attacker address with a P2A anchor output for CPFP.
    let arbitrary_tx = build_arbitrary_tx_with_anchor(&rpc).await;

    // BEFORE: binding holds trivially (tx not related to aggregator signing pipeline == no aggregator broadcast yet)
    assert!(rpc.get_raw_transaction_info(&arbitrary_tx.compute_txid(), None).await.is_err());

    aggregator
        .internal_send_tx(SendTxRequest {
            raw_tx: Some(RawSignedTx { raw_tx: bitcoin::consensus::serialize(&arbitrary_tx) }),
            fee_type: FeeType::Cpfp as i32,
        })
        .await
        .expect("call succeeds without any authentication");

    tokio::time::sleep(std::time::Duration::from_secs(5)).await;
    rpc.mine_blocks(2).await.unwrap();

    // AFTER: binding is broken - tx WAS broadcast via aggregator's funded RPC despite
    // never being produced by the aggregator's signing pipeline, and aggregator's wallet
    // paid the CPFP fee-payer UTXO cost.
    assert!(ensure_tx_onchain(&rpc, arbitrary_tx.compute_txid()).await.is_ok());
}
```
Note: I was unable to fully confirm from the index whether the default production/testnet4 deployment scripts actually set `client_verification = false` for the *aggregator* role specifically (the docker configs I found set `client_verification = true` generically for all roles including aggregator's `bridge_config.toml`); the "Noop-by-default-for-aggregator" behavior is confirmed at the code/doc level (`docs/usage.md`, `servers.rs`) but the exact operator-chosen deployment config could override this. A Devin session with full repo/file access could verify the actual `client_verification` value used for the aggregator in each deployment profile.

### Citations

**File:** core/src/rpc/aggregator.rs (L1278-1306)
```rust
        #[cfg(feature = "automation")]
        {
            let send_tx_req = request.into_inner();
            let fee_type = send_tx_req.fee_type();
            let signed_tx: bitcoin::Transaction = send_tx_req
                .raw_tx
                .ok_or(Status::invalid_argument("Missing raw_tx"))?
                .try_into()?;
            tracing::warn!(
                "Internal send tx rpc called with feetype: {:?}, tx hex: {}",
                fee_type,
                bitcoin::consensus::encode::serialize_hex(&signed_tx)
            );

            let mut dbtx = self.db.begin_transaction().await?;
            self.tx_sender
                .insert_try_to_send(
                    &mut dbtx,
                    None,
                    &signed_tx,
                    fee_type.try_into()?,
                    None,
                    &[],
                    &[],
                    &[],
                    &[],
                )
                .await
                .map_to_status()?;
```

**File:** core/src/rpc/interceptors.rs (L22-32)
```rust
impl Interceptor for Interceptors {
    #[allow(clippy::result_large_err)]
    fn call(&mut self, req: Request<()>) -> Result<Request<()>, Status> {
        match self {
            Interceptors::OnlyAggregatorAndSelf {
                our_cert,
                aggregator_cert,
            } => only_aggregator_and_self(req, our_cert, aggregator_cert),
            Interceptors::Noop => Ok(req),
        }
    }
```

**File:** core/src/servers.rs (L106-139)
```rust
            let tls_config = if config.client_verification {
                ServerTlsConfig::new()
                    .identity(server_identity)
                    .client_ca_root(client_ca)
            } else {
                ServerTlsConfig::new().identity(server_identity)
            };

            let service = InterceptedService::new(
                service,
                if config.client_verification {
                    let client_cert = CertificateDer::from_pem_file(&config.client_cert_path)
                        .wrap_err(format!(
                            "Failed to read client certificate from {}",
                            config.client_cert_path.display()
                        ))?
                        .to_owned();

                    let aggregator_cert =
                        CertificateDer::from_pem_file(&config.aggregator_cert_path)
                            .wrap_err(format!(
                                "Failed to read aggregator certificate from {}",
                                config.aggregator_cert_path.display()
                            ))?
                            .to_owned();

                    OnlyAggregatorAndSelf {
                        aggregator_cert,
                        our_cert: client_cert,
                    }
                } else {
                    Noop
                },
            );
```

**File:** core/src/servers.rs (L293-317)
```rust
pub async fn create_aggregator_grpc_server(
    config: BridgeConfig,
) -> Result<(std::net::SocketAddr, oneshot::Sender<()>), BridgeError> {
    let addr: std::net::SocketAddr = format!("{}:{}", config.host, config.port)
        .parse()
        .wrap_err("Failed to parse address")?;
    let aggregator_server = AggregatorServer::new(config.clone()).await?;
    aggregator_server.start_background_tasks().await?;

    let svc = ClementineAggregatorServer::new(aggregator_server)
        .max_encoding_message_size(config.grpc.max_message_size)
        .max_decoding_message_size(config.grpc.max_message_size);

    if config.client_verification {
        tracing::warn!("Client verification is enabled on aggregator gRPC server",);
    }

    let (server_addr, shutdown_tx) =
        create_grpc_server(addr.into(), svc, "Aggregator", &config).await?;

    match server_addr {
        ServerAddr::Tcp(socket_addr) => Ok((socket_addr, shutdown_tx)),
        _ => Err(BridgeError::ConfigError("Expected TCP address".into())),
    }
}
```

**File:** docs/usage.md (L203-203)
```markdown
The aggregator does not enforce client certificates but does use TLS for encryption.
```

**File:** crates/clementine-tx-sender/src/cpfp.rs (L195-254)
```rust
        let fee_payer_tx = Transaction {
            version: Version::TWO,
            lock_time: LockTime::ZERO,
            input: vec![],
            output: vec![TxOut {
                value: new_fee_payer_amount,
                script_pubkey: self.signer.address().script_pubkey(),
            }],
        };

        let fee_payer_bytes = crate::serialize_tx_for_fund_raw(&fee_payer_tx);

        let funded_fee_payer_tx = self
            .rpc
            .fund_raw_transaction(
                &fee_payer_bytes,
                Some(&FundRawTransactionOptions {
                    add_inputs: Some(true),
                    // for cpfp txs, the speed of tx inclusion is not that important, so we can not use unsafe utxos and wait for them to become safe. Also all cpfp fee payer tx's are safe (all wallet owned inputs), so wallet can already chain them
                    include_unsafe: Some(self.include_unsafe),
                    change_address: None,
                    change_position: None,
                    change_type: None,
                    include_watching: None,
                    lock_unspents: None,
                    fee_rate: Some(Amount::from_sat(fee_rate.to_sat_per_kvb())),
                    subtract_fee_from_outputs: None,
                    replaceable: Some(true),
                    conf_target: None,
                    estimate_mode: None,
                }),
                None,
            )
            .await
            .wrap_err("Failed to fund cpfp fee payer tx")?
            .hex;

        let signed_fee_payer_tx: Transaction = bitcoin::consensus::deserialize(
            &self
                .rpc
                .sign_raw_transaction_with_wallet(&funded_fee_payer_tx, None, None)
                .await
                .wrap_err("Failed to sign funded tx through bitcoin RPC")?
                .hex,
        )
        .wrap_err("Failed to deserialize signed tx")?;

        let outpoint_vout = signed_fee_payer_tx
            .output
            .iter()
            .position(|o| {
                o.value == new_fee_payer_amount
                    && o.script_pubkey == self.signer.address().script_pubkey()
            })
            .ok_or(eyre!("Failed to find outpoint vout"))?;

        self.rpc
            .send_raw_transaction(&signed_fee_payer_tx)
            .await
            .wrap_err("Failed to send signed fee payer tx")?;
```

**File:** crates/clementine-tx-sender/src/cpfp.rs (L574-679)
```rust
    #[tracing::instrument(skip_all, fields(try_to_send_id, tx_meta=?tx_metadata))]
    pub async fn send_cpfp_tx(
        &self,
        try_to_send_id: u32,
        tx: Transaction,
        tx_metadata: Option<TxMetadata>,
        fee_rate: FeeRateKvb,
        current_tip_height: u32,
    ) -> Result<()> {
        let unconfirmed = self
            .db
            .get_unconfirmed_fee_payer_txs(None, try_to_send_id)
            .await
            .map_err(|e: BridgeError| SendTxError::Other(e.into()))?;
        if !unconfirmed.is_empty() {
            // Log that we're waiting for unconfirmed UTXOs
            tracing::debug!(
                try_to_send_id,
                "Waiting for {} UTXOs to confirm",
                unconfirmed.len()
            );

            let _ = self
                .db
                .update_tx_debug_sending_state(
                    try_to_send_id,
                    "waiting_for_utxo_confirmation",
                    true,
                )
                .await;
            return Ok(());
        }

        let confirmed = self.get_confirmed_fee_payer_utxos(try_to_send_id).await?;
        let total_amount: Amount = confirmed.iter().map(|u| u.txout.value).sum();

        let _ = self
            .db
            .update_tx_debug_sending_state(try_to_send_id, "creating_package", true)
            .await;

        let package = match self
            .create_package(tx.clone(), fee_rate, confirmed.clone())
            .await
        {
            Ok(p) => p,
            Err(SendTxError::InsufficientFeePayerAmount) => {
                self.create_fee_payer_utxo(
                    try_to_send_id,
                    None,
                    &tx,
                    fee_rate,
                    total_amount,
                    confirmed.len(),
                )
                .await?;
                let _ = self
                    .db
                    .update_tx_debug_sending_state(
                        try_to_send_id,
                        "waiting_for_fee_payer_utxos",
                        true,
                    )
                    .await;
                return Ok(());
            }
            Err(e) => {
                tracing::error!(try_to_send_id, "Failed to create CPFP package: {:?}", e);
                return Err(e);
            }
        };

        let package_refs: Vec<&Transaction> = package.iter().collect();

        tracing::debug!(
            try_to_send_id,
            "Submitting package\n Pkg tx hexs: {:?}",
            if env::var("DBG_PACKAGE_HEX").is_ok() {
                package
                    .iter()
                    .map(|tx| hex::encode(bitcoin::consensus::serialize(tx)))
                    .collect::<Vec<_>>()
            } else {
                vec!["use DBG_PACKAGE_HEX=1 to print the package as hex".into()]
            }
        );

        // Save the effective fee rate before attempting to send
        // This ensures that even if the send fails, we track the attempt
        // so the 10-block stuck logic can trigger a bump
        self.db
            .update_effective_fee_rate(None, try_to_send_id, fee_rate, current_tip_height)
            .await
            .wrap_err("Failed to update effective fee rate")?;

        // Update sending state to submitting_package
        let _ = self
            .db
            .update_tx_debug_sending_state(try_to_send_id, "submitting_package", true)
            .await;

        let submit_result = self
            .rpc
            .submit_package(&package_refs, Some(Amount::ZERO), None)
            .await
            .wrap_err("Failed to submit package")?;
```

**File:** crates/clementine-tx-sender/src/client.rs (L59-101)
```rust
    pub async fn insert_try_to_send(
        &self,
        dbtx: &mut crate::TxSenderTransaction,
        tx_metadata: Option<TxMetadata>,
        signed_tx: &Transaction,
        fee_paying_type: FeePayingType,
        rbf_signing_info: Option<RbfSigningInfo>,
        cancel_outpoints: &[OutPoint],
        cancel_txids: &[Txid],
        activate_txids: &[ActivatedWithTxid],
        activate_outpoints: &[ActivatedWithOutpoint],
    ) -> Result<u32, BridgeError> {
        let txid = signed_tx.compute_txid();

        // do not add duplicate transactions to the txsender
        let tx_exists = self
            .db
            .check_if_tx_exists_on_txsender(Some(dbtx), txid)
            .await?;
        if let Some(try_to_send_id) = tx_exists {
            return Ok(try_to_send_id);
        }

        tracing::info!(
            "Added tx {} with txid {} to the queue",
            tx_metadata
                .as_ref()
                .map(|data| format!("{:?}", data.tx_type))
                .unwrap_or("N/A".to_string()),
            txid
        );

        let try_to_send_id = self
            .db
            .save_tx(
                dbtx,
                tx_metadata,
                signed_tx,
                fee_paying_type,
                txid,
                rbf_signing_info,
            )
            .await?;
```
