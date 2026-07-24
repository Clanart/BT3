### Title
`aggregator_verification_address` Guard Absent from `InternalWithdraw` RPC, Bypassed When `client_verification = false` — (File: `core/src/rpc/operator.rs`)

---

### Summary

`aggregator_verification_address` is a configured flag that enforces a mandatory ECDSA sign-off from Citrea/aggregator on every withdrawal. The `Withdraw` RPC and `sign_optimistic_payout` both gate on this flag. `InternalWithdraw` — the sibling withdrawal endpoint — never checks it at all. When `client_verification = false` (the `Noop` interceptor path), the mTLS cert-pinning that is supposed to restrict `InternalWithdraw` to the operator itself is also absent, so any TLS-connected peer can call `InternalWithdraw` and queue a payout transaction without the required Citrea/aggregator sign-off.

---

### Finding Description

**The unchecked flag — direct analog to the Solvent `is_staking_enabled` bug**

`aggregator_verification_address: Option<alloy::primitives::Address>` is documented as:

> "Used for both an extra verification of aggregator's identity and to force citrea to check withdrawal params manually during some time after launch."

`Withdraw` enforces it:

```rust
// core/src/rpc/operator.rs:209-238
if let Some(address_in_config) = self.operator.config.aggregator_verification_address {
    // ... recover address from ECDSA sig, compare, reject if mismatch
}
```

`sign_optimistic_payout` (verifier) enforces it:

```rust
// core/src/verifier.rs:1602-1622
if let Some(address_in_config) = self.config.aggregator_verification_address {
    // ... same pattern
}
```

`InternalWithdraw` does not check it at all — it calls `operator.withdraw()` directly:

```rust
// core/src/rpc/operator.rs:168-190
async fn internal_withdraw(
    &self,
    request: Request<WithdrawParams>,
) -> Result<Response<RawSignedTx>, Status> {
    let (...) = parser::operator::parse_withdrawal_sig_params(request.into_inner())?;
    // NO aggregator_verification_address check
    let payout_tx = self.operator.withdraw(...).await?;
    Ok(Response::new(RawSignedTx::from(&payout_tx)))
}
```

**The broken trust boundary — `client_verification = false` disables the only remaining guard**

The interceptor that restricts `InternalWithdraw` to the operator's own cert is only installed when `client_verification = true`:

```rust
// core/src/servers.rs:106-138
let tls_config = if config.client_verification {
    ServerTlsConfig::new().identity(server_identity).client_ca_root(client_ca)
} else {
    ServerTlsConfig::new().identity(server_identity)  // no client CA
};

let service = InterceptedService::new(
    service,
    if config.client_verification {
        OnlyAggregatorAndSelf { aggregator_cert, our_cert: client_cert }
    } else {
        Noop   // ← no cert check at all
    },
);
```

When `client_verification = false`, the `Noop` interceptor is active. The `is_internal()` check inside `only_aggregator_and_self` — which restricts `InternalWithdraw` to the operator's own cert — is never reached:

```rust
// core/src/rpc/interceptors.rs:62-69
if is_internal(&req) {
    if leaf_cert == our_cert { Ok(req) }
    else { Err(Status::unauthenticated("Unauthorized call to internal method (not self)")) }
}
```

With `Noop`, any peer that can complete the one-sided TLS handshake (server cert only) can call `InternalWithdraw`.

---

### Impact Explanation

When both conditions hold — `aggregator_verification_address` is `Some(addr)` and `client_verification = false` — an external party can:

