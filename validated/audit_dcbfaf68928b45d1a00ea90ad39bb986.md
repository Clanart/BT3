### Title
`client_verification = false` Disables All gRPC mTLS Authentication on Verifier/Operator, Enabling Unauthorized Internal State Transitions — (File: `core/src/servers.rs`, `core/src/rpc/interceptors.rs`, `core/src/config/mod.rs`)

---

### Summary

The `client_verification` configuration flag, when set to `false`, simultaneously disables TLS client-certificate enforcement at the transport layer and replaces the `OnlyAggregatorAndSelf` gRPC interceptor with a `Noop` interceptor that passes every request unconditionally. The only code-level guard against this is `check_mainnet_requirements`, which early-returns `Ok(())` for every non-`Network::Bitcoin` network, leaving any non-mainnet deployment (including testnet4 with real operator collateral) completely unprotected. Any party that can reach the gRPC port can then call privileged internal methods — most critically `InternalEndRound` and `InternalWithdraw` — without possessing any certificate.

---

### Finding Description

**Two-layer authentication collapse when `client_verification = false`**

In `core/src/servers.rs`, `create_grpc_server` builds the TLS configuration and the interceptor together, both gated on the same boolean:

```rust
// core/src/servers.rs  lines 106-138
let tls_config = if config.client_verification {
    ServerTlsConfig::new()
        .identity(server_identity)
        .client_ca_root(client_ca)   // ← requires client cert signed by CA
} else {
    ServerTlsConfig::new().identity(server_identity)  // ← server-only TLS, no client cert
};

let service = InterceptedService::new(
    service,
    if config.client_verification {
        OnlyAggregatorAndSelf { aggregator_cert, our_cert: client_cert }
    } else {
        Noop   // ← passes every request unconditionally
    },
);
``` [1](#0-0) 

The `Noop` interceptor implementation:

```rust
// core/src/rpc/interceptors.rs  lines 22-32
impl Interceptor for Interceptors {
    fn call(&mut self, req: Request<()>) -> Result<Request<()>, Status> {
        match self {
            Interceptors::OnlyAggregatorAndSelf { .. } =>
                only_aggregator_and_self(req, our_cert, aggregator_cert),
            Interceptors::Noop => Ok(req),   // ← no check whatsoever
        }
    }
}
``` [2](#0-1) 

**The only guard is mainnet-only**

`check_mainnet_requirements` returns `Ok(())` immediately for every non-mainnet network:

```rust
// core/src/config/mod.rs  lines 292-303
pub fn check_mainnet_requirements(&self, actor_type: cli::Actor) -> Result<(), BridgeError> {
    if self.protocol_paramset().network != Network::Bitcoin {
        return Ok(());   // ← no check for testnet4, signet, regtest
    }
    // ...
    if matches!(actor_type, cli::Actor::Verifier | cli::Actor::Operator)
        && !self.client_verification
    {
        misconfigs.push("CLIENT_VERIFICATION=false".to_string());
    }
``` [3](#0-2) 

**Exposed internal methods**

With `Noop` active, any TCP-reachable caller can invoke every method on the Operator and Verifier gRPC services, including:

| Method | Proto declaration | Impact |
|---|---|---|
| `InternalEndRound` | `rpc InternalEndRound(Empty) returns (Empty)` | Forces the operator to commit and broadcast the current round transaction, advancing the round state machine |
| `InternalWithdraw` | `rpc InternalWithdraw(WithdrawParams) returns (RawSignedTx)` | Processes a withdrawal **without** the ECDSA `aggregator_verification_address` check that `Withdraw` enforces |
| `InternalFinalizedPayout` | `rpc InternalFinalizedPayout(FinalizedPayoutParams) returns (Txid)` | Marks a payout as finalized and triggers kickoff |
| `InternalCreateAssertCommitmentTxs` | `rpc InternalCreateAssertCommitmentTxs(TransactionRequest)` | Creates and signs assert transactions | [4](#0-3) 

`InternalWithdraw` is documented as "intended for operator's own use" and deliberately omits the aggregator ECDSA signature check present in `Withdraw`:

```rust
// core/src/rpc/operator.rs  lines 168-190
async fn internal_withdraw(&self, request: Request<WithdrawParams>)
    -> Result<Response<RawSignedTx>, Status> {
    // No aggregator_verification_address check here
    let payout_tx = self.operator.withdraw(...).await?;
    Ok(Response::new(RawSignedTx::from(&payout_tx)))
}
``` [5](#0-4) 

Compare with `Withdraw`, which does enforce the ECDSA check when `aggregator_verification_address` is set: [6](#0-5) 

---

### Impact Explanation

**`InternalEndRound` — unauthorized round lifecycle advancement**

`InternalEndRound` calls `operator.end_round()`, which commits a DB transaction and broadcasts the round transaction on-chain. An attacker who can call this repeatedly can force the operator to cycle through all `num_round_txs` rounds before any deposit kickoff has been processed. This exhausts the pre-signed round/kickoff UTXO set for the current deposit session, preventing the operator from initiating legitimate kickoffs and blocking reimbursement. Deposits whose kickoff UTXOs are consumed by a forced round cycle cannot be reimbursed through the normal path, locking bridged BTC in the vault. [7](#0-6) 

**`InternalWithdraw` — ECDSA guard bypass**

When `aggregator_verification_address` is set, `Withdraw` requires a valid ECDSA signature from the aggregator's known address. `InternalWithdraw` skips this check entirely. With `client_verification = false`, any caller can invoke `InternalWithdraw` directly, bypassing the aggregator's manual-verification gate. The remaining guard is the user's Taproot signature over the withdrawal UTXO; an attacker without the user's key cannot forge it, so direct fund theft is not achievable through this path alone. However, the ECDSA guard's purpose — preventing unauthorized withdrawal processing — is fully nullified. [5](#0-4) 

---

### Likelihood Explanation

- `client_verification` is parsed from the environment variable `CLIENT_VERIFICATION`. The env-parsing code treats any value other than `"true"` or `"1"` as `false`:

```rust
// core/src/config/env.rs  line 181-182
let client_verification =
    read_string_from_env("CLIENT_VERIFICATION").is_ok_and(|s| s == "true" || s == "1");
``` [8](#0-7) 

- If the variable is absent or misspelled, `client_verification` silently defaults to `false`, activating `Noop` with no warning.
- `check_mainnet_requirements` is the only enforcement point, and it is a no-op for every non-mainnet network.
- The testnet4 production config has `client_verification = true` today, but the code path is reachable in any deployment where the variable is unset or set incorrectly, and there is no runtime warning logged when `Noop` is selected. [9](#0-8) 

---

### Recommendation

1. **Short term:** Remove the `Noop` branch from production server startup. If `client_verification` is `false` and the network is not a local regtest, refuse to start with a hard error — not just on `Network::Bitcoin`. Extend `check_mainnet_requirements` (or a new `check_security_requirements`) to cover all networks that carry real operator collateral.

2. **Short term:** Log a prominent warning (or error) whenever `Noop` is selected, so operators are not silently running without authentication.

3. **Long term:** Decouple the "disable for local dev" use-case from the production code path. Use a compile-time feature flag (e.g., `#[cfg(feature = "insecure-no-auth")]`) rather than a runtime boolean so that production binaries cannot be started without mTLS regardless of environment variables.

4. **Long term:** Add a separate authorization check inside `InternalEndRound`, `InternalWithdraw`, and `InternalFinalizedPayout` that verifies the caller is the operator itself (e.g., by checking a process-local token or a separate secret), independent of the TLS interceptor, so that defense-in-depth is preserved even if the outer auth layer is misconfigured.

---

### Proof of Concept

**Preconditions:**
- A Verifier or Operator is running with `CLIENT_VERIFICATION` unset (or set to any value other than `"true"`/`"1"`), which is the default when the variable is absent.
- The attacker can reach the gRPC TCP port (e.g., the port is exposed on a non-loopback interface, which is typical for multi-machine deployments).
- Network is non-mainnet (testnet4, signet, or regtest with real operator collateral).

**Steps:**

```python
import grpc
# No client certificate — plain TLS channel
channel = grpc.secure_channel(
    "verifier-host:17001",
    grpc.ssl_channel_credentials(root_certificates=open("ca.pem","rb").read()),
    # No client cert/key provided
)

from clementine_pb2_grpc import ClementineOperatorStub
from clementine_pb2 import Empty

stub = ClementineOperatorStub(channel)

# Call internal method — succeeds because Noop interceptor passes everything
stub.InternalEndRound(Empty())
# Operator's round state machine advances; round tx is broadcast on-chain.
# Repeat to exhaust all pre-signed round slots for the current deposit session.
```

**Expected result with `client_verification = false`:** The call succeeds. The operator broadcasts the round transaction and advances to the next round, consuming a kickoff UTXO slot. Repeated calls exhaust all round slots, preventing legitimate kickoff processing for in-flight deposits.

**Expected result with `client_verification = true`:** The TLS handshake fails (no client cert presented), or the `OnlyAggregatorAndSelf` interceptor rejects the request with `Status::unauthenticated("Unauthorized call to internal method (not self)")`. [10](#0-9) [11](#0-10)

### Citations

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

**File:** core/src/config/mod.rs (L292-303)
```rust
    pub fn check_mainnet_requirements(&self, actor_type: cli::Actor) -> Result<(), BridgeError> {
        if self.protocol_paramset().network != Network::Bitcoin {
            return Ok(());
        }

        let mut misconfigs = Vec::new();

        if matches!(actor_type, cli::Actor::Verifier | cli::Actor::Operator)
            && !self.client_verification
        {
            misconfigs.push("CLIENT_VERIFICATION=false".to_string());
        }
```

**File:** core/src/rpc/clementine.proto (L391-451)
```text
  // registered in Citrea bridge contract. If withdrawal is accepted, the payout
  // tx will be added to the TxSender and success is returned, otherwise an
  // error is returned. If automation is disabled, the withdrawal will not be
  // accepted and an error will be returned. Note: This is intended for
  // operator's own use, so it doesn't include a signature from aggregator.
  rpc InternalWithdraw(WithdrawParams) returns (RawSignedTx) {}

  // First, if verification address in operator's config is set, the signature
  // in rpc is checked to see if it was signed by the verification address. Then
  // prepares a withdrawal if it's profitable and the withdrawal is correct and
  // registered in Citrea bridge contract. If withdrawal is accepted, the payout
  // tx will be added to the TxSender and success is returned, otherwise an
  // error is returned. If automation is disabled, the withdrawal will not be
  // accepted and an error will be returned.
  rpc Withdraw(WithdrawParamsWithSig) returns (RawSignedTx) {}

  // For a given deposit outpoint, determines the next step in the kickoff
  // process the operator is in, and returns the raw signed txs that the
  // operator needs to send next, for enabling reimbursement process without
  // automation.
  //
  // # Parameters
  // - deposit_outpoint: Deposit outpoint to create the kickoff for
  //
  // # Returns
  // - Raw signed txs that the operator needs to send next
  rpc GetReimbursementTxs(Outpoint) returns (SignedTxsWithType) {}

  // Signs all tx's it can according to given transaction type (use it with
  // AllNeededForDeposit to get almost all tx's) Creates the transactions
  // denoted by the deposit and operator_idx, round_idx, and kickoff_idx. It
  // will create the transaction and sign it with the operator's private key
  // and/or saved nofn signatures.
  //
  // # Parameters
  // - deposit_params: User's deposit information
  // - transaction_type: Requested Transaction type
  // - kickoff_id: Operator's kickoff ID
  //
  // # Returns
  // - Raw signed transactions that the entity can sign (no asserts and
  // watchtower challenge)
  //
  // Only used in tests
  rpc InternalCreateSignedTxs(TransactionRequest) returns (SignedTxsWithType) {}

  // Creates all assert transactions (AssertBegin, MiniAsserts, AssertEnd),
  // signs them, and returns the raw txs in the same order. # Parameters
  // - deposit_params: User's deposit information
  // - kickoff_id: Operator's kickoff ID
  // - commit_data: Commitment data for each MiniAssert tx's
  //
  // # Returns
  // - Raw signed assert transactions
  rpc InternalCreateAssertCommitmentTxs(TransactionRequest)
      returns (SignedTxsWithType) {}

  rpc InternalFinalizedPayout(FinalizedPayoutParams) returns (Txid) {}

  rpc InternalEndRound(Empty) returns (Empty) {}

```

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

**File:** core/src/rpc/operator.rs (L423-448)
```rust
    #[tracing::instrument(skip_all, err(level = tracing::Level::ERROR), ret(level = tracing::Level::TRACE))]
    async fn internal_end_round(
        &self,
        _request: Request<Empty>,
    ) -> Result<Response<Empty>, Status> {
        tracing::warn!("Internal end round rpc called");
        #[cfg(feature = "automation")]
        {
            use eyre::Context;

            let mut dbtx = self.operator.db.begin_transaction().await?;

            self.operator.end_round(&mut dbtx).await?;

            dbtx.commit()
                .await
                .wrap_err("Failed to commit transaction")
                .map_to_status()?;
            Ok(Response::new(Empty {}))
        }

        #[cfg(not(feature = "automation"))]
        Err(Status::unimplemented(
            "Automation is not enabled. Operator does not manage its rounds",
        ))
    }
```

**File:** core/src/config/env.rs (L181-182)
```rust
        let client_verification =
            read_string_from_env("CLIENT_VERIFICATION").is_ok_and(|s| s == "true" || s == "1");
```
