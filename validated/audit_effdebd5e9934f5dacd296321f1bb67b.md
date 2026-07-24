### Title
Unauthenticated `send_tx` JSON-RPC Endpoint Allows Arbitrary Transaction Submission and CPFP Wallet Drain on Behalf of TxSender - (`crates/clementine-tx-sender/src/jsonrpc/server.rs`)

### Summary

The standalone tx-sender service exposes a JSON-RPC server (`send_tx`) with **no authentication layer**. Any caller who can reach the endpoint can submit an arbitrary signed Bitcoin transaction with any `FeePayingType`, including `CPFP`. When `CPFP` is selected, the tx-sender automatically funds the submitted transaction from its own Bitcoin Core wallet by calling `fund_raw_transaction` and `sign_raw_transaction_with_wallet`, broadcasting a fee-payer UTXO on behalf of the bridge. This is the direct Clementine analog of the `VoterProxy.vote()` bug: an unprivileged caller can make the privileged tx-sender component spend its own wallet funds on arbitrary transactions, draining the balances used to fund legitimate bridge operations (kickoff, payout, reimbursement, etc.).

### Finding Description

**Root cause — no authentication on the JSON-RPC server**

`start_jsonrpc_server` in `crates/clementine-tx-sender/src/jsonrpc/server.rs` builds a plain HTTP `jsonrpsee` server with no mTLS, API key, IP allowlist, or any other access control: [1](#0-0) 

The `send_tx` handler deserializes any caller-supplied hex transaction and passes it directly to `TxSenderClient::insert_try_to_send` with the caller-chosen `fee_paying_type`, `cancel_outpoints`, `cancel_txids`, `activate_txids`, and `activate_outpoints` — all without any validation of the caller's identity or the transaction's relationship to bridge state: [2](#0-1) 

**Network exposure — `0.0.0.0` binding is explicitly supported**

The configuration layer explicitly permits binding to `0.0.0.0`, which exposes the unauthenticated endpoint to any network-reachable host: [3](#0-2) 

In a distributed deployment (the intended use case for the standalone binary), the aggregator, operator, and verifier all reside on separate machines and must reach the tx-sender over the network, making `0.0.0.0` a realistic production configuration.

**CPFP path — wallet funds spent on attacker-controlled transactions**

When `FeePayingType::CPFP` is submitted, the tx-sender loop calls `send_cpfp_tx`, which calls `create_fee_payer_utxo`. This function calls `fund_raw_transaction` and `sign_raw_transaction_with_wallet` on the Bitcoin Core RPC, creating and broadcasting a fee-payer UTXO funded from the tx-sender's own wallet — regardless of whether the parent transaction is valid or bridge-related: [4](#0-3) 

The fee-payer transaction is broadcast to the Bitcoin network before any attempt to submit the CPFP package. Even if the parent transaction is invalid (spending non-existent UTXOs), the fee-payer UTXO is already spent from the wallet.

**The tx-sender wallet funds legitimate bridge operations**

The same wallet is used to fund CPFP fee-payer UTXOs for all critical bridge transaction types: `Kickoff`, `Payout`, `Reimburse`, `ReadyToReimburse`, `Round`, `OptimisticPayout`, `WatchtowerChallenge`, `AssertTimeout`, etc.: [5](#0-4) 

### Impact Explanation

An attacker who can reach the JSON-RPC endpoint (reachable when `TX_SENDER_JSONRPC_BIND=0.0.0.0`) can:

1. **Drain the tx-sender's Bitcoin wallet** by submitting many transactions with `FeePayingType::CPFP` and a P2A anchor output. Each submission causes the tx-sender to broadcast a funded fee-payer UTXO from its wallet.
2. **Starve legitimate bridge transactions** of CPFP funding. Without wallet funds, the tx-sender cannot create fee-payer UTXOs for kickoff, payout, reimbursement, and challenge-timeout transactions.
3. **Cause slashable operator exposure**: if the operator misses a challenge window (e.g., `AssertTimeout`, `WatchtowerChallengeTimeout`) because the tx-sender cannot fund the required transaction, the operator's collateral can be slashed.
4. **Block reimbursement**: if `ReadyToReimburse` or `Reimburse` transactions cannot be funded, the operator loses the BTC it fronted for withdrawals.

This directly matches the allowed impact gate: loss of tx-sender-managed balances and slashable exposure of operator collateral.

### Likelihood Explanation

- The standalone tx-sender binary is explicitly designed for distributed deployments where `0.0.0.0` binding is required.
- The configuration explicitly allows `0.0.0.0` and documents it as a valid option.
- There is zero authentication code in the JSON-RPC server — no mechanism exists to add it without code changes.
- The attack requires only HTTP access to the tx-sender port and knowledge of the `send_tx` JSON-RPC method (documented in `run.sh` and the client crate).
- No bridge keys, operator credentials, or privileged access are needed.

### Recommendation

1. **Add authentication to the JSON-RPC server.** At minimum, require a shared secret (bearer token or HMAC) on every request. Preferably, use mTLS with the same certificate infrastructure already used for gRPC actors.
2. **Restrict `FeePayingType` accepted via JSON-RPC.** External callers should not be able to request `CPFP` or `RBF` — only `NoFunding` (pre-signed, self-funded transactions) should be accepted from untrusted callers.
3. **Enforce an IP allowlist** at the application layer (not just at the OS/firewall level) when `0.0.0.0` binding is used.
4. **Validate that submitted transactions are bridge-related** (e.g., check that inputs reference known bridge UTXOs) before queuing them for fee-bumping.

### Proof of Concept

**Precondition:** tx-sender is running with `TX_SENDER_JSONRPC_BIND=0.0.0.0` and `TX_SENDER_JSONRPC_PORT=3030` (a realistic distributed deployment).

**Steps:**

1. Craft a minimal Bitcoin transaction with a P2A anchor output (OP_TRUE / OP_RETURN anchor) and a dummy input spending a non-existent UTXO.
2. Serialize it to hex.
3. Send the following JSON-RPC request to `http://<tx-sender-host>:3030`:

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "send_tx",
  "params": [{
    "tx_metadata": null,
    "signed_tx_hex": "<hex of crafted tx with P2A anchor>",
    "fee_paying_type": "cpfp",
    "rbf_signing_info": null,
    "cancel_outpoints": [],
    "cancel_txids": [],
    "activate_txids": [],
    "activate_outpoints": []
  }]
}
```

4. The tx-sender accepts the request (HTTP 200, returns a `try_to_send_id`).
5. On the next tx-sender loop iteration, `send_cpfp_tx` is called. Since no confirmed fee-payer UTXOs exist, `create_fee_payer_utxo` is called.
6. `fund_raw_transaction` and `sign_raw_transaction_with_wallet` are called on the Bitcoin Core RPC, spending from the tx-sender's wallet.
7. The fee-payer transaction is broadcast to the Bitcoin network (`send_raw_transaction`).
8. Repeat steps 1–7 in a loop to drain the wallet.

**Expected outcome:** The tx-sender's Bitcoin Core wallet balance decreases with each iteration. After sufficient iterations, the wallet is exhausted and the tx-sender can no longer fund CPFP fee-payer UTXOs for legitimate bridge transactions (kickoff, payout, reimbursement), causing liveness failure and potential operator slashing. [6](#0-5) [7](#0-6) [3](#0-2) [8](#0-7)

### Citations

**File:** crates/clementine-tx-sender/src/jsonrpc/server.rs (L42-111)
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

    // Citrea-specific RPCs.
    #[cfg(feature = "citrea")]
    {
        module
            .register_async_method("send_citrea_tx", |params, client, _| async move {
                let req: InsertCitreaRawTxParams = params.one().map_err(jsonrpc_err)?;

                let insertion_id = client
                    .send_citrea_tx(req.citrea_tx_request)
                    .await
                    .map_err(jsonrpc_err)?;

                Ok::<i64, ErrorObjectOwned>(insertion_id)
            })
            .map_err(|e| BridgeError::Eyre(e.into()))?;
    }

    let handle = server.start(module);

    Ok(TxSenderJsonRpcServer { handle, local_addr })
}
```

**File:** crates/clementine-tx-sender/src/config.rs (L30-35)
```rust
pub struct TxSenderJsonRpcConfig {
    /// Bind address for the JSON-RPC server. Restricted to 127.0.0.1 or 0.0.0.0.
    pub bind: String,
    /// TCP port for the JSON-RPC server.
    pub port: u16,
}
```

**File:** crates/clementine-tx-sender/src/config.rs (L197-211)
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

**File:** core/src/tx_sender_queue.rs (L57-91)
```rust
        match tx_type {
            TransactionType::Kickoff
            | TransactionType::Dummy
            | TransactionType::ChallengeTimeout
            | TransactionType::DisproveTimeout
            | TransactionType::Reimburse
            | TransactionType::Round
            | TransactionType::OperatorChallengeNack(_)
            | TransactionType::UnspentKickoff(_)
            | TransactionType::MoveToVault
            | TransactionType::BurnUnusedKickoffConnectors
            | TransactionType::KickoffNotFinalized
            | TransactionType::MiniAssert(_)
            | TransactionType::LatestBlockhashTimeout
            | TransactionType::LatestBlockhash
            | TransactionType::EmergencyStop
            | TransactionType::OptimisticPayout
            | TransactionType::ReadyToReimburse
            | TransactionType::ReplacementDeposit
            | TransactionType::WatchtowerChallenge(_)
            | TransactionType::AssertTimeout(_) => {
                // no_dependency and cpfp
                self.insert_try_to_send(
                    dbtx,
                    tx_metadata,
                    signed_tx,
                    FeePayingType::CPFP,
                    rbf_info,
                    &[],
                    &[],
                    &[],
                    &[],
                )
                .await
            }
```