1. Obtain a valid withdrawal UTXO registered in Citrea's bridge contract (public on-chain data).
2. Construct a valid user Taproot signature over the payout transaction (the user's key is the UTXO owner; the attacker can be the user themselves or observe the mempool).
3. Call `InternalWithdraw` directly, bypassing the Citrea/aggregator ECDSA sign-off entirely.
4. The operator's `withdraw()` logic queues the payout transaction to TxSender and broadcasts it to Bitcoin.

The operator's BTC is spent on a valid, Citrea-registered withdrawal, so there is no direct theft of operator collateral. However, the `aggregator_verification_address` safety gate — the mechanism by which Citrea retains manual veto power over withdrawals during the launch period — is rendered completely ineffective. Any party with network access can force the operator to process any pending withdrawal without Citrea's approval, defeating the purpose of the flag.

---

### Likelihood Explanation

`client_verification = false` is blocked on mainnet by `check_mainnet_requirements` but is a valid and documented configuration for testnet4, signet, and regtest deployments. The proto comment explicitly marks `InternalWithdraw` as "intended for operator's own use" — the design intent is that the cert interceptor provides the access control. The missing `aggregator_verification_address` check is therefore not a deliberate omission but an oversight: the flag was added to `Withdraw` and `sign_optimistic_payout` but not backported to `InternalWithdraw`.

---

### Recommendation

Add the same `aggregator_verification_address` guard to `internal_withdraw` in `core/src/rpc/operator.rs` that already exists in `withdraw`. Since `InternalWithdraw` takes `WithdrawParams` (no `verification_signature` field), either:

- Reject `InternalWithdraw` entirely when `aggregator_verification_address` is `Some`, or
- Extend `WithdrawParams` / add a separate internal message that carries the verification signature.

Additionally, consider whether `client_verification = false` should also block `InternalWithdraw` at the server level regardless of the interceptor state, since the proto explicitly states it is "for operator's own use."

---

### Proof of Concept

1. Operator is deployed on testnet4 with `client_verification = false` and `aggregator_verification_address = Some(citrea_addr)`.
2. A withdrawal UTXO `W` is registered in Citrea's bridge contract at index `i`. Citrea/aggregator has not yet issued a manual sign-off for it.
3. Attacker (the user who owns `W`, or any observer) constructs a valid Taproot `SIGHASH_SINGLE|ANYONECANPAY` signature over the payout transaction spending `W`.
4. Attacker calls `InternalWithdraw` on the operator's gRPC endpoint with `withdrawal_id = i`, the valid user signature, and desired output.
5. `internal_withdraw` skips the `aggregator_verification_address` check, calls `operator.withdraw()`, which validates the UTXO against Citrea's DB, verifies the user signature, checks profitability, and queues the payout transaction to TxSender.
6. The payout transaction is broadcast to Bitcoin. The withdrawal is processed without Citrea/aggregator's manual approval, defeating the safety gate entirely. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** core/src/rpc/operator.rs (L168-190)
```rust
    #[tracing::instrument(skip(self), err(level = tracing::Level::ERROR))]
    async fn internal_withdraw(
        &self,
        request: Request<WithdrawParams>,
    ) -> Result<Response<RawSignedTx>, Status> {
        let (withdrawal_id, input_signature, input_outpoint, output_script_pubkey, output_amount) =
            parser::operator::parse_withdrawal_sig_params(request.into_inner())?;

        tracing::warn!("Called internal_withdraw with withdrawal id: {:?}, input signature: {:?}, input outpoint: {:?}, output script pubkey: {:?}, output amount: {:?}", withdrawal_id, input_signature, input_outpoint, output_script_pubkey, output_amount);

        let payout_tx = self
            .operator
            .withdraw(
                withdrawal_id,
                input_signature,
                input_outpoint,
                output_script_pubkey,
                output_amount,
            )
            .await?;

        Ok(Response::new(RawSignedTx::from(&payout_tx)))
    }
```

**File:** core/src/rpc/operator.rs (L209-239)
```rust
        // if verification address is set in config, check if verification signature is valid
        if let Some(address_in_config) = self.operator.config.aggregator_verification_address {
            let verification_signature = params
                .verification_signature
                .map(|sig| {
                    PrimitiveSignature::from_str(&sig).map_err(|e| {
                        Status::invalid_argument(format!("Invalid verification signature: {e}"))
                    })
                })
                .transpose()?;
            // check if verification signature is provided by aggregator
            if let Some(verification_signature) = verification_signature {
                let address_from_sig =
                    recover_address_from_ecdsa_signature::<OperatorWithdrawalMessage>(
                        withdrawal_id,
                        input_signature,
                        input_outpoint,
                        output_script_pubkey.clone(),
                        output_amount,
                        verification_signature,
                    )?;

                // check if verification signature is signed by the address in config
                if address_from_sig != address_in_config {
                    return Err(BridgeError::InvalidECDSAVerificationSignature).map_to_status();
                }
            } else {
                // if verification signature is not provided, but verification address is set in config, return error
                return Err(BridgeError::ECDSAVerificationSignatureMissing).map_to_status();
            }
        }
```

**File:** core/src/verifier.rs (L1601-1623)
```rust
        // if verification address is set in config, check if verification signature is valid
        if let Some(address_in_config) = self.config.aggregator_verification_address {
            // check if verification signature is provided by aggregator
            if let Some(verification_signature) = verification_signature {
                let address_from_sig =
                    recover_address_from_ecdsa_signature::<OptimisticPayoutMessage>(
                        deposit_id,
                        input_signature,
                        input_outpoint,
                        output_script_pubkey.clone(),
                        output_amount,
                        verification_signature,
                    )?;

                // check if verification signature is signed by the address in config
                if address_from_sig != address_in_config {
                    return Err(BridgeError::InvalidECDSAVerificationSignature);
                }
            } else {
                // if verification signature is not provided, but verification address is set in config, return error
                return Err(BridgeError::ECDSAVerificationSignatureMissing);
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

**File:** core/src/rpc/interceptors.rs (L62-76)
```rust
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

**File:** core/src/config/mod.rs (L134-152)
```rust
    /// Whether client certificates should be restricted to Aggregator and Self certificates.
    ///
    /// Client certificates are always validated against the CA certificate
    /// according to mTLS regardless of this setting.
    pub client_verification: bool,

    /// Path to the aggregator certificate file. (used to authenticate requests from aggregator)
    ///
    /// Aggregator's client cert should be equal to the this certificate.
    pub aggregator_cert_path: PathBuf,

    /// Telemetry configuration
    pub telemetry: Option<TelemetryConfig>,

    /// The ECDSA address of the citrea/aggregator that will sign the withdrawal params
    /// after manual verification of the optimistic payout and operator's withdrawal.
    /// Used for both an extra verification of aggregator's identity and to force citrea
    /// to check withdrawal params manually during some time after launch.
    pub aggregator_verification_address: Option<alloy::primitives::Address>,
```

**File:** core/src/config/mod.rs (L299-303)
```rust
        if matches!(actor_type, cli::Actor::Verifier | cli::Actor::Operator)
            && !self.client_verification
        {
            misconfigs.push("CLIENT_VERIFICATION=false".to_string());
        }
```
