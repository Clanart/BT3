## Analysis

### Step 1: Trace the Unprivileged Entrypoint

The `nonce_gen` RPC handler in `core/src/rpc/verifier.rs` (lines 255–290) accepts a `NonceGenRequest` with `num_nonces` and calls `self.verifier.nonce_gen(num_nonces)` with no per-method authentication check of its own. [1](#0-0) 

### Step 2: Authentication Gate — The Critical Conditional

The verifier gRPC server applies authentication via an interceptor **only when `config.client_verification = true`**. When it is `false`, the `Noop` interceptor is used, meaning any caller — authenticated or not — can reach every RPC method including `nonce_gen`. [2](#0-1) 

The interceptor itself (`OnlyAggregatorAndSelf`) only allows the aggregator or the verifier itself when enabled: [3](#0-2) 

### Step 3: Session Pool Eviction Logic

`nonce_gen` in `core/src/verifier.rs` enforces only a per-call nonce count cap (`NUM_NONCES_LIMIT ≈ 1,136,363`) and zero check. It then unconditionally adds a new session to `AllSessions`: [4](#0-3) 

`AllSessions::add_new_session_with_id` evicts the **oldest** session whenever `MAX_NUM_SESSIONS = 2000` is reached or `MAX_ALL_SESSIONS_BYTES = 2 GB` is exceeded. There is no concept of a "protected" or "in-progress" session: [5](#0-4) 

`remove_oldest_session` pops from the front of the queue with no discrimination: [6](#0-5) 

### Step 4: Legitimate Session Consumption in `deposit_sign`

`deposit_sign` removes the session from the map, uses it for signing, then re-adds it with the same ID for the finalize step. If the session was evicted between removal and re-addition, `remove_session_with_id` returns `"Session not found"`: [7](#0-6) [8](#0-7) 

### Step 5: Rate Limiting Does Not Prevent the Attack

A `RateLimitLayer` is applied, but it is configurable and applies globally, not per-session. With `MAX_NUM_SESSIONS = 2000`, an attacker needs only 2001 requests — achievable over time even under rate limiting. [9](#0-8) 

### Step 6: Constants Confirm Feasibility

`MAX_NUM_SESSIONS = 2000` and `NUM_NONCES_LIMIT ≈ 1,136,363`. An attacker calling `NonceGen` with `num_nonces = 1` (132 bytes each) needs only 2001 calls to trigger eviction of the oldest session. The byte limit (2 GB) is far harder to hit than the session count limit. [10](#0-9) 

---

## Verdict

The attack is **real and exploitable** when `client_verification = false`. The code explicitly supports this mode (it is not a test-only path), and the session eviction logic has no mechanism to protect in-progress deposit sessions from being displaced by unauthenticated callers. This is a code-level design flaw, not merely a config mistake: the `AllSessions` data structure has no concept of session priority or protection, and `nonce_gen` has no caller-identity check of its own.

---

### Title
Unauthenticated `NonceGen` Callers Can Exhaust the Nonce Session Pool and Evict In-Progress Deposit Sessions, Causing Permanent BTC Lock — (`core/src/rpc/verifier.rs`, `core/src/verifier.rs`)

### Summary
When `client_verification = false`, any network-reachable caller can invoke `NonceGen` without authentication. By flooding the verifier's `AllSessions` pool (bounded by `MAX_NUM_SESSIONS = 2000`) with attacker-generated sessions, the oldest legitimate deposit session is evicted via `remove_oldest_session`. A subsequent `deposit_sign` or `deposit_finalize` call referencing the evicted session ID fails with "Session not found", permanently stalling the deposit and locking the bridged BTC.

### Finding Description
`AllSessions::add_new_session_with_id` enforces two limits — `MAX_NUM_SESSIONS` (2000 sessions) and `MAX_ALL_SESSIONS_BYTES` (2 GB) — by evicting the oldest session in FIFO order. There is no distinction between sessions created by the legitimate aggregator for in-progress deposits and sessions created by arbitrary callers. The `nonce_gen` RPC method has no per-method authentication; it relies entirely on the server-level `OnlyAggregatorAndSelf` interceptor, which is only active when `config.client_verification = true`. When that flag is `false` (the `Noop` interceptor is used), any caller can create sessions. With `num_nonces = 1` per call, 2001 calls suffice to evict the oldest legitimate session. The `deposit_sign` flow removes the session from the map, uses it, and re-inserts it; if evicted between those two operations, the re-insertion fails. Even before `deposit_sign` is called, a session created by `NonceGen` for a deposit can be evicted before `deposit_sign` is ever invoked.

### Impact Explanation
A legitimate deposit's nonce session is evicted. `deposit_sign` (or `deposit_finalize`) fails with "Session not found". The deposit cannot be finalized. If the deposit window expires or the aggregator does not retry with a fresh nonce round, the bridged BTC is permanently locked in the deposit UTXO with no recovery path.

### Likelihood Explanation
Exploitability depends on `client_verification = false`, which the codebase explicitly supports as a production configuration mode (not test-only). The attack requires only 2001 sequential RPC calls with minimal payload, is not computationally expensive, and can be executed over time even under rate limiting. No cryptographic material or privileged access is needed.

### Recommendation
1. **Enforce authentication on `nonce_gen` unconditionally** — add a per-method check independent of the server-level interceptor, or require `client_verification = true` in production.
2. **Protect in-progress sessions** — introduce a "pinned" or "active" flag on sessions that are associated with a known deposit outpoint, preventing them from being evicted by the FIFO eviction loop.
3. **Bind sessions to caller identity** — record which authenticated caller created a session and reject `deposit_sign` if the session was not created by the aggregator for the matching deposit.
4. **Enforce `client_verification = true` in production** — document and enforce this as a required security setting, or remove the `Noop` path from production builds.

### Proof of Concept
```rust
// Pseudocode: attacker floods the session pool
let verifier_client = connect_to_verifier_grpc(); // no mTLS needed when client_verification=false

// Step 1: Legitimate aggregator creates a deposit session
let legit_session_id = verifier_client.nonce_gen(NonceGenRequest { num_nonces: 1000 }).await;

// Step 2: Attacker floods with MAX_NUM_SESSIONS + 1 = 2001 sessions
for _ in 0..2001 {
    verifier_client.nonce_gen(NonceGenRequest { num_nonces: 1 }).await;
}

// Step 3: Legitimate deposit_sign fails — legit_session_id was evicted
let result = verifier_client.deposit_sign(VerifierDepositSignParams {
    session_id: legit_session_id,
    ...
}).await;
assert!(result.is_err()); // "Session not found"
// Deposit is now permanently stalled; BTC locked.
```

### Citations

**File:** core/src/rpc/verifier.rs (L255-264)
```rust
    async fn nonce_gen(
        &self,
        req: Request<NonceGenRequest>,
    ) -> Result<Response<Self::NonceGenStream>, Status> {
        let num_nonces = req.into_inner().num_nonces;
        tracing::info!(
            "Verifier nonce gen rpc called with num_nonces: {}",
            num_nonces
        );
        let (session_id, pub_nonces) = self.verifier.nonce_gen(num_nonces).await?;
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

**File:** core/src/servers.rs (L150-153)
```rust
                .layer(RateLimitLayer::new(
                    config.grpc.ratelimit_req_count as u64,
                    Duration::from_secs(config.grpc.ratelimit_req_interval_secs),
                ))
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

**File:** core/src/verifier.rs (L124-158)
```rust
    pub fn add_new_session_with_id(
        &mut self,
        new_nonce_session: NonceSession,
        id: u128,
    ) -> Result<(), eyre::Report> {
        if new_nonce_session.nonces.is_empty() {
            // empty session, return error
            return Err(eyre::eyre!("Empty session attempted to be added"));
        }

        if self.sessions.contains_key(&id) {
            return Err(eyre::eyre!("Nonce session with id {id} already exists"));
        }

        let mut total_needed = Self::session_bytes(&new_nonce_session)?
            .checked_add(self.total_sessions_byte_size()?)
            .ok_or_else(|| eyre::eyre!("Session size calculation overflow in add_new_session"))?;

        loop {
            // check byte size and session count, if session count is already at the limit or byte size is higher than limit
            // we remove the oldest session until the conditions are met
            if total_needed <= MAX_ALL_SESSIONS_BYTES && self.sessions.len() < MAX_NUM_SESSIONS {
                break;
            }
            total_needed = total_needed
                .checked_sub(self.remove_oldest_session()?)
                .ok_or_else(|| eyre::eyre!("Session size calculation overflow"))?;
        }

        // save the session to the HashMap and the session id queue
        self.sessions.insert(id, new_nonce_session);
        self.session_queue.push_back(id);
        self.used_ids.insert(id);
        Ok(())
    }
```

**File:** core/src/verifier.rs (L195-206)
```rust
    fn remove_oldest_session(&mut self) -> Result<usize, eyre::Report> {
        match self.session_queue.pop_front() {
            Some(oldest_id) => {
                let removed_session = self.sessions.remove(&oldest_id);
                match removed_session {
                    Some(session) => Ok(Self::session_bytes(&session)?),
                    None => Ok(0),
                }
            }
            None => Err(eyre::eyre!("No session to remove")),
        }
    }
```

**File:** core/src/verifier.rs (L827-864)
```rust
    pub async fn nonce_gen(
        &self,
        num_nonces: u32,
    ) -> Result<(u128, Vec<PublicNonce>), BridgeError> {
        // reject if too many nonces are requested
        if num_nonces > NUM_NONCES_LIMIT {
            return Err(eyre::eyre!(
                "Number of nonces requested is too high, max allowed is {}, requested: {}",
                NUM_NONCES_LIMIT,
                num_nonces
            )
            .into());
        }
        if num_nonces == 0 {
            return Err(
                eyre::eyre!("Number of nonces requested is 0, cannot generate nonces").into(),
            );
        }
        let (sec_nonces, pub_nonces): (Vec<SecretNonce>, Vec<PublicNonce>) = (0..num_nonces)
            .map(|_| {
                // nonce pair needs keypair and a rng
                let (sec_nonce, pub_nonce) = musig2::nonce_pair(&self.signer.keypair)?;
                Ok((sec_nonce, pub_nonce))
            })
            .collect::<Result<Vec<(SecretNonce, PublicNonce)>, BridgeError>>()?
            .into_iter()
            .unzip();

        let session = NonceSession { nonces: sec_nonces };

        // save the session
        let session_id = {
            let all_sessions = &mut *self.nonces.lock().await;
            all_sessions.add_new_session_with_random_id(session)?
        };

        Ok((session_id, pub_nonces))
    }
```

**File:** core/src/verifier.rs (L899-905)
```rust
        let handle = tokio::spawn(async move {
            // Take the lock and extract the session before entering the async block
            // Extract the session and remove it from the map to release the lock early
            let mut session = {
                let mut session_map = verifier.nonces.lock().await;
                session_map.remove_session_with_id(session_id)?
            };
```

**File:** core/src/verifier.rs (L972-974)
```rust
            let mut session_map = verifier.nonces.lock().await;
            session_map.add_new_session_with_id(session, session_id)?;

```

**File:** core/src/constants.rs (L17-31)
```rust
/// The maximum number of nonces that can be generated in a single nonce generation session.
/// A single nonce takes 132 (musig2 secret nonce) bytes. We calculate NUM_NONCES so that a nonce
/// session takes at maximum 150MB.
pub const NUM_NONCES_LIMIT: u32 = 150 * 1_000_000 / MUSIG_SECNONCE_LEN as u32;

/// The maximum number of bytes that can be used by all nonce sessions.
/// If it exceeds this limit, the verifier will delete the oldest nonce sessions.
/// This limit is approximate, because it doesn't take into account the internal extra bytes used in
/// HashMap and VecDeque used in the AllSessions. It only takes into account bytes used for the secnonces.
pub const MAX_ALL_SESSIONS_BYTES: usize = 2_000_000_000;

/// The maximum number of nonce sessions that can be stored in the verifier.
/// It is used so that the allsessions do not store too many small (1 nonce) sessions.
pub const MAX_NUM_SESSIONS: usize = 2000;

```
