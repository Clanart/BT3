### Title
Unauthenticated JSON-RPC interface on the standalone tx-sender lets any network caller queue Bitcoin transactions and Citrea DA payloads for broadcast - (File: `crates/clementine-tx-sender/src/jsonrpc/server.rs`)

### Summary
`start_jsonrpc_server` in `crates/clementine-tx-sender/src/jsonrpc/server.rs` exposes `send_tx` and `send_citrea_tx` as plain `jsonrpsee` JSON-RPC methods over HTTP with no authentication, no TLS, and no request-origin check of any kind [1](#0-0) . This is the same bug class as the reported unauthenticated JSON-RPC service: `bind` is explicitly allowed to be `0.0.0.0` (a supported, documented value, not a misconfiguration) [2](#0-1) , so this endpoint can be network-reachable to an unprivileged caller with no verifier/operator/aggregator role, key, or certificate.

### Finding Description
`send_tx` deserializes an attacker-supplied `signed_tx_hex` and calls `TxSenderClient::insert_try_to_send`, which persists the transaction into the send queue with no check on who submitted it or what its purpose is [3](#0-2) [4](#0-3) . The background loop then actively fee-bumps and broadcasts any queued transaction: for `FeePayingType::CPFP` it will, if needed, mint a brand-new "fee payer" UTXO funded from the tx-sender's own Bitcoin wallet via `fund_raw_transaction`/`send_to_address`, sign it, and broadcast it, then submit the CPFP package with `submitpackage` [5](#0-4) [6](#0-5) . Nothing in this path validates that the queued transaction actually corresponds to a legitimate bridge transaction type (kickoff/round/reimburse/etc.) issued by the entity's own signer — the JSON-RPC handler accepts an arbitrary caller-supplied `Transaction` and `fee_paying_type`.

This directly parallels the reported vulnerability class: an unauthenticated JSON-RPC interface exposing state-changing/broadcasting methods that a remote, unprivileged party can invoke. Contrast this with every gRPC surface in this repo (`core/src/servers.rs`, `core/src/rpc/interceptors.rs`), which enforces mutual TLS and an `Interceptors::OnlyAggregatorAndSelf` check restricting internal/state-changing methods to the aggregator or the entity itself [7](#0-6) . The standalone tx-sender's JSON-RPC surface has no equivalent binding — no certificate check, no caller identity, no host validation.

### Impact Explanation
This is an unauthenticated, state-changing/broadcasting call reachable without any bridge role, matching the High-impact category ("an unauthenticated state-changing or broadcasting call"). Concretely, an attacker can:
- Repeatedly submit arbitrary signed transactions for CPFP fee-bumping, causing the tx-sender's wallet to mint and broadcast new fee-payer UTXOs funded from its own balance for transactions it does not control the purpose of, burning the operating entity's own BTC collateral on fees for attacker-chosen transactions.
- Submit `send_citrea_tx` payloads that get queued into the Citrea DA-blob-sending pipeline, again with no verification of caller identity.

Because `insert_try_to_send` only dedupes by txid and otherwise persists whatever is submitted [8](#0-7) , there's no reliance on the attacker's transaction actually being valid bridge protocol data — merely being a syntactically valid Bitcoin transaction is enough to enter the fee-bumping pipeline and trigger wallet-funded broadcast activity.

### Likelihood Explanation
Likelihood depends on deployment: the JSON-RPC feature is opt-in (`json-rpc` cargo feature) and only activated when `TX_SENDER_JSONRPC_PORT` is set [2](#0-1) , but the configuration schema explicitly supports binding to `0.0.0.0` as a first-class option rather than treating it as an unsupported/insecure choice, and there is no authentication layer available at all even for that supported configuration [1](#0-0) . Given the standalone tx-sender is designed to run as an independently deployed service (with its own Postgres schema, likely shared or remotely called), operators enabling this JSON-RPC interface per its documented options have no way to restrict it to legitimate callers.

### Recommendation
Add authentication to the JSON-RPC server (e.g., an API key/bearer token checked via a `jsonrpsee` middleware, or mTLS similar to the gRPC servers' `Interceptors::OnlyAggregatorAndSelf`), and/or restrict `send_tx`/`send_citrea_tx` to require proof that the caller is the entity's own operator/aggregator process (e.g., a shared secret configured out-of-band). At minimum, remove `0.0.0.0` as a supported bind option unless paired with mandatory authentication.

### Proof of Concept
1. Deploy `clementine-tx-sender` with the `json-rpc` feature enabled and `TX_SENDER_JSONRPC_BIND=0.0.0.0`, `TX_SENDER_JSONRPC_PORT=3030` (both are accepted, documented configuration values per `crates/clementine-tx-sender/src/config.rs:197-212`).
2. From any network location able to reach the port, send:
```
curl -H 'content-type: application/json' \
  --data '{"jsonrpc":"2.0","id":1,"method":"send_tx","params":[{"signed_tx_hex":"<attacker tx hex>","fee_paying_type":"CPFP", ...}]}' \
  http://<target>:3030
```
3. The request succeeds without any credential, inserting the attacker's transaction into the queue (`crates/clementine-tx-sender/src/jsonrpc/server.rs:58-88`, `crates/clementine-tx-sender/src/client.rs:59-101`).
4. The background loop's `send_cpfp_tx`/`create_fee_payer_utxo` path funds and broadcasts a fee-payer UTXO from the tx-sender's own wallet to bump the attacker's transaction (`crates/clementine-tx-sender/src/cpfp.rs:195-254,574-644`), consuming the operator's BTC for a transaction it never fronted.

### Citations

**File:** crates/clementine-tx-sender/src/jsonrpc/server.rs (L42-56)
```rust
pub async fn start_jsonrpc_server(
    tx_sender_client: TxSenderClient,
    bind_addr: SocketAddr,
) -> Result<TxSenderJsonRpcServer, BridgeError> {
    let server: Server = ServerBuilder::default()
        .max_request_body_size(MAX_JSONRPC_REQUEST_BODY_SIZE)
        .build(bind_addr)
        .await
        .map_err(|e| BridgeError::Eyre(e.into()))?;

    let local_addr = server
        .local_addr()
        .map_err(|e| BridgeError::Eyre(e.into()))?;

    let mut module = RpcModule::new(tx_sender_client.clone());
```

**File:** crates/clementine-tx-sender/src/jsonrpc/server.rs (L58-88)
```rust
        .register_async_method("send_tx", |params, client, _| async move {
            let req: InsertTryToSendParams = params.one().map_err(jsonrpc_err)?;

            let raw_tx = hex::decode(&req.signed_tx_hex).map_err(jsonrpc_err)?;
            let signed_tx: Transaction = consensus::deserialize(&raw_tx).map_err(jsonrpc_err)?;

            let mut dbtx = client.db.begin_transaction().await.map_err(jsonrpc_err)?;

            let try_to_send_id = client
                .insert_try_to_send(
                    &mut dbtx,
                    req.tx_metadata,
                    &signed_tx,
                    req.fee_paying_type,
                    req.rbf_signing_info,
                    &req.cancel_outpoints,
                    &req.cancel_txids,
                    &req.activate_txids,
                    &req.activate_outpoints,
                )
                .await
                .map_err(jsonrpc_err)?;

            client
                .db
                .commit_transaction(dbtx)
                .await
                .map_err(jsonrpc_err)?;

            Ok::<u32, ErrorObjectOwned>(try_to_send_id)
        })
```

**File:** crates/clementine-tx-sender/src/config.rs (L197-212)
```rust
        #[cfg(feature = "json-rpc")]
        let jsonrpc = {
            let port = env_parse_optional::<u16>("TX_SENDER_JSONRPC_PORT")?;
            port.map(|port| {
                let bind = env_optional("TX_SENDER_JSONRPC_BIND")
                    .unwrap_or_else(|| "127.0.0.1".to_string());
                if bind != "127.0.0.1" && bind != "0.0.0.0" {
                    return Err(BridgeError::EnvVarMalformed(
                        "TX_SENDER_JSONRPC_BIND",
                        "bind must be either 127.0.0.1 or 0.0.0.0".to_string(),
                    ));
                }
                Ok(TxSenderJsonRpcConfig { bind, port })
            })
            .transpose()?
        };
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

**File:** crates/clementine-tx-sender/src/cpfp.rs (L574-644)
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
```

**File:** core/src/rpc/interceptors.rs (L22-76)
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
}

#[allow(clippy::result_large_err)]
fn only_aggregator_and_self(
    req: Request<()>,
    our_cert: &CertificateDer<'static>,
    aggregator_cert: &CertificateDer<'static>,
) -> Result<Request<()>, Status> {
    let Some(peer_certs) = req.peer_certs() else {
        if cfg!(test) {
            // Test mode, we don't need to verify peer certificates
            return Ok(req);
        } else {
            // If we're not in test mode, we need to check peer certificates
            return Err(Status::unauthenticated(
                "Failed to verify peer certificate, is TLS enabled?",
            ));
        }
    };

    // IMPORTANT: Only check the leaf (end-entity) certificate, which is always the first
    // certificate in the chain. The leaf is the only certificate whose private key the peer
    // proved possession of during the TLS handshake. Checking anywhere else in the chain
    // would allow identity spoofing: an attacker could include a pinned cert as an
    // intermediate in their chain without possessing its private key.
    let Some(leaf_cert) = peer_certs.first() else {
        return Err(Status::unauthenticated("Peer certificate chain is empty"));
    };

    if is_internal(&req) {
        if leaf_cert == our_cert {
            Ok(req)
        } else {
            Err(Status::unauthenticated(
                "Unauthorized call to internal method (not self)",
            ))
        }
    } else if leaf_cert == aggregator_cert || leaf_cert == our_cert {
        Ok(req)
    } else {
        Err(Status::unauthenticated(
            "Unauthorized call to method (not aggregator or self)",
        ))
    }
```
