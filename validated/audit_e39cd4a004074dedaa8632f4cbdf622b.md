### Title
`set_identity_keypair` swaps `cluster_info`'s identity even when network-layer key notifiers fail to update, leaving QUIC/TPU components running under a stale key while consensus-facing components already use the new one - ([File: validator/src/admin_rpc_service.rs])

### Summary
This is the closest legitimate Agave analog to the `Staker.setValidator`/`dropBoost` pattern: a state-transition function proceeds to commit the "new" state (assign new validator / set new identity) even though a required cleanup/propagation step on the "old" state can fail, and the failure is only logged, never checked or reverted.

### Finding Description
In `AdminRpcImpl::set_identity_keypair`, before switching the validator's active identity, the code iterates over all registered network-layer key notifiers (QUIC endpoints, connection caches, etc.) and calls `update_key`. The return value is not propagated - failures are only logged with `error!` and swallowed: [1](#0-0) 

Immediately after this loop, regardless of whether any `update_key` call failed, the function unconditionally commits the identity switch by calling `cluster_info.set_keypair(...)` and then notifies the voting/consensus loop of the identity change via `votor_event_sender.send(VotorEvent::SetIdentity)`: [2](#0-1) 

This mirrors the `Staker.setValidator` bug exactly: a pre-transition step (`dropBoost` / `update_key`) that is expected to synchronize old state before the pointer to the "current validator/identity" is overwritten can silently fail, yet the caller does not check the boolean/Result outcome and proceeds to flip the authoritative pointer (`validator = _newValidator` / `cluster_info.set_keypair(...)`) anyway. The corrupted value here is the set of network-layer signing keys held by QUIC/TPU/connection-cache components (registered via the `notifies` map), which can become desynchronized from `cluster_info`'s identity — the value that gossip, repair, and the leader schedule use to determine "who I am."

### Impact Explanation
If a key notifier update fails (e.g. TLS/QUIC endpoint rebuild error in `tls-utils`/`streamer::quic`, or a connection-cache reconfiguration failure), the validator ends up in a split-brain state: `cluster_info.id()` (and therefore leader-schedule/vote/gossip logic) reports the new identity, but the QUIC/TPU transport layer keeps authenticating/dialing peers with the old identity's TLS certificate. This can cause the validator to be unable to receive transactions/repair traffic addressed to its new identity (non-RPC remote effect on TPU/QUIC), or to send votes that peers cannot correctly attribute/authenticate at the transport layer, degrading participation without any error surfaced to the operator (the RPC call still returns `Ok(())`).

### Likelihood Explanation
This does not require a malicious peer or admin: any legitimate `set-identity` operation (a normal validator operational task, e.g. during identity rotation for hot spares) can hit this path if any one of the registered key notifiers fails transiently (I/O error rebuilding QUIC endpoint, cert generation failure, etc.). The failure path is a normal error branch already present in the code (`if let Err(err) = notifier.update_key(...)`), not a crafted edge case, so it is reachable under ordinary operating conditions.

### Recommendation
Track whether any `update_key` call failed and abort the identity switch (return an RPC error) instead of unconditionally calling `cluster_info.set_keypair` and sending `VotorEvent::SetIdentity`. At minimum, if a full abort is undesired, re-attempt or force-retry the failed notifiers before finalizing the swap.

### Proof of Concept
1. Start a validator with the admin RPC service enabled.
2. Arrange for one of the registered `notifies` entries (e.g. the QUIC endpoint key updater) to fail its `update_key` call — this happens on any transient error during endpoint reconfiguration (e.g. certificate/key material rebuild failure), which is a normal error branch already handled in `admin_rpc_service.rs:992-995`.
3. Invoke the `set_identity` (or `set_identity_from_bytes`) admin RPC method.
4. Observe: `set_identity_keypair` logs `"Error updating network layer keypair..."` but still returns `Ok(())`, and `cluster_info.set_keypair` / `VotorEvent::SetIdentity` are dispatched unconditionally at `admin_rpc_service.rs:1007-1017`, leaving the QUIC/TPU layer on the stale identity while gossip/consensus report the new one. [3](#0-2)

### Citations

**File:** validator/src/admin_rpc_service.rs (L945-1021)
```rust
    fn set_identity_keypair(
        meta: AdminRpcRequestMetadata,
        identity_keypair: Keypair,
        require_tower: bool,
        require_vote_history: bool,
    ) -> Result<()> {
        meta.with_post_init(|post_init| {
            if require_tower {
                let _ = Tower::restore(meta.tower_storage.as_ref(), &identity_keypair.pubkey())
                    .map_err(|err| {
                        jsonrpc_core::error::Error::invalid_params(format!(
                            "Unable to load tower file for identity {}: {}",
                            identity_keypair.pubkey(),
                            err
                        ))
                    })?;
            }

            if require_vote_history {
                let should_require_vote_history = {
                    let bank_forks = post_init.bank_forks.read().unwrap();
                    should_require_vote_history_file(
                        &bank_forks.working_bank(),
                        &post_init.vote_account,
                        &identity_keypair.pubkey(),
                    )
                };
                if should_require_vote_history {
                    let _ = VoteHistory::restore(
                        meta.vote_history_storage.as_ref(),
                        &identity_keypair.pubkey(),
                    )
                    .map_err(|err| {
                        jsonrpc_core::error::Error::invalid_params(format!(
                            "Unable to load vote history file for identity {}: {}. The vote \
                             account {} has prior Alpenglow votes. Ensure the vote history file \
                             is present or (dangerous) use --do-not-require-vote-history if you \
                             know what you're doing",
                            identity_keypair.pubkey(),
                            err,
                            post_init.vote_account
                        ))
                    })?;
                }
            }

            for (key, notifier) in &*post_init.notifies.read().unwrap() {
                if let Err(err) = notifier.update_key(&identity_keypair) {
                    error!("Error updating network layer keypair: {err} on {key:?}");
                }
            }

            let old_identity = post_init.cluster_info.id();
            let new_identity = identity_keypair.pubkey();
            solana_metrics::set_host_id(new_identity.to_string());
            // Emit the datapoint after updating metrics to emit the new pubkey
            datapoint_info!(
                "validator-set_identity",
                ("old_id", old_identity.to_string(), String),
                ("new_id", new_identity.to_string(), String),
                ("version", solana_version::version!(), String),
            );
            post_init
                .cluster_info
                .set_keypair(Arc::new(identity_keypair));
            post_init
                .votor_event_sender
                .send(VotorEvent::SetIdentity)
                .map_err(|err| jsonrpc_core::error::Error {
                    code: ErrorCode::InternalError,
                    message: format!("Failed to send SetIdentity event: {err}").to_string(),
                    data: None,
                })?;

            warn!("Identity set to {new_identity}");
            Ok(())
        })
```
