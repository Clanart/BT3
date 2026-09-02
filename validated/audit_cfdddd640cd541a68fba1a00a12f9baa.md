`default_tx_sender_limits` and the `TxSenderLimits` struct it produces are purely fee-policy configuration (fee rate cap, mempool fee multiplier/offset, CPFP wait time, fee-bump-after-blocks, min bump increment) used by the tx-sender to decide fee rates when broadcasting/bumping transactions.Confirmed: `default_tx_sender_limits` in `core/src/config/mod.rs` only supplies fee-policy defaults (`fee_rate_hard_cap`, `mempool_fee_rate_multiplier`, `mempool_fee_rate_offset_sat_kvb`, `cpfp_fee_payer_bump_wait_time_seconds`, `fee_bump_after_blocks`, `min_bump_kvb`) via `TxSenderLimits::default()`.### No vulnerability found for this question.

`default_tx_sender_limits` in `core/src/config/mod.rs` only returns `TxSenderLimits::default()` — fee-policy parameters (`fee_rate_hard_cap`, `mempool_fee_rate_multiplier`, `mempool_fee_rate_offset_sat_kvb`, `cpfp_fee_payer_bump_wait_time_seconds`, `fee_bump_after_blocks`, `min_bump_kvb`) used by the tx-sender to decide broadcast/fee-bump behavior [1](#0-0) [2](#0-1) . This struct is consumed only by `BridgeConfig::tx_sender_config` / `TxSender::get_fee_rate` to compute Bitcoin fee rates [3](#0-2) [4](#0-3) . It has no relationship to decrypted keys, Winternitz preimages, or challenge-ack preimages.

The actual pre-reveal material the question describes (Winternitz secret keys, challenge-ack preimages) is derived and stored via `Actor::get_derived_winternitz_sk` / `generate_preimage_from_path` [5](#0-4) , and only public hashes/pubkeys (not preimages) are exposed over gRPC through `get_deposit_keys` -> `generate_challenge_ack_preimages_and_hashes` [6](#0-5) [7](#0-6) . Actual preimages are only revealed by signing/broadcasting the `OperatorChallengeAck` transaction on-chain [8](#0-7) , a path entirely disconnected from `default_tx_sender_limits`.

Since the file/function named in the question does not handle, store, transmit, or gate disclosure of any secret or pre-reveal material, there is no binding to break and no exploit path through this function.

### Citations

**File:** core/src/config/mod.rs (L180-182)
```rust
fn default_tx_sender_limits() -> TxSenderLimits {
    TxSenderLimits::default()
}
```

**File:** core/src/config/mod.rs (L343-376)
```rust
    /// Build a tx-sender standalone config from this bridge config.
    ///
    /// This keeps tx-sender wiring centralized in the config module, so core can
    /// run tx-sender using a single derived config object.
    #[cfg(feature = "automation")]
    pub fn tx_sender_config(&self) -> clementine_tx_sender::config::TxSenderConfig {
        use clementine_tx_sender::config::{
            TxSenderBitcoinRpcConfig, TxSenderConfig, TxSenderPostgresConfig,
        };

        TxSenderConfig {
            network: self.protocol_paramset.network,
            secret_key: self.secret_key,
            private_da_key: None,
            postgres: TxSenderPostgresConfig {
                host: self.db_host.clone(),
                port: self.db_port,
                user: self.db_user.clone(),
                password: self.db_password.clone(),
                dbname: self.db_name.clone(),
            },
            bitcoin_rpc: TxSenderBitcoinRpcConfig {
                url: self.bitcoin_rpc_url.clone(),
                user: self.bitcoin_rpc_user.clone(),
                password: self.bitcoin_rpc_password.clone(),
            },
            mempool: self.mempool_config(),
            limits: self.tx_sender_limits.clone(),
            finality_depth: self.protocol_paramset.finality_depth,
            // poll_delay_ms not used in clementine, poll delay for txsender is defined in core/src/task/tx_sender.rs
            poll_delay_ms: 60_000,
            include_unsafe: false,
            jsonrpc: None,
        }
```

**File:** crates/clementine-config/src/tx_sender.rs (L23-34)
```rust
impl Default for TxSenderLimits {
    fn default() -> Self {
        Self {
            fee_rate_hard_cap: 100,
            mempool_fee_rate_multiplier: 1,
            mempool_fee_rate_offset_sat_kvb: 0,
            cpfp_fee_payer_bump_wait_time_seconds: 60 * 60, // 1 hour in seconds
            fee_bump_after_blocks: 10,
            // 0.2 sat/vB ~= 200 sat/kvB
            min_bump_kvb: 200,
        }
    }
```

**File:** crates/clementine-tx-sender/src/lib.rs (L205-217)
```rust
    pub async fn get_fee_rate(&self) -> Result<FeeRateKvb, BridgeError> {
        self.rpc
            .get_fee_rate_kvb(
                self.network,
                &self.mempool_config.host,
                &self.mempool_config.endpoint,
                self.tx_sender_limits.mempool_fee_rate_multiplier,
                self.tx_sender_limits.mempool_fee_rate_offset_sat_kvb,
                self.tx_sender_limits.fee_rate_hard_cap,
            )
            .await
            .map_err(|e| BridgeError::Eyre(e.into()))
    }
```

**File:** core/src/actor.rs (L287-339)
```rust
    /// Returns derivied Winternitz secret key from given path.
    pub fn get_derived_winternitz_sk(
        &self,
        path: WinternitzDerivationPath,
    ) -> Result<winternitz::SecretKey, BridgeError> {
        let hk = Hkdf::<Sha256>::new(None, self.keypair.secret_key().as_ref());
        let path_bytes = path.to_bytes();
        let mut derived_key = vec![0u8; 32];
        hk.expand(&path_bytes, &mut derived_key)
            .map_err(|e| eyre::eyre!("Key derivation failed: {:?}", e))?;

        Ok(derived_key)
    }

    /// Generates a Winternitz public key for the given path.
    pub fn derive_winternitz_pk(
        &self,
        path: WinternitzDerivationPath,
    ) -> Result<winternitz::PublicKey, BridgeError> {
        let winternitz_params = path.get_params();

        let altered_secret_key = self.get_derived_winternitz_sk(path)?;
        let public_key = winternitz::generate_public_key(&winternitz_params, &altered_secret_key);

        Ok(public_key)
    }

    /// Signs given data with Winternitz signature.
    #[cfg(test)]
    pub fn sign_winternitz_signature(
        &self,
        path: WinternitzDerivationPath,
        data: Vec<u8>,
    ) -> Result<Witness, BridgeError> {
        let winternitz = Winternitz::<BinarysearchVerifier, ToBytesConverter>::new();

        let winternitz_params = path.get_params();

        let altered_secret_key = self.get_derived_winternitz_sk(path)?;

        let witness = winternitz.sign(&winternitz_params, &altered_secret_key, &data);

        Ok(witness)
    }

    pub fn generate_preimage_from_path(
        &self,
        path: WinternitzDerivationPath,
    ) -> Result<PublicHash, BridgeError> {
        let first_preimage = self.get_derived_winternitz_sk(path)?;
        let second_preimage = hash160::Hash::hash(&first_preimage);
        Ok(second_preimage.to_byte_array())
    }
```

**File:** core/src/rpc/operator.rs (L298-328)
```rust
    #[tracing::instrument(skip_all, err(level = tracing::Level::ERROR))]
    async fn get_deposit_keys(
        &self,
        request: Request<DepositParams>,
    ) -> Result<Response<OperatorKeys>, Status> {
        let start = std::time::Instant::now();
        let deposit_params = request.into_inner();
        let deposit_data: DepositData = deposit_params.try_into()?;
        tracing::info!(
            "Called get_deposit_keys with deposit data: {:?}",
            deposit_data
        );
        let winternitz_keys = self
            .operator
            .generate_assert_winternitz_pubkeys(deposit_data.get_deposit_outpoint())?;
        let hashes = self
            .operator
            .generate_challenge_ack_preimages_and_hashes(&deposit_data)?;
        tracing::info!("Generated deposit keys in {:?}", start.elapsed());

        Ok(Response::new(OperatorKeys {
            winternitz_pubkeys: winternitz_keys
                .into_iter()
                .map(|pubkey| pubkey.into())
                .collect(),
            challenge_ack_digests: hashes
                .into_iter()
                .map(|hash| ChallengeAckDigest { hash: hash.into() })
                .collect(),
        }))
    }
```

**File:** core/src/operator.rs (L811-837)
```rust
    pub fn generate_challenge_ack_preimages_and_hashes(
        &self,
        deposit_data: &DepositData,
    ) -> Result<Vec<PublicHash>, BridgeError> {
        let mut hashes = Vec::with_capacity(self.config.get_num_challenge_ack_hashes(deposit_data));

        for watchtower_idx in 0..deposit_data.get_num_watchtowers() {
            let path = WinternitzDerivationPath::ChallengeAckHash(
                watchtower_idx as u32,
                deposit_data.get_deposit_outpoint(),
                self.config.protocol_paramset(),
            );
            let hash = self.signer.generate_public_hash_from_path(path)?;
            hashes.push(hash);
        }

        if hashes.len() != self.config.get_num_challenge_ack_hashes(deposit_data) {
            return Err(eyre::eyre!(
                "Expected {} number of challenge ack hashes, but got {}",
                self.config.get_num_challenge_ack_hashes(deposit_data),
                hashes.len()
            )
            .into());
        }

        Ok(hashes)
    }
```

**File:** core/src/builder/transaction/sign.rs (L168-180)
```rust
        if let TransactionType::OperatorChallengeAck(watchtower_idx) = tx_type {
            let path = WinternitzDerivationPath::ChallengeAckHash(
                watchtower_idx as u32,
                context
                    .deposit_data
                    .as_ref()
                    .expect("Should have deposit data at this point")
                    .get_deposit_outpoint(),
                config.protocol_paramset(),
            );
            let preimage = signer.generate_preimage_from_path(path)?;
            let _ = signer.tx_sign_preimage(&mut txhandler, preimage);
        }
```
