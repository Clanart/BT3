### Title
Unauthenticated JSON-RPC `send_tx` Endpoint Allows Arbitrary `rbf_signing_info` Injection, Enabling Theft of tx-sender-Managed Balances — (`crates/clementine-tx-sender/src/jsonrpc/server.rs`)

---

### Summary

The tx-sender JSON-RPC server exposes a `send_tx` method with zero authentication. Any caller that can reach the socket can submit an `InsertTryToSendParams` with a fully attacker-controlled `rbf_signing_info` (including `vout`, `spend_path=KeyPath{tweak_merkle_root}`, and `tap_sighash_type`). The stored signing info is later consumed by `send_rbf_tx → attempt_sign_psbt → signer.sign_with_tweak_data`, causing the tx-sender's Taproot key to sign an attacker-crafted sighash over an attacker-chosen UTXO. Because the tx-sender's signing key is the sole authority over its own fee-paying UTXOs (which the Bitcoin Core wallet does not hold), this path enables direct theft of tx-sender-managed balances.

---

### Finding Description

**Step 1 — Unauthenticated entrypoint.**

`start_jsonrpc_server` builds the server with `ServerBuilder::default()` and registers `send_tx` with no middleware, no token check, and no mTLS: [1](#0-0) 

The bind address is configurable as either `127.0.0.1` or `0.0.0.0`; the code explicitly permits `0.0.0.0`: [2](#0-1) 

When bound to `0.0.0.0`, any network peer is an unprivileged caller. Even with `127.0.0.1`, any co-located process (another container, SSRF pivot, etc.) can reach it. No authentication exists in either case.

**Step 2 — Attacker-controlled `rbf_signing_info` stored verbatim.**

The handler deserialises `InsertTryToSendParams` and passes `req.rbf_signing_info` directly to `insert_try_to_send` without any validation: [3](#0-2) 

**Step 3 — Task loop calls `send_rbf_tx` with the stored signing info.**

The background task loop calls `try_to_send_unconfirmed_txs`, which calls `send_rbf_tx` with the `rbf_signing_info` retrieved from the DB. On the initial-send path, `attempt_sign_psbt` is called unconditionally whenever `rbf_signing_info` is `Some`: [4](#0-3) 

**Step 4 — `attempt_sign_psbt` computes sighash from attacker-controlled fields.**

`input_index` is taken directly from `rbf_signing_info.vout`. The prevouts are extracted from the PSBT (which Bitcoin Core's `fund_raw_transaction` populates from the UTXO set, including the attacker-specified input). The sighash is computed over those prevouts with the attacker's `tap_sighash_type`: [5](#0-4) 

**Step 5 — Signer applies attacker-chosen tweak and signs.**

The `tweak_merkle_root` from `rbf_signing_info.spend_path` is passed verbatim to `sign_with_tweak_data`: [6](#0-5) 

`sign_with_tweak_data` computes `TapTweakHash::from_key_and_tweak(xonly, attacker_merkle_root)` and signs the sighash with the resulting tweaked keypair: [7](#0-6) 

**Step 6 — The tx-sender's own UTXOs are the target.**

The tx-sender's funded UTXOs are locked to `Address::p2tr(&SECP, xonly, None, network)` — i.e., the key tweaked with `merkle_root = None`. The Bitcoin Core wallet does not hold this private key; only the tx-sender's `TxSenderSigningKey` can sign for it: [8](#0-7) 

An attacker who sets `tweak_merkle_root = None` and `vout = 0` in `rbf_signing_info`, and submits a `signed_tx_hex` that spends a known tx-sender UTXO to an attacker-controlled output, will receive a valid Schnorr signature from the tx-sender over that spend. Bitcoin Core's `fund_raw_transaction` will populate `witness_utxo` from the UTXO set, making the sighash computation valid. The resulting transaction is broadcastable.

---

### Impact Explanation

The tx-sender's signing key is the sole authority over its fee-paying UTXOs (tx-sender-managed balances, explicitly in scope). An attacker who can reach the JSON-RPC socket can drain all such UTXOs in a single request cycle. Loss of these balances halts fee-bumping for all pending bridge transactions, causing liveness failure for deposits, withdrawals, and challenge flows. The impact is **theft/permanent loss of tx-sender-managed balances** and **bridge liveness failure**.

---

### Likelihood Explanation

- When `TX_SENDER_JSONRPC_BIND=0.0.0.0` (explicitly supported by the code), any network attacker can exploit this with a single HTTP POST.
- Even with the default `127.0.0.1` binding, any co-located process (another bridge component, a compromised sidecar, or an SSRF in any HTTP-speaking component on the same host) can reach the endpoint.
- The attack requires only knowledge of the tx-sender's public address (derivable from its public key, observable on-chain) and the ability to form a valid JSON-RPC request. No cryptographic material needs to be compromised.

---

### Recommendation

1. **Add authentication to the JSON-RPC server.** Implement a shared-secret bearer token or restrict callers via an allowlist checked in a `tower` middleware layer before any method is dispatched.
2. **Validate `rbf_signing_info` against the stored transaction.** Before signing, verify that the input at `rbf_signing_info.vout` in the PSBT actually corresponds to a UTXO that the tx-sender is authorised to sign (e.g., by checking the scriptPubKey matches the tx-sender's own address).
3. **Do not allow `0.0.0.0` binding without mandatory authentication.** If network-wide binding is required, enforce authentication before it is permitted.

---

### Proof of Concept

```rust
// 1. Identify a tx-sender UTXO on-chain (address = p2tr(xonly, None)).
// 2. Craft a transaction spending that UTXO, output to attacker address.
// 3. POST to the tx-sender JSON-RPC (no credentials needed):
let req = InsertTryToSendParams {
    signed_tx_hex: hex::encode(consensus::serialize(&attacker_tx)),
    fee_paying_type: FeePayingType::RBF,
    rbf_signing_info: Some(RbfSigningInfo::new(
        0, // vout: index of the tx-sender's UTXO input
        RbfSigningSpendPath::KeyPath { tweak_merkle_root: None }, // matches tx-sender address
        TapSighashType::Default,
    )),
    tx_metadata: None,
    cancel_outpoints: vec![],
    cancel_txids: vec![],
    activate_txids: vec![],
    activate_outpoints: vec![],
};
// HTTP POST {"jsonrpc":"2.0","method":"send_tx","params":[req],"id":1}
// On the next task-loop tick, attempt_sign_psbt produces a valid Schnorr
// signature over the attacker's sighash; the tx is broadcast and confirmed.
```

### Citations

**File:** crates/clementine-tx-sender/src/jsonrpc/server.rs (L46-58)
```rust
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
```

**File:** crates/clementine-tx-sender/src/jsonrpc/server.rs (L59-79)
```rust
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

**File:** crates/clementine-tx-sender/src/rbf.rs (L269-314)
```rust
        let input_index = rbf_signing_info.vout as usize;

        // Get the transaction to calculate the sighash
        let tx = decoded_psbt.unsigned_tx.clone();
        let mut sighash_cache = SighashCache::new(&tx);

        let tap_sighash_type = rbf_signing_info.tap_sighash_type;

        // Calculate the sighash for this input
        // Extract previous outputs from the PSBT
        let prevouts: Vec<bitcoin::TxOut> = decoded_psbt
            .inputs
            .iter()
            .zip(tx.input.iter())
            .map(|(psbt_input, tx_input)| {
                // Try witness_utxo first (for segwit inputs)
                if let Some(witness_utxo) = psbt_input.witness_utxo.clone() {
                    Ok(witness_utxo)
                } else if let Some(ref non_witness_tx) = psbt_input.non_witness_utxo {
                    // For non-segwit inputs, extract the output from the previous transaction
                    let vout = tx_input.previous_output.vout as usize;
                    non_witness_tx
                        .output
                        .get(vout)
                        .cloned()
                        .ok_or_eyre(format!(
                            "Output index {vout} out of bounds in previous transaction",
                        ))
                        .map_err(SendTxError::Other)
                } else {
                    Err(eyre!(
                        "Neither witness_utxo nor non_witness_utxo found for input"
                    ))
                    .map_err(SendTxError::Other)
                }
            })
            .collect::<Result<Vec<_>>>()?;

        let sighash = match &rbf_signing_info.spend_path {
            RbfSigningSpendPath::KeyPath { .. } => sighash_cache
                .taproot_key_spend_signature_hash(
                    input_index,
                    &Prevouts::All(&prevouts),
                    tap_sighash_type,
                )
                .map_err(|e| eyre!("Failed to calculate sighash: {}", e))?,
```

**File:** crates/clementine-tx-sender/src/rbf.rs (L358-368)
```rust
        let tweak_data = match &rbf_signing_info.spend_path {
            RbfSigningSpendPath::KeyPath { tweak_merkle_root } => {
                TapTweakData::KeyPath(*tweak_merkle_root)
            }
            RbfSigningSpendPath::ScriptPath { .. } => TapTweakData::ScriptPath,
        };

        let signature = self
            .signer
            .sign_with_tweak_data(sighash, tweak_data)
            .map_err(|e| eyre!("Failed to sign input: {}", e))?;
```

**File:** crates/clementine-tx-sender/src/rbf.rs (L999-1015)
```rust
                if let Some(rbf_signing_info) = &rbf_signing_info {
                    psbt = self
                        .attempt_sign_psbt(process_result.psbt, rbf_signing_info, cached_leaf_hash)
                        .await
                        .map_err(|err| {
                            let err = eyre!(err).wrap_err("Failed to sign initial RBF PSBT");
                            self.handle_err(
                                format!("{err:?}"),
                                "rbf_psbt_sign_failed",
                                try_to_send_id,
                            );

                            err
                        })?;
                } else {
                    psbt = process_result.psbt;
                }
```

**File:** crates/clementine-tx-sender/src/signer.rs (L37-47)
```rust
    pub(crate) fn new(secret_key: SecretKey, network: Network) -> Self {
        let keypair = Keypair::from_secret_key(&SECP, &secret_key);
        let (xonly, _parity) = XOnlyPublicKey::from_keypair(&keypair);
        let address = Address::p2tr(&SECP, xonly, None, network);

        Self {
            keypair,
            xonly_public_key: xonly,
            address,
        }
    }
```

**File:** crates/clementine-tx-sender/src/signer.rs (L57-74)
```rust
    pub(crate) fn sign_with_tweak_data(
        &self,
        sighash: TapSighash,
        tweak_data: TapTweakData,
    ) -> Result<schnorr::Signature, BridgeError> {
        let keypair;
        let keypair_ref = match tweak_data {
            TapTweakData::KeyPath(merkle_root) => {
                keypair = calc_tweaked_keypair(&self.keypair, merkle_root)?;
                &keypair
            }
            TapTweakData::ScriptPath => &self.keypair,
            TapTweakData::Unknown => return Err(eyre::eyre!("Spend Data Unknown").into()),
        };

        Ok(SECP
            .sign_schnorr_no_aux_rand(&Message::from_digest(sighash.to_byte_array()), keypair_ref))
    }
```
