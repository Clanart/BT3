### Title
`InternalEndRound` gRPC Handler Lacks Application-Level Authorization, Allowing Any Network Peer to Force Unauthorized Round State Transitions When `client_verification = false` — (File: `core/src/servers.rs`, `core/src/rpc/operator.rs`)

---

### Summary

When the `client_verification` configuration flag is `false`, the `create_grpc_server` function installs a `Noop` interceptor instead of `OnlyAggregatorAndSelf`, stripping all mTLS-based caller identity checks from every gRPC method on the operator and verifier servers. The `InternalEndRound` handler has no application-level authorization guard of its own (unlike `InternalFinalizedPayout`, which has an explicit `cfg!(test)` gate). Any network-reachable party can therefore call `InternalEndRound` and force the operator to advance its round state machine, spending collateral UTXOs and exhausting kickoff connectors outside of the legitimate automation flow.

---

### Finding Description

**Root cause — `Noop` interceptor when `client_verification = false`**

In `core/src/servers.rs`, `create_grpc_server` branches on `config.client_verification`:

```rust
// core/src/servers.rs lines 114–138
let service = InterceptedService::new(
    service,
    if config.client_verification {
        OnlyAggregatorAndSelf { aggregator_cert, our_cert: client_cert }
    } else {
        Noop          // ← all auth checks disabled
    },
);
```

When `Noop` is active, the interceptor's `call` method unconditionally returns `Ok(req)` for every incoming request, including those whose method name starts with `"Internal"`.

**Missing application-level guard on `InternalEndRound`**

`InternalFinalizedPayout` carries an explicit compile-time gate that blocks production use:

```rust
// core/src/rpc/operator.rs lines 378–382
if !cfg!(test) {
    return Err(Status::permission_denied(
        "This method is only available in tests",
    ));
}
```

`InternalEndRound` has no equivalent guard. Its only protection is the interceptor:

```rust
// core/src/rpc/operator.rs lines 423–448
async fn internal_end_round(
    &self,
    _request: Request<Empty>,
) -> Result<Response<Empty>, Status> {
    tracing::warn!("Internal end round rpc called");
    #[cfg(feature = "automation")]
    {
        let mut dbtx = self.operator.db.begin_transaction().await?;
        self.operator.end_round(&mut dbtx).await?;
        dbtx.commit()...
        Ok(Response::new(Empty {}))
    }
    ...
}
```

`end_round` advances the operator's round index in the database and queues the next round transaction (spending the collateral UTXO chain) via `tx_sender`.

**Interceptor classification logic**

The `is_internal` helper in `core/src/rpc/interceptors.rs` classifies a request as internal by checking whether the `grpc-method` metadata header starts with `"Internal"`. When `Noop` is active this classification is never consulted, so the distinction between public and internal methods is entirely lost.

**Attack path**

1. Operator is deployed with `client_verification = false` (a supported, non-mainnet configuration mode; the mainnet guard in `check_mainnet_requirements` only fires for `Network::Bitcoin`).
2. Server-side TLS is still active, but no client certificate is required — any TLS client can connect.
3. Attacker connects to the operator's TCP gRPC port and calls `/clementine.ClementineOperator/InternalEndRound` with an empty body.
4. `Noop` interceptor passes the request; `internal_end_round` executes `operator.end_round()`.
5. The operator's current round index is incremented in the database and the next round transaction is queued for broadcast, spending the collateral UTXO.
6. Repeated calls exhaust all round slots prematurely.

---

### Impact Explanation

`end_round` advances the operator's collateral UTXO chain (round transactions) and updates the DB round index. Forcing this repeatedly:

- Exhausts all pre-signed kickoff connectors, preventing the operator from processing any further legitimate withdrawals.
- Causes the operator's collateral UTXOs to be spent in an unintended order, potentially making the operator unable to satisfy the reimbursement protocol and exposing its collateral to slashing.
- Breaks bridge liveness for all deposits that depend on this operator's kickoff connectors.

This meets the "unauthorized state transition in round flow that breaks bridge safety/liveness with material fund impact" criterion.

---

### Likelihood Explanation

`client_verification = false` is a supported, documented configuration mode. The `check_mainnet_requirements` guard only enforces `client_verification = true` for `Network::Bitcoin`; testnet4 and regtest deployments are not protected by that check. Any attacker who can reach the operator's gRPC TCP port (no client certificate required) can trigger the issue with a single unauthenticated gRPC call.

---

### Recommendation

Add an application-level caller-identity check inside `internal_end_round` that does not rely solely on the interceptor. The simplest consistent fix is to mirror the pattern used in `InternalFinalizedPayout`:

```rust
async fn internal_end_round(&self, _request: Request<Empty>) -> Result<Response<Empty>, Status> {
    // Verify caller is self (own client cert) regardless of interceptor config
    // OR gate behind cfg!(test) if this method is not needed in production automation
    if !cfg!(feature = "automation") {
        return Err(Status::unimplemented("..."));
    }
    // existing logic
}
```

Alternatively, enforce `client_verification = true` for all non-test deployments (not just mainnet) in `check_general_requirements`, or add a per-handler certificate check that reads the peer cert from the request extensions and validates it against the configured `client_cert_path`.

---

### Proof of Concept

```
# Operator running with client_verification = false on testnet/regtest
# Attacker (no client cert) calls InternalEndRound via grpcurl:

grpcurl \
  -insecure \
  -proto clementine.proto \
  -d '{}' \
  <operator_host>:<port> \
  clementine.ClementineOperator/InternalEndRound

# Expected (correct): UNAUTHENTICATED
# Actual (buggy):     OK — operator advances round, spends collateral UTXO
```

The `Noop` interceptor passes the request unconditionally; `internal_end_round` calls `operator.end_round()`, increments the DB round index, and queues the round transaction for broadcast. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** core/src/rpc/operator.rs (L373-448)
```rust
    #[tracing::instrument(skip(self), err(level = tracing::Level::ERROR), ret(level = tracing::Level::TRACE))]
    async fn internal_finalized_payout(
        &self,
        request: Request<FinalizedPayoutParams>,
    ) -> Result<Response<clementine::Txid>, Status> {
        if !cfg!(test) {
            return Err(Status::permission_denied(
                "This method is only available in tests",
            ));
        }

        tracing::info!(
            "Internal finalized payout rpc called with finalized payout params: {:?}",
            request.get_ref()
        );

        let payout_blockhash: [u8; 32] = request
            .get_ref()
            .payout_blockhash
            .clone()
            .try_into()
            .map_err(|e| {
                Status::invalid_argument(format!(
                    "Failed to convert payout blockhash to [u8; 32]: {e:?}"
                ))
            })?;
        let deposit_outpoint: OutPoint = request
            .get_ref()
            .deposit_outpoint
            .clone()
            .ok_or(Status::invalid_argument("Failed to get deposit outpoint"))?
            .try_into()?;

        let mut dbtx = self.operator.db.begin_transaction().await?;
        let kickoff_txid = self
            .operator
            .handle_finalized_payout(
                &mut dbtx,
                deposit_outpoint,
                BlockHash::from_byte_array(payout_blockhash),
            )
            .await?;
        dbtx.commit()
            .await
            .wrap_err("Failed to commit transaction")
            .map_to_status()?;

        Ok(Response::new(kickoff_txid.into()))
    }

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
