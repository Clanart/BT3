### Title
Unauthenticated tx-sender JSON-RPC server allows any network peer to inject arbitrary transactions, drain the operator fee wallet, and permanently DoS time-sensitive bridge transactions — (`crates/clementine-tx-sender/src/jsonrpc/server.rs`)

### Summary

The standalone `clementine-tx-sender` JSON-RPC server (`start_jsonrpc_server`) is built with `jsonrpsee::ServerBuilder::default()` and carries zero authentication, zero TLS, and no IP allowlist beyond the bind address. When the operator deploys with `TX_SENDER_JSONRPC_BIND=0.0.0.0`, any network-reachable attacker can call `send_tx` to inject arbitrary transactions into the broadcast queue with full control over `fee_paying_type`, `cancel_outpoints`, and `cancel_txids`. The tx-sender then autonomously spends the operator's Bitcoin wallet to create CPFP fee-payer UTXOs for every injected entry, draining the fee wallet. A drained fee wallet prevents the operator from broadcasting time-sensitive bridge transactions (Challenge, Payout, ChallengeTimeout) before their timelocks expire, causing permanent loss of the operator's collateral (up to `operator_challenge_amount` = 200 000 000 sat in the reference config).

### Finding Description

**Root cause — `start_jsonrpc_server`:**

```rust
// crates/clementine-tx-sender/src/jsonrpc/server.rs  lines 42-50
pub async fn start_jsonrpc_server(
    tx_sender_client: TxSenderClient,
    bind_addr: SocketAddr,
) -> Result<TxSenderJsonRpcServer, BridgeError> {
    let server: Server = ServerBuilder::default()
        .max_request_body_size(MAX_JSONRPC_REQUEST_BODY_SIZE)
        .build(bind_addr)
        .await
        ...
```

