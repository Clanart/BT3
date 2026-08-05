[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** rpc/src/rpc.rs (L3865-4008)
```rust
        fn send_transaction(
            &self,
            meta: Self::Metadata,
            data: String,
            config: Option<RpcSendTransactionConfig>,
        ) -> Result<String> {
            debug!("send_transaction rpc request received");
            let RpcSendTransactionConfig {
                skip_preflight,
                preflight_commitment,
                encoding,
                max_retries,
                min_context_slot,
            } = config.unwrap_or_default();
            let tx_encoding = encoding.unwrap_or(UiTransactionEncoding::Base58);
            let binary_encoding = tx_encoding.into_binary_encoding().ok_or_else(|| {
                Error::invalid_params(format!(
                    "unsupported encoding: {tx_encoding}. Supported encodings: base58, base64"
                ))
            })?;
            let (wire_transaction, unsanitized_tx) =
                decode_and_deserialize::<VersionedTransaction>(data, binary_encoding)?;

            let preflight_commitment = if skip_preflight {
                Some(CommitmentConfig::processed())
            } else {
                preflight_commitment.map(|commitment| CommitmentConfig { commitment })
            };
            let preflight_bank = &*meta.get_bank_with_config(RpcContextConfig {
                commitment: preflight_commitment,
                min_context_slot,
            })?;

            let transaction = sanitize_transaction(
                unsanitized_tx,
                preflight_bank,
                preflight_bank.get_reserved_account_keys(),
            )?;
            let blockhash = *transaction.message().recent_blockhash();
            let message_hash = *transaction.message_hash();
            let signature = *transaction.signature();

            let mut last_valid_block_height = preflight_bank
                .get_blockhash_last_valid_block_height(&blockhash)
                .unwrap_or(0);

            let durable_nonce_info = transaction
                .get_durable_nonce()
                .map(|&pubkey| (pubkey, blockhash));
            if durable_nonce_info.is_some() || (skip_preflight && last_valid_block_height == 0) {
                // While it uses a defined constant, this last_valid_block_height value is chosen arbitrarily.
                // It provides a fallback timeout for durable-nonce transaction retries in case of
                // malicious packing of the retry queue. Durable-nonce transactions are otherwise
                // retried until the nonce is advanced.
                last_valid_block_height =
                    preflight_bank.block_height() + preflight_bank.max_processing_age() as u64;
            }

            if !skip_preflight {
                let verification_error = transaction.verify().err();

                if verification_error.is_none() && !meta.config.skip_preflight_health_check {
                    match meta.health.check() {
                        RpcHealthStatus::Ok => (),
                        RpcHealthStatus::Unknown => {
                            inc_new_counter_info!("rpc-send-tx_health-unknown", 1);
                            return Err(RpcCustomError::NodeUnhealthy {
                                num_slots_behind: None,
                            }
                            .into());
                        }
                        RpcHealthStatus::Behind { num_slots } => {
                            inc_new_counter_info!("rpc-send-tx_health-behind", 1);
                            return Err(RpcCustomError::NodeUnhealthy {
                                num_slots_behind: Some(num_slots),
                            }
                            .into());
                        }
                    }
                }

                let simulation_result = if let Some(err) = verification_error {
                    TransactionSimulationResult::new_error(err)
                } else {
                    preflight_bank.simulate_transaction(&transaction, false)
                };

                if let TransactionSimulationResult {
                    result: Err(err),
                    logs,
                    post_simulation_accounts: _,
                    units_consumed,
                    loaded_accounts_data_size,
                    return_data,
                    inner_instructions: _, // Always `None` due to `enable_cpi_recording = false`
                    fee,
                    pre_balances: _,
                    post_balances: _,
                    pre_token_balances: _,
                    post_token_balances: _,
                } = simulation_result
                {
                    match err {
                        TransactionError::BlockhashNotFound => {
                            inc_new_counter_info!("rpc-send-tx_err-blockhash-not-found", 1);
                        }
                        _ => {
                            inc_new_counter_info!("rpc-send-tx_err-other", 1);
                        }
                    }
                    return Err(RpcCustomError::SendTransactionPreflightFailure {
                        message: format!("Transaction simulation failed: {err}"),
                        result: RpcSimulateTransactionResult {
                            err: Some(err.into()),
                            logs: Some(logs),
                            accounts: None,
                            units_consumed: Some(units_consumed),
                            loaded_accounts_data_size: Some(loaded_accounts_data_size),
                            return_data: return_data.map(|return_data| return_data.into()),
                            inner_instructions: None,
                            replacement_blockhash: None,
                            fee,
                            pre_balances: None,
                            post_balances: None,
                            pre_token_balances: None,
                            post_token_balances: None,
                            loaded_addresses: None,
                        },
                    }
                    .into());
                }
            }

            _send_transaction(
                meta,
                message_hash,
                signature,
                blockhash,
                wire_transaction,
                last_valid_block_height,
                durable_nonce_info,
                max_retries,
            )
        }
```
