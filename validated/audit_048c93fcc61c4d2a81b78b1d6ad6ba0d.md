### Title
Replayed/duplicate consensus finality proofs unconditionally refresh `consensus_update_time`, defeating the unbonding-period freshness check for GRANDPA-based state machines - (File: `modules/ismp/core/src/handlers/consensus.rs`)

### Summary
The `update_client` handler for ISMP consensus messages stores the new consensus state and, critically, resets `consensus_update_time` unconditionally after every successful `verify_consensus` call, without checking whether the verified state actually changed. The `GrandpaConsensusClient::verify_consensus` path (`verify_grandpa_finality_proof`) never rejects a finality proof whose target block is not strictly newer than the currently trusted height/hash — it only checks hash/justification/ancestry validity. This lets anyone permissionlessly resubmit an old, previously-accepted (and thus publicly known) valid GRANDPA justification to keep "refreshing" the freshness timer that the `is_expired` unbonding check relies on, without any real consensus progress. This mirrors the reported bug class: a state-mutating entry point accepts and processes a no-op/duplicate value update instead of rejecting it, causing unintended side effects on protocol invariants.

### Finding Description
`update_client` in `modules/ismp/core/src/handlers/consensus.rs` does: [1](#0-0) 
It calls `consensus_client.verify_consensus`, then unconditionally does `host.store_consensus_state` and `host.store_consensus_update_time(msg.consensus_state_id, timestamp)`, regardless of whether `new_state` differs from `trusted_state`.

`GrandpaConsensusClient::verify_consensus` decodes and dispatches to `verify_grandpa_finality_proof` / `verify_parachain_headers_with_grandpa_finality_proof`: [2](#0-1) 

The underlying verifier `verify_grandpa_finality_proof` only checks that `target.hash() == finality_proof.block`, that the justification verifies against the *currently stored* authority set, and that an ancestry path exists — it never requires `target.number() > consensus_state.latest_height` or that the new state differs from the trusted one: [3](#0-2) 

Because a previously-submitted, already-justified finality proof (or one for a block that is not newer than the current trusted height, e.g. one from a fork below the current tip that still has valid ancestry) can pass this verification again, `update_client` will happily call `store_consensus_update_time` with `host.timestamp()` again — resetting the freshness clock that `is_expired` depends on: [4](#0-3) 

Contrast this with the EVM path in `HandlerV2.handleConsensus`, which explicitly guards against this exact case by comparing `keccak256(previousState) == keccak256(verifiedState)` and returning *before* the state/time are persisted: [5](#0-4) 

The Substrate `update_client` handler has no equivalent guard, so the "same value" update — updating with a state that is unchanged, or resubmitting an already-processed justified proof — is accepted and treated as a legitimate refresh.

### Impact Explanation
`is_expired` (and by extension the unbonding-period freeze mechanism) is the fail-safe that is supposed to stop a GRANDPA consensus client from being trusted once `host_timestamp - last_update >= unbonding_period`. This is a critical security boundary: it bounds how long a consensus client can remain "live" without genuinely fresh finality progress, which matters for validator-set rotation/slashing assumptions underlying the light client's trust model. If any permissionless account can indefinitely reset `consensus_update_time` merely by rebroadcasting old, already-public justification data (no new authority signatures, no genuine progress), the unbonding-period expiry can never trigger. This keeps a consensus state that should be considered stale/untrustworthy perpetually "fresh," undermining the "false remote state must never become trusted past the trust window" invariant that gates proof/state acceptance for the entire bridge (request/response processing, GET/POST dispatch, state-machine commitments) built on top of that consensus state.

### Likelihood Explanation
The `update_client` handler is a standard, permissionless ISMP message-processing path (any relayer/user can submit a `ConsensusMessage`); this does not require a malicious peer, prover, or admin — only rebroadcasting previously-valid, publicly available proof bytes. The missing "state changed" / "height advanced" check is a straightforward oversight, especially visible when compared directly against the equivalent and correctly-guarded EVM `handleConsensus` implementation in the same codebase, confirming the intended invariant exists elsewhere but was not applied uniformly on the Substrate handler path.

### Recommendation
Add an explicit no-op/staleness guard in `update_client` (`modules/ismp/core/src/handlers/consensus.rs`), mirroring the EVM `HandlerV2.handleConsensus` pattern: after calling `verify_consensus`, compare the newly returned state to `trusted_state` (or otherwise require verified proof to strictly advance height/hash) and skip `store_consensus_state` / `store_consensus_update_time` if no real progress occurred. Additionally, harden `verify_grandpa_finality_proof` to reject finality proofs whose target block is not strictly greater than `consensus_state.latest_height` (or whose hash equals the already-trusted `latest_hash`), consistent with the "stale proof is a no-op" pattern already used in the EVM BEEFY consensus clients (`EcdsaBeefy.sol`, `SP1Beefy.sol`).

### Proof of Concept
1. A relayer submits a valid GRANDPA `ConsensusMessage` finality proof for block `N`; `update_client` calls `verify_consensus`, which succeeds and advances `latest_height` to `N`; `store_consensus_update_time` sets `last_update = t0`.
2. Time passes; no further genuine finality progress happens (e.g., the counterparty's validator set is compromised/stalled), so `t0` should eventually make `is_expired` trip once `unbonding_period` elapses.
3. Before that happens, the same relayer (or anyone) resubmits the *same* previously-broadcast `ConsensusMessage` for block `N` (or any older/non-advancing but justification-valid block reachable via ancestry from the trusted hash).
4. `verify_grandpa_finality_proof` re-validates the justification against the still-current authority set and succeeds (no height/hash-advance check), returning an unchanged/no-progress `ConsensusState`.
5. `update_client` unconditionally calls `host.store_consensus_update_time(consensus_state_id, host.timestamp())`, resetting the freshness clock.
6. Repeating step 3–5 before every `unbonding_period` window elapses keeps `is_expired` from ever returning `Err(UnbondingPeriodElapsed)`, permanently suppressing the intended freeze/expiry safeguard for that consensus client.

### Citations

**File:** modules/ismp/core/src/handlers/consensus.rs (L41-49)
```rust
	let (new_state, intermediate_states) = consensus_client.verify_consensus(
		host,
		msg.consensus_state_id,
		trusted_state,
		msg.consensus_proof,
	)?;
	host.store_consensus_state(msg.consensus_state_id, new_state)?;
	let timestamp = host.timestamp();
	host.store_consensus_update_time(msg.consensus_state_id, timestamp)?;
```

**File:** modules/ismp/clients/grandpa/src/consensus.rs (L69-103)
```rust
	fn verify_consensus(
		&self,
		_host: &dyn IsmpHost,
		consensus_state_id: ConsensusStateId,
		trusted_consensus_state: Vec<u8>,
		proof: Vec<u8>,
	) -> Result<(Vec<u8>, VerifiedCommitments), Error> {
		// decode the proof into consensus message
		let consensus_message: ConsensusMessage = codec::Decode::decode(&mut &proof[..])
			.map_err(|e| GrandpaError::DecodeConsensusMessage(format!("{e:?}")))?;

		// decode the consensus state
		let consensus_state: ConsensusState =
			codec::Decode::decode(&mut &trusted_consensus_state[..])
				.map_err(|e| GrandpaError::DecodeConsensusState(format!("{e:?}")))?;

		// Reject before any arm runs; see `envelope_matches_state_machine`.
		if !envelope_matches_state_machine(&consensus_state.state_machine, &consensus_message) {
			Err(GrandpaError::ConsensusMessageStateMachineMismatch(
				consensus_state.state_machine,
			))?
		}

		let mut intermediates = BTreeMap::new();

		// match over the message
		match consensus_message {
			ConsensusMessage::Polkadot(relay_chain_message) => {
				let headers_with_finality_proof = ParachainHeadersWithFinalityProof {
					finality_proof: relay_chain_message.finality_proof,
					parachain_headers: relay_chain_message.parachain_headers,
				};

				let (consensus_state, parachain_headers) =
					verify_parachain_headers_with_grandpa_finality_proof(
```

**File:** modules/consensus/grandpa/verifier/src/lib.rs (L44-103)
```rust
pub fn verify_grandpa_finality_proof<H>(
	mut consensus_state: ConsensusState,
	finality_proof: FinalityProof<H>,
) -> Result<(ConsensusState, H, Vec<H256>, AncestryChain<H>), Error>
where
	H: Header<Hash = H256, Number = u32>,
	H::Number: finality_grandpa::BlockNumberOps + Into<u32>,
{
	// First validate unknown headers.
	let headers = AncestryChain::<H>::new(&finality_proof.unknown_headers);

	let target = finality_proof
		.unknown_headers
		.iter()
		.max_by_key(|h| *h.number())
		.ok_or(Error::UnknownHeadersEmpty)?;

	// this is illegal
	if target.hash() != finality_proof.block {
		Err(Error::LatestBlockMismatch)?;
	}

	let justification =
		GrandpaJustification::<H>::decode_all(&mut &finality_proof.justification[..])
			.map_err(|e| Error::DecodeJustification(alloc::format!("{e:?}")))?;

	if justification.commit.target_hash != finality_proof.block {
		Err(Error::JustificationTargetMismatch)?;
	}

	let from = consensus_state.latest_hash;

	let base = finality_proof
		.unknown_headers
		.iter()
		.min_by_key(|h| *h.number())
		.ok_or(Error::UnknownHeadersEmpty)?;

	if base.number() < &consensus_state.latest_height {
		headers
			.ancestry(base.hash(), consensus_state.latest_hash)
			.map_err(|_| Error::InvalidAncestry)?;
	}

	let finalized = headers.ancestry(from, target.hash()).map_err(|_| Error::InvalidAncestry)?;

	// 2. verify justification.
	justification
		.verify(consensus_state.current_set_id, &consensus_state.current_authorities)
		.map_err(|e| Error::JustificationVerify(e.to_string()))?;

	// Sets new consensus state, optionally rotating authorities
	consensus_state.latest_hash = target.hash();
	consensus_state.latest_height = (*target.number()).into();
	if let Some(scheduled_change) = find_scheduled_change::<H>(&target) {
		consensus_state.current_set_id += 1;
		consensus_state.current_authorities = scheduled_change.next_authorities;
	}

	Ok((consensus_state, target.clone(), finalized, headers))
```

**File:** modules/ismp/core/src/host.rs (L208-220)
```rust
	/// Check if the client has expired since the last update
	fn is_expired(&self, consensus_state_id: ConsensusStateId) -> Result<(), Error> {
		let host_timestamp = self.timestamp();
		let unbonding_period = self
			.unbonding_period(consensus_state_id)
			.ok_or(Error::UnnbondingPeriodNotConfigured { consensus_state_id })?;
		let last_update = self.consensus_update_time(consensus_state_id)?;
		if host_timestamp.saturating_sub(last_update) >= unbonding_period {
			Err(Error::UnbondingPeriodElapsed { consensus_state_id })?
		}

		Ok(())
	}
```

**File:** evm/src/core/HandlerV2.sol (L144-153)
```text
    function handleConsensus(IHost host, bytes calldata proof) external notFrozen(host) {
        uint256 delay = block.timestamp - host.consensusUpdateTime();
        if (delay >= host.unStakingPeriod()) revert ConsensusClientExpired();

        bytes memory previousState = host.consensusState();
        (bytes memory verifiedState, IntermediateState[] memory intermediates, uint256 nextAuthoritySetId) =
            IConsensusV2(host.consensusClient()).verify(previousState, proof);

        if (keccak256(previousState) == keccak256(verifiedState)) return;
        host.storeConsensusState(verifiedState);
```