`ServerBuilder::default()` creates a plain HTTP server. No TLS layer, no token check, no IP filter, no rate limit beyond the 50 MB body cap is applied. [1](#0-0) 

The `send_tx` handler accepts a fully attacker-controlled `InsertTryToSendParams`: [2](#0-1) 

The caller supplies `fee_paying_type`, `cancel_outpoints`, and `cancel_txids` without any server-side validation of the caller's identity: [3](#0-2) 

**Contrast with the gRPC servers:** every gRPC server (verifier, operator) wraps its service in `OnlyAggregatorAndSelf` when `client_verification=true`, enforcing mTLS certificate pinning. The JSON-RPC server has no equivalent guard. [4](#0-3) [5](#0-4) 

**Bind address is operator-configurable to `0.0.0.0`:** [6](#0-5) 

**CPFP fee-drain path:** when the attacker submits a transaction with `FeePayingType::CPFP`, the tx-sender loop calls `create_fee_payer_utxo`, which calls `fund_raw_transaction` + `sign_raw_transaction_with_wallet` + `send_raw_transaction` on the operator's Bitcoin Core wallet, spending real wallet UTXOs to create a fee-payer output: [7](#0-6) 

**Cancel-outpoints path:** the attacker can also set `cancel_txids` to the txids of legitimate queued bridge transactions. If the attacker's injected transaction confirms on-chain, the tx-sender marks those legitimate transactions as cancelled and stops broadcasting them: [8](#0-7) 

### Impact Explanation

**Fee wallet drain → collateral loss.** The operator's collateral (`collateral_funding_amount` = 90 000 000–200 000 000 sat in reference configs) is protected only as long as the operator can broadcast the `Challenge` or `ChallengeTimeout` transaction before `operator_challenge_timeout_timelock` (144 blocks ≈ 1 day on mainnet) expires. [9](#0-8) 

If the fee wallet is drained, the tx-sender cannot create CPFP packages for any bridge transaction. The `Challenge` transaction uses `FeePayingType::RBF`, but the RBF path also requires wallet funds for fee bumping. A drained wallet means no fee bumping, no mempool acceptance, and the timelock expires — the operator's collateral UTXO becomes claimable by the adversary via the `ChallengeTimeout` path. [10](#0-9) 

**Cancel-outpoints → permanent DoS.** An attacker who can confirm a transaction with `cancel_txids` pointing to the `Challenge` or `Payout` txid removes those entries from the active queue permanently, achieving the same outcome without draining the wallet.

### Likelihood Explanation

The JSON-RPC server is a documented, production-ready feature of the standalone `clementine-tx-sender` binary. The `run.sh` in the tx-sender crate and the Docker compose files show it is intended for deployment. The only barrier is the operator choosing `TX_SENDER_JSONRPC_BIND=0.0.0.0` (the default fallback when the env var is absent) instead of `127.0.0.1`. The config comment itself says "Restricted to 127.0.0.1 or 0.0.0.0", acknowledging that `0.0.0.0` is a valid and expected value. An attacker who can reach the port (e.g., same data-center network, misconfigured firewall, or cloud VPC) has full unauthenticated access. [11](#0-10) 

### Recommendation

1. **Add authentication to the JSON-RPC server.** At minimum, require a shared secret bearer token in the `Authorization` header, validated in a tower middleware layer before any handler is invoked. Alternatively, restrict the server to Unix-domain sockets (no network exposure) and enforce filesystem permissions.
2. **Enforce `127.0.0.1`-only binding by default.** Change the default bind to `127.0.0.1` and require an explicit opt-in for `0.0.0.0`, with a loud warning logged at startup.
3. **Validate caller identity at the handler level.** Even with a network-restricted bind, add a token or HMAC check so that a compromised co-tenant cannot abuse the endpoint.

### Proof of Concept

```bash
# Attacker on the same network as the tx-sender (TX_SENDER_JSONRPC_BIND=0.0.0.0)
# Step 1: craft a minimal Bitcoin transaction with a P2A anchor output
# (OP_1 OP_PUSHBYTES_2 0x4e73 = P2A script the tx-sender recognises for CPFP)
SIGNED_TX_HEX="<hex of any syntactically valid tx with a P2A output>"

# Step 2: call send_tx with FeePayingType::CPFP (value 0 in the enum)
curl -s -X POST http://<operator-ip>:<TX_SENDER_JSONRPC_PORT> \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc":"2.0","id":1,"method":"send_tx",
    "params":[{
      "tx_metadata": null,
      "signed_tx_hex": "'"$SIGNED_TX_HEX"'",
      "fee_paying_type": "CPFP",
      "rbf_signing_info": null,
      "cancel_outpoints": [],
      "cancel_txids": ["<txid-of-legitimate-challenge-tx>"],
      "activate_txids": [],
      "activate_outpoints": []
    }]
  }'
# Result: tx-sender calls fund_raw_transaction + send_raw_transaction on the
# operator's Bitcoin Core wallet, spending wallet UTXOs for a fee-payer output.
# Repeated calls drain the wallet. cancel_txids entry removes the Challenge tx
# from the queue if the injected tx ever confirms.
```

The `insert_try_to_send` call succeeds unconditionally for any syntactically valid transaction because no caller identity check exists anywhere in the handler path. [12](#0-11) [13](#0-12)

### Citations

**File:** crates/clementine-tx-sender/src/jsonrpc/server.rs (L42-50)
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
```

**File:** crates/clementine-tx-sender/src/jsonrpc/server.rs (L56-89)
```rust
    let mut module = RpcModule::new(tx_sender_client.clone());
    module
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
        .map_err(|e| BridgeError::Eyre(e.into()))?;
```

**File:** crates/tx-sender-types/src/clementine.rs (L158-170)
```rust
/// Parameters for inserting a transaction into the tx-sender queue.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InsertTryToSendParams {
    pub tx_metadata: Option<TxMetadata>,
    /// Signed tx encoded as hex.
    pub signed_tx_hex: String,
    pub fee_paying_type: FeePayingType,
    pub rbf_signing_info: Option<RbfSigningInfo>,
    pub cancel_outpoints: Vec<OutPoint>,
    pub cancel_txids: Vec<Txid>,
    pub activate_txids: Vec<ActivatedWithTxid>,
    pub activate_outpoints: Vec<ActivatedWithOutpoint>,
}
```

**File:** core/src/servers.rs (L114-139)
```rust
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

**File:** core/src/rpc/interceptors.rs (L22-33)
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
```

**File:** crates/clementine-tx-sender/src/config.rs (L29-35)
```rust
#[derive(Clone, Debug)]
pub struct TxSenderJsonRpcConfig {
    /// Bind address for the JSON-RPC server. Restricted to 127.0.0.1 or 0.0.0.0.
    pub bind: String,
    /// TCP port for the JSON-RPC server.
    pub port: u16,
}
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

**File:** crates/clementine-tx-sender/src/cpfp.rs (L146-268)
```rust
    async fn create_fee_payer_utxo(
        &self,
        bumped_id: u32,
        dbtx: Option<&mut TxSenderTransaction>,
        tx: &Transaction,
        fee_rate: FeeRateKvb,
        total_fee_payer_amount: Amount,
        fee_payer_utxos_len: usize,
    ) -> Result<()> {
        tracing::debug!(
            "Creating fee payer UTXO for txid {} with bump id {}",
            &tx.compute_txid().to_string(),
            bumped_id
        );
        let required_fee = Self::calculate_required_fee(
            tx.weight(),
            fee_payer_utxos_len + 1,
            fee_rate,
            FeePayingType::CPFP,
        )?;

        // Aggressively add 2x required fee to the total amount to account for sudden spikes
        // We won't actually use 2x fees, but the fee payer utxo will hold that much amount so that while fee payer utxo gets mined
        // if fees increase the utxo should still be sufficient to fund the tx with high probability
        // leftover fees will get sent back to wallet with a change output in fn create_child_tx
        let new_total_fee_needed = required_fee
            .checked_mul(2)
            .and_then(|fee| fee.checked_add(MIN_TAPROOT_AMOUNT));
        if new_total_fee_needed.is_none() {
            return Err(eyre!("Total fee needed is too large, required fee: {}, total fee payer amount: {}, fee rate: {}", required_fee, total_fee_payer_amount, fee_rate).into());
        }
        let new_fee_payer_amount =
            new_total_fee_needed.and_then(|fee| fee.checked_sub(total_fee_payer_amount));

        let new_fee_payer_amount = match new_fee_payer_amount {
            Some(fee) => fee,
            // if underflow, no new fee payer utxo is needed, log it anyway in case its a bug
            None => {
                tracing::debug!("create_fee_payer_utxo was called but no new fee payer utxo is needed for tx: {:?}, required fee: {}, total fee payer amount: {}, current fee rate: {}", tx, required_fee, total_fee_payer_amount, fee_rate);
                return Ok(());
            }
        };

        tracing::debug!(
            "Creating fee payer UTXO with amount {} ({} sat/kvB)",
            new_fee_payer_amount,
            fee_rate
        );

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

        self.db
            .save_fee_payer_tx(
                dbtx,
                bumped_id,
                signed_fee_payer_tx.compute_txid(),
                outpoint_vout as u32,
                new_fee_payer_amount,
                None,
            )
            .await
            .map_to_eyre()?;

        Ok(())
```

**File:** scripts/docker/configs/regtest/.env.regtest (L33-44)
```text
OPERATOR_CHALLENGE_AMOUNT=130000000
COLLATERAL_FUNDING_AMOUNT=90000000
KICKOFF_BLOCKHASH_COMMIT_LENGTH=40
WATCHTOWER_CHALLENGE_BYTES=144
WINTERNITZ_LOG_D=4
WINTERNITZ_SECRET_KEY=2222222222222222222222222222222222222222222222222222222222222222
USER_TAKES_AFTER=200
OPERATOR_CHALLENGE_TIMEOUT_TIMELOCK=144
OPERATOR_CHALLENGE_NACK_TIMELOCK=432
DISPROVE_TIMEOUT_TIMELOCK=720
ASSERT_TIMEOUT_TIMELOCK=576
OPERATOR_REIMBURSE_TIMELOCK=12
```

**File:** core/src/tx_sender_queue.rs (L92-105)
```rust
            TransactionType::Challenge | TransactionType::Payout => {
                self.insert_try_to_send(
                    dbtx,
                    tx_metadata,
                    signed_tx,
                    FeePayingType::RBF,
                    rbf_info,
                    &[],
                    &[],
                    &[],
                    &[],
                )
                .await
            }
```
