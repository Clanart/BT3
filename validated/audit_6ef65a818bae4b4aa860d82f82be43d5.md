### Title
Governance `set_epoch_length` update invalidates cached BSC rotation state without recomputation, enabling authority-set promotion under stale epoch math - (File: `modules/ismp/clients/bsc/src/lib.rs`)

### Summary
`BscClient::verify_consensus` stages a pending authority set (`ConsensusState.next_validators`) with a cached absolute `rotation_block`, computed under whatever `EpochLength` was in effect at staging time [1](#0-0) . Every subsequent verification call re-derives the *epoch* of that cached block using the **current** `EpochLength` value read fresh from `pallet_ismp_bsc::Pallet::<T>::epoch_length()` [2](#0-1) . `EpochLength` is a plain governance-settable `StorageValue` with no linkage to any in-flight rotation [3](#0-2) . If it changes while a rotation is staged, the promotion check (`attested_epoch == rotation_epoch`) is evaluated with mismatched semantics: the numerator (`rotation_block`) reflects the old epoch length, the modulus (`epoch_length`) reflects the new one — exactly the "stale cached parameter, no revalidation" pattern from the external report.

### Finding Description
`verify_consensus` is reached via the fully permissionless `handleConsensus`/`update_client` entrypoint (any relayer can submit a `BscClientUpdate`) [4](#0-3) . On each call it:

1. Reads the *current* `epoch_length` from governance-controlled storage.
2. Uses it to recompute `attested_epoch` and `rotation_epoch` against the previously-cached `next_validators.rotation_block`.
3. Promotes `next_validators` → `current_validators` and sets `current_epoch = attested_epoch` when the two match. [5](#0-4) 

`rotation_block` is a raw BSC block number computed once, at staging time, as `epoch_header.number + validator_size/2` under the epoch length that was active then [6](#0-5) . It is never recomputed, and nothing invalidates or re-derives it if `EpochLength` changes before the rotation is enacted. `set_epoch_length` only overwrites the storage value (and optionally resets the consensus state, but this is optional and not required) — it performs no check for, and no reset of, an in-flight `next_validators` staging [7](#0-6) .

Because `compute_epoch` is integer division (`number / epoch_length`) [8](#0-7) , changing the divisor after `rotation_block` was fixed shifts which epoch that block number is deemed to belong to. This desynchronizes the promotion guard from the guard it was specifically hardened against: the code comment for this very check states the fix was to bind rotation strictly to "the recorded `rotation_block`'s epoch" specifically to stop an attacker from promoting a stale/retired validator set in an unintended epoch [9](#0-8) . An `EpochLength` change silently reopens exactly that class of attack: the epoch binding is now computed with different arithmetic than the one used when `rotation_block` was recorded, so a relayer can select/submit an `attested_header` whose epoch — under the *new* `epoch_length` — collides with `rotation_epoch` even though it would not have under the length in effect when the rotation was staged (or vice-versa, suppressing an intended promotion). The same stale `epoch_length` mismatch also feeds `ensure_finalized_epoch_consistent`, the guard explicitly built to prevent `finalized_height` from running ahead of the validator set the client holds and permanently stranding/desyncing the client [10](#0-9) ; corrupting the epoch math undermines that guard as well.

The corrupted value is `ConsensusState.next_validators.rotation_block`'s *interpreted epoch* — it is treated as still meaningful under the current `epoch_length`, but its numeric value encodes the semantics of the length at staging time, and no code path revalidates or refreshes it when governance updates the parameter it depends on.

### Impact Explanation
BSC consensus state feeds directly into which `state_root` Hyperbridge accepts as the trusted BSC state commitment for that height [11](#0-10) . Every downstream proof-based operation (request/response delivery via `handlePostRequests`/`handleGetResponses`, non-membership timeout proofs, outbound reward claims verified against destination state) trusts this commitment. If the epoch-boundary desync lets an unintended validator set get promoted to `current_validators` (e.g., a retired set whose keys may no longer be trustworthy, or promotion at the wrong height), a subsequently BLS-signed-but-illegitimate header could be accepted as a genuine BSC state commitment — i.e., false state acceptance, which can be leveraged to forge withdrawal/state proofs and misappropriate bridged funds or relayer/claim rewards.

### Likelihood Explanation
`set_epoch_length` is not a hypothetical lever — BSC has historically changed its epoch length via network hard forks, so governance updating this parameter mid-operation is an expected, benign maintenance action, not an attacker-controlled event. The promotion check itself is evaluated on every single `update_client` call from any permissionless relayer, so the window between an epoch-length change and the next enactment attempt is the only precondition, and the attacker action (choosing which attested header to submit) requires no special privilege.

### Recommendation
Snapshot the `epoch_length` used to compute `rotation_block` alongside `NextValidators` (mirroring the report's "governance state versioning" recommendation), and use that snapshotted value — not the live `Pallet::<T>::epoch_length()` — when evaluating `rotation_epoch`/`attested_epoch` for promotion and in `ensure_finalized_epoch_consistent`. Alternatively, have `set_epoch_length` refuse to change the parameter (or force-clear `next_validators`/require a fresh sync) whenever a rotation is currently staged for any tracked consensus state, so no in-flight rotation can ever be evaluated under mismatched epoch arithmetic.

### Proof of Concept
1. BSC consensus client stages a rotation: a sync update sets `next_validators = { validators: V2, rotation_block: R }`, with `R` derived under `EpochLength = L1` (e.g. `L1 = 200`).
2. Governance legitimately calls `pallet_ismp_bsc::set_epoch_length` to change `EpochLength` to `L2` (e.g. `L2 = 500`, mirroring a real BSC hard-fork parameter change) — a benign operational action, not an attack.
3. Before any further update lands, `compute_epoch(R, L1) != compute_epoch(R, L2)` in general (integer division changes bucket boundaries). An unprivileged relayer now submits a `BscClientUpdate` whose `attested_header.number` satisfies `compute_epoch(attested_number, L2) == compute_epoch(R, L2)` even though this equality would not have held under `L1`.
4. `verify_consensus` promotes `V2` to `current_validators` and sets `current_epoch = attested_epoch` computed under `L2` [12](#0-11) , at a point in the chain's real history that does not correspond to the actual on-chain BSC rotation boundary.
5. Because `ensure_finalized_epoch_consistent` is evaluated with the same now-inconsistent `epoch_length`, its staleness guard can be satisfied even though the client's validator-set view no longer corresponds to the real chain state, allowing a subsequently crafted/aligned header's `state_root` to be stored as the trusted BSC commitment used by all downstream ISMP proof verification.

### Citations

**File:** modules/consensus/bsc/verifier/src/lib.rs (L166-205)
```rust
            let epoch_header = update.epoch_header_ancestry[0].clone();
            let epoch_header_extra_data = parse_extra::<H, C>(&epoch_header)
                .map_err(|_| Error::ParseEpochExtraData)?;
            let validators = epoch_header_extra_data
                .validators
                .into_iter()
                .map(|val| val.bls_public_key.as_slice().try_into().expect("Infallible"))
                .collect::<Vec<BlsPublicKey>>();

            if !validators.is_empty() {
                Some(NextValidators {
                    validators,
                    rotation_block: epoch_header.number.low_u64() +
                        (current_validators.len() as u64 / 2),
                })
            } else {
                Err(Error::MissingValidatorSet)?
            }
            // If the source header that was finalized is the epoch header we extract the next validator set
        } else if update.source_header.number.low_u64() % epoch_length == 0 {
            let epoch_header_extra_data = parse_extra::<H, C>(&update.source_header)
                .map_err(|_| Error::ParseEpochExtraData)?;
            let validators = epoch_header_extra_data
                .validators
                .into_iter()
                .map(|val| val.bls_public_key.as_slice().try_into().expect("Infallible"))
                .collect::<Vec<BlsPublicKey>>();

            if !validators.is_empty() {
                Some(NextValidators {
                    validators,
                    rotation_block: update.source_header.number.low_u64() +
                        (current_validators.len() as u64 / 2),
                })
            } else {
                Err(Error::MissingValidatorSet)?
            }
        } else {
            None
        };
```

**File:** modules/consensus/bsc/verifier/src/lib.rs (L214-244)
```rust
/// Enforce that `finalized_height` never runs ahead of the validator set the client holds.
///
/// A BSC epoch-N validator set signs for `current_epoch` and remains valid into the first half of
/// `current_epoch + 1` (until the mid-epoch rotation block), so an update finalizing a header early
/// in the next epoch verifies fine against it. But the client may only *rely* on that overlap once
/// the next set has been staged (`next_validators_staged`) so the rotation can subsequently be
/// enacted. An update that finalizes a header in a new epoch without staging that rotation leaves
/// `current_epoch`/`current_validators` a full epoch behind `finalized_height`; the relayer derives
/// its sync target from `max(epoch(finalized_height), current_epoch)`, so it would skip the
/// un-staged epoch forever and the client would be permanently stuck (also a griefing vector).
///
/// The finalized height may therefore cross an epoch boundary only via a validator-set-staging
/// (sync) update, and by at most one epoch: `epoch(finalized_height) <= current_epoch +
/// next_validators_staged`.
pub fn ensure_finalized_epoch_consistent(
	finalized_height: u64,
	current_epoch: u64,
	next_validators_staged: bool,
	epoch_length: u64,
) -> Result<(), Error> {
	let finalized_epoch = compute_epoch(finalized_height, epoch_length);
	let max_finalized_epoch = current_epoch + next_validators_staged as u64;
	if finalized_epoch > max_finalized_epoch {
		return Err(Error::StaleValidatorSet {
			finalized_epoch,
			current_epoch,
			next_validators_staged,
		});
	}
	Ok(())
}
```

**File:** modules/ismp/clients/bsc/src/lib.rs (L95-124)
```rust
		let epoch_length = Pallet::<T>::epoch_length().ok_or(Error::EpochLengthNotSet)?;
		if let Some(next_validators) = consensus_state.next_validators.clone() {
			let attested_number = bsc_client_update.attested_header.number.low_u64();
			let attested_epoch = compute_epoch(attested_number, epoch_length);
			let rotation_epoch = compute_epoch(next_validators.rotation_block, epoch_length);
			// Promote the pending validator set only when the submitted update is in the
			// specific epoch where that set is scheduled to activate, and the attested
			// header has reached the recorded `rotation_block`. The previous rule —
			// "any update whose `attested.number % epoch_length` is past the rotation
			// midpoint" — promoted the pending set in any later epoch, so an attacker
			// holding the keys of a stale `next_validators` (e.g. retired or compromised
			// validators) could submit an update many epochs later, get their set
			// promoted to `current_validators`, and then have their forged
			// `source_header`'s `state_root` accepted as a BSC state commitment. Binding
			// rotation to the recorded `rotation_block`'s epoch prevents that reuse.
			if attested_epoch == rotation_epoch && attested_number >= next_validators.rotation_block {
				// During authority set rotation, the source header must be from the same epoch as
				// the attested header.
				let source_header_epoch =
					compute_epoch(bsc_client_update.source_header.number.low_u64(), epoch_length);
				if source_header_epoch != attested_epoch {
					Err(Error::SourceHeaderEpochMismatch {
						attested_epoch,
						source_epoch: source_header_epoch,
					})?
				}
				consensus_state.current_validators = next_validators.validators;
				consensus_state.next_validators = None;
				consensus_state.current_epoch = attested_epoch;
			}
```

**File:** modules/ismp/clients/bsc/src/lib.rs (L127-150)
```rust
		let VerificationResult { hash, finalized_header, next_validators } =
			verify_bsc_header::<H, C>(
				&consensus_state.current_validators,
				bsc_client_update,
				epoch_length,
			)?;

		let mut state_machine_map: BTreeMap<StateMachineId, Vec<StateCommitmentHeight>> =
			BTreeMap::new();

		let state_commitment = StateCommitmentHeight {
			commitment: StateCommitment {
				timestamp: finalized_header.timestamp,
				overlay_root: None,
				state_root: finalized_header.state_root,
			},
			height: finalized_header.number.low_u64(),
		};
		consensus_state.finalized_hash = hash;

		if let Some(next_validators) = next_validators {
			consensus_state.next_validators = Some(next_validators);
		}
		consensus_state.finalized_height = finalized_header.number.low_u64();
```

**File:** modules/ismp/clients/bsc/src/pallet.rs (L53-94)
```rust
	/// BSC Epoch length
	#[pallet::storage]
	#[pallet::getter(fn epoch_length)]
	pub type EpochLength<T: Config> = StorageValue<_, u64, OptionQuery>;

	#[derive(
		Clone,
		codec::Encode,
		codec::Decode,
		DecodeWithMemTracking,
		scale_info::TypeInfo,
		PartialEq,
		Eq,
		Debug,
	)]
	pub struct UpdateParams {
		pub epoch_length: u64,
		pub consensus_state: Option<Vec<u8>>,
		pub consensus_state_id: Option<ConsensusStateId>,
	}

	#[pallet::call]
	impl<T: Config> Pallet<T> {
		/// Sets the new BSC epoch length and resets the consensus state
		#[pallet::call_index(0)]
		#[pallet::weight(<T as frame_system::Config>::DbWeight::get().reads_writes(1, 3))]
		pub fn set_epoch_length(origin: OriginFor<T>, params: UpdateParams) -> DispatchResult {
			<T as Config>::AdminOrigin::ensure_origin(origin)?;
			let host = <T as Config>::IsmpHost::default();
			EpochLength::<T>::put(params.epoch_length);
			if let Some((consensus_state_id, consensus_state)) = params
				.consensus_state_id
				.and_then(|id| params.consensus_state.map(|state| (id, state)))
			{
				host.store_consensus_state(consensus_state_id, consensus_state)
					.map_err(|_| Error::<T>::ErrorStoringConsensusState)?;
			}

			Self::deposit_event(Event::<T>::NewEpochLength { epoch_length: params.epoch_length });

			Ok(())
		}
```

**File:** modules/ismp/core/src/handlers/consensus.rs (L29-49)
```rust
pub fn update_client<H>(host: &H, msg: ConsensusMessage) -> Result<MessageResult, anyhow::Error>
where
	H: IsmpHost,
{
	let consensus_client_id = host.consensus_client_id(msg.consensus_state_id).ok_or(
		Error::ConsensusStateIdNotRecognized { consensus_state_id: msg.consensus_state_id },
	)?;
	let consensus_client = host.consensus_client(consensus_client_id)?;
	let trusted_state = host.consensus_state(msg.consensus_state_id)?;
	host.is_consensus_client_frozen(msg.consensus_state_id)?;
	host.is_expired(msg.consensus_state_id)?;

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

**File:** modules/consensus/bsc/verifier/src/primitives.rs (L206-209)
```rust
pub fn compute_epoch(number: u64, epoch_length: u64) -> u64 {
	number / epoch_length
}

```
