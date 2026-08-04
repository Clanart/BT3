I found a genuine authority-set misbinding bug in the Tendermint light-client verifier used by `pallet_ismp`'s consensus update path.

### Title
Tendermint light-client update unconditionally promotes stale `next_validators` as the new authenticating validator set, decoupling the stored authority set from the set that actually signed the proven block - (File: modules/consensus/tendermint/verifier/src/verifier.rs)

### Summary
`verify_header_update` correctly determines which validator set (`trusted_state.validators` or `trusted_state.next_validators`) actually produced the signatures on the untrusted header via `extract_validators`, and uses that matched set to check voting power. However, after a successful verification, `create_updated_trusted_state` does **not** reuse that matched set. Instead it unconditionally promotes `old_trusted_state.next_validators` into the new state's `validators` field, regardless of whether the header was actually authenticated by the old current set or the old next set.

### Finding Description
In `extract_validators` [1](#0-0) , the function checks whether `header.validators_hash` matches `trusted_state.validators` (current) or `trusted_state.next_validators` (next), and returns whichever matched as the authenticating set. This returned set is what is actually used to check quorum signatures in `verify_update_header` [2](#0-1) .

After a `Verdict::Success`, `create_updated_trusted_state` is called with only `old_trusted_state` and `consensus_proof` — it does not receive or reuse the matched validator set from `extract_validators`. It instead does:

```rust
// Promote next_validators to validators
let validators = old_trusted_state.next_validators.clone();
``` [3](#0-2) 

This assumes the header was always signed by the previously-announced *next* validator set (i.e., that a rotation just occurred). But `extract_validators` may have matched the header against the OLD *current* set instead (i.e., no rotation occurred for this particular header — the validator set is unchanged). In that case, the new trusted state's `validators` field is incorrectly overwritten with `old_trusted_state.next_validators` — a set that never actually signed the proven block.

### Impact Explanation
Once `old_trusted_state.next_validators` (call it set B) diverges from `old_trusted_state.validators` (set A) — which happens naturally any time a validator rotation is announced via `next_validators_hash` in a prior update — submitting any subsequent consensus proof whose header is still legitimately signed by the unrotated set A causes the trusted state to falsely record B as the authority set that finalized that block, even though B never authenticated it. This breaks the required invariant: "a consensus update must advance only when the exact current or next authority set for that block authenticated it." Once the trusted state's `validators` field is corrupted this way, the light client's future trust decisions are anchored to a validator set with no genuine chain-of-custody link to the finalized chain at that height, which can be leveraged (via subsequent unsigned proof submissions through `pallet_ismp::handle_unsigned`) to accept state/consensus updates that should not be trusted, i.e., false state acceptance that can unlock unauthorized cross-chain execution, withdrawals, or settlement.

### Likelihood Explanation
This does not require a malicious relayer or infrastructure compromise — an unprivileged submitter of `handle_unsigned` messages can supply any consensus proof (signed by the real, unrotated validator set A) once the tracked `next_validators` has diverged from `validators` due to an earlier legitimately-processed rotation announcement. The bug triggers deterministically whenever `extract_validators` selects the *current* branch (not the *next* branch) while `old_trusted_state.next_validators != old_trusted_state.validators`, which is a normal, expected condition on any live Tendermint-based chain with periodic validator rotation.

### Recommendation
`create_updated_trusted_state` should receive (or recompute) the validator set that `extract_validators` actually matched against `header.validators_hash`, and set the new state's `validators` field to that matched set — not blindly promote `old_trusted_state.next_validators`. Only when the matched set is confirmed to be `old_trusted_state.next_validators` (i.e., a genuine rotation occurred for this exact header) should the promotion happen; when the matched set is `old_trusted_state.validators`, the new `validators` field should remain `old_trusted_state.validators`.

### Proof of Concept
1. Start with `trusted_state.validators = A`, `trusted_state.next_validators = B` (B was legitimately validated as the announced-but-not-yet-active next set in a prior update, with `next_validators_hash` pointing at B).
2. Submit a new consensus proof for a header at height H that is still signed by A (no rotation has actually occurred on the real chain; `header.validators_hash == hash(A)`, `header.next_validators_hash == hash(B)` unchanged from before, or empty).
3. `extract_validators` matches the header against `current_set = A`, verification succeeds using A's signatures (correct so far).
4. `create_updated_trusted_state` sets `new_trusted_state.validators = old_trusted_state.next_validators = B`, even though B never signed header H.
5. The stored trusted state now claims B is the authority that finalized block H, though only A did. Subsequent proofs can now be checked against B for state at/after H, decoupling accepted state from the chain's actual finality. [1](#0-0) [4](#0-3)

### Citations

**File:** modules/consensus/tendermint/verifier/src/verifier.rs (L45-69)
```rust
	let validators = extract_validators(&trusted_state, &consensus_proof)?;
	let next_validators = consensus_proof
		.next_validators
		.as_ref()
		.map(|validators| ValidatorSet::new(validators.clone(), None));

	let untrusted_block_state = UntrustedBlockState {
		signed_header: &consensus_proof.signed_header,
		validators: &validators,
		next_validators: next_validators.as_ref(),
	};

	let verifier_options = convert_verification_options(
		&trusted_state.verification_options,
		trusted_state.trusting_period_duration(),
	)?;
	let now = convert_timestamp(current_time)?;

	let verifier = SpIoVerifier::default();
	let result = verifier.verify_update_header(
		untrusted_block_state,
		tendermint_trusted_state,
		&verifier_options,
		now,
	);
```

**File:** modules/consensus/tendermint/verifier/src/verifier.rs (L148-170)
```rust
fn extract_validators<'a>(
	trusted_state: &'a TrustedState,
	consensus_proof: &'a ConsensusProof,
) -> Result<ValidatorSet, VerificationError> {
	let header = &consensus_proof.signed_header.header;
	let current_set = ValidatorSet::new(trusted_state.validators.clone(), None);
	let next_set = ValidatorSet::new(trusted_state.next_validators.clone(), None);

	// Validate current and next validator set hashes using the shared helper
	let current_hash_result =
		validate_validator_set_hash(&current_set, header.validators_hash, false);
	let next_hash_result = validate_validator_set_hash(&next_set, header.validators_hash, true);

	let validators = if current_hash_result.is_ok() {
		current_set
	} else if next_hash_result.is_ok() {
		next_set
	} else {
		return Err(VerificationError::Invalid(format!(
			"Unknown validator set hash: {:?}",
			header.validators_hash
		)));
	};
```

**File:** modules/consensus/tendermint/verifier/src/verifier.rs (L231-262)
```rust
fn create_updated_trusted_state(
	old_trusted_state: &TrustedState,
	consensus_proof: &ConsensusProof,
) -> Result<UpdatedTrustedState, VerificationError> {
	let header = &consensus_proof.signed_header.header;

	// Promote next_validators to validators
	let validators = old_trusted_state.next_validators.clone();

	// Only a signalled rotation may replace the stored next set, and only with a list
	// that hashes to the new signed next_validators_hash. When the interval is unchanged
	// we keep the trusted set and ignore any list the proof happened to carry.
	let old_next_hash = Hash::Sha256(old_trusted_state.next_validators_hash);
	let rotates =
		!header.next_validators_hash.is_empty() && header.next_validators_hash != old_next_hash;
	let next_validators = if rotates {
		let provided = consensus_proof.next_validators.as_ref().ok_or_else(|| {
			VerificationError::ValidatorSetError(
				"next validator set rotated but the proof carried no next validators".to_string(),
			)
		})?;
		ensure_unique_addresses(provided)?;
		validate_validator_set_hash(
			&ValidatorSet::new(provided.clone(), None),
			header.next_validators_hash,
			true,
		)
		.map_err(|e| VerificationError::ValidatorSetError(e.to_string()))?;
		provided.clone()
	} else {
		old_trusted_state.next_validators.clone()
	};
```
