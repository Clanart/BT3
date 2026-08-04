## Title
Signature domain confusion between `withdraw()` and `accumulate()` beneficiary-redirect lets a captured relayer signature be replayed across contexts to hijack fee routing and invalidate withdrawals - (File: `modules/pallets/relayer/src/withdrawal.rs`, `modules/pallets/relayer/src/accumulate.rs`)

### Summary
The external report's core defect is a shared, ambiguous accrual/authorization state (`updateAccruedETHPerShares`) that lets an attacker manipulate a lazily-updated per-share accounting value to redirect ETH owed to other SLOT holders. The Hyperbridge local analog is a shared per-`(address, chain)` `Nonce` counter used to authorize two semantically different fund-moving operations (`withdraw` and the `accumulate` beneficiary redirect) whose signed messages hash to byte-identical preimages under certain conditions, so a signature captured for one operation is valid for the other.

### Finding Description
`pallet-relayer` uses one nonce map, `Nonce<T>`, keyed by `(address, StateMachine)`, to prevent replay of two distinct signed actions:

1. `withdraw()` in `modules/pallets/relayer/src/withdrawal.rs` signs over:
```rust
pub fn message(nonce: u64, dest_chain: StateMachine, beneficiary: Option<Vec<u8>>) -> [u8; 32] {
	if let Some(beneficiary) = beneficiary {
		return sp_io::hashing::keccak_256(&(nonce, dest_chain, beneficiary).encode());
	}
	sp_io::hashing::keccak_256(&(nonce, dest_chain).encode())
}
``` [1](#0-0) 

2. `accumulate()`'s beneficiary-redirect path in `modules/pallets/relayer/src/accumulate.rs` signs over:
```rust
pub fn beneficiary_message(
	nonce: u64,
	state_machine: StateMachine,
	beneficiary: &[u8],
) -> [u8; 32] {
	sp_io::hashing::keccak_256(&(nonce, state_machine, beneficiary).encode())
}
``` [2](#0-1) 

When `withdraw()` is called with `beneficiary = Some(b)`, its preimage is `SCALE_encode((nonce: u64, dest_chain: StateMachine, b: Vec<u8>))` [3](#0-2) . `beneficiary_message` always encodes `(nonce: u64, state_machine: StateMachine, b: &[u8])`. SCALE encoding of `Vec<u8>` and `&[u8]` is identical (length-prefixed bytes), so for the same `(nonce, chain, beneficiary)` triple the two functions produce **byte-identical hashes**. There is no domain-separation tag, context discriminant, or distinct hashing scheme distinguishing "authorize a withdrawal to beneficiary B" from "authorize redirecting this delivery batch's freshly accumulated fee to beneficiary B."

Both consumers read and bump the **same** `Nonce<T>` entry:
- `withdraw()`: `let nonce = Nonce::<T>::get(address.clone(), withdrawal_data.dest_chain); ... Nonce::<T>::try_mutate(address.clone(), withdrawal_data.dest_chain, |value| { *value += 1; ... })` [4](#0-3) [5](#0-4) 
- `accumulate()`: `let nonce = Nonce::<T>::get(&delivery_address, state_machine); ... Nonce::<T>::try_mutate(&delivery_address, state_machine, |value| { *value += 1; ... })` [6](#0-5) 

`accumulate()` is dispatched as an **unsigned extrinsic** (per its own module doc, this is deliberate to let relayers submit without gas) and is publicly submittable by anyone who supplies a valid state proof and a valid signature — the pallet only checks that the recovered signer equals the `delivery_address` proven by the destination receipt, not that the submitter is the relayer itself [7](#0-6) . Anyone (including a non-relayer observer) can therefore take a relayer's captured signature and submit it via `accumulate()`.

### Impact Explanation
Because a relayer's `withdraw()` signature (over `(nonce, dest_chain, beneficiary)`) is visible in the mempool/extrinsic data before inclusion, an attacker can:
1. Capture relayer R's pending signed `WithdrawalInputData` with an explicit `beneficiary`.
2. Locate any of R's already-delivered-but-unclaimed batches on the same destination chain (public data — `RequestCommitments` and destination receipts are queryable by anyone) and build a valid `WithdrawalProof`.
3. Submit `accumulate()` first with `beneficiary_details = Some((beneficiary, R's captured signature))`. It passes verification because the hash matches, and `Nonce<T>` for `(R, chain)` is bumped from `N` to `N+1`.

This unauthorizedly consumes R's nonce outside of R's control, causing R's originally-crafted `withdraw()` extrinsic (signed against nonce `N`) to fail with `Error::InvalidSignature`/`InvalidPublicKey` once included, denying R access to their already-accumulated `Fees` balance until they resign a new withdrawal. It also forces routing of the *new* batch's fee to a beneficiary chosen at a time and context the relayer did not intend, ahead of the relayer's own transaction — a transaction-manipulation / unauthorized-execution primitive against the fee-settlement path, matching the "logic attacks" and "replay across contexts" categories called out in the bounty scope. This is directly analogous to the external report's root cause: an ambiguous, insufficiently-scoped shared accounting/authorization primitive (there: `updateAccruedETHPerShares`; here: a single `Nonce` shared across two differently-scoped signed messages) that lets an unprivileged actor manipulate settlement for funds they don't own.

### Likelihood Explanation
No privileged role, relayer collusion, or malicious node is required — any unprivileged party who observes a pending signed withdrawal transaction (mempool-visible) and who can assemble a standard state proof for an already-public delivery receipt can trigger this. `accumulate()` is explicitly unsigned/permissionless by design, so the only barrier is timing (front-running the withdraw extrinsic), which is a normal capability for any chain observer.

### Recommendation
Domain-separate the two signed messages, e.g. by prefixing each hash with a distinct constant/action-tag (`b"WITHDRAW"` vs `b"REDIRECT"`) before encoding, so that no `(nonce, chain, beneficiary)` triple can produce a valid signature for both `withdraw()` and `accumulate()`'s beneficiary path. Additionally, consider using independent nonce namespaces for the two operations rather than sharing `Nonce<T>` across both authorization contexts.

### Proof of Concept
1. Relayer R crafts `WithdrawalInputData { signature: sig_over(message(N, dest_chain, Some(B))), dest_chain, beneficiary: Some(B) }` and broadcasts it to withdraw their accumulated `Fees[dest_chain][R]`.
2. Attacker observes `sig` and `B` in the pending extrinsic (public mempool data).
3. Attacker builds a `WithdrawalProof` for any unclaimed commitment batch where `delivery_address == R` on `state_machine == dest_chain` (publicly derivable from `RequestCommitments` and destination receipts), and sets `beneficiary_details = Some((B, sig))`.
4. Attacker submits `Pallet::accumulate(withdrawal_proof)` (unsigned, no fee) before R's `withdraw()` lands.
5. `beneficiary_message(N, dest_chain, B) == message(N, dest_chain, Some(B))` (byte-identical SCALE encodings), so signature verification in `accumulate()` succeeds against `sig`, `Nonce[R][dest_chain]` becomes `N+1`, and the batch's `total_fee` is credited to `B` via that call instead of R's intended `withdraw()`.
6. R's original `withdraw()` extrinsic, still signed against nonce `N`, now fails `Error::<T>::InvalidSignature`/`InvalidPublicKey` when processed, since `Nonce::<T>::get` returns `N+1`. [8](#0-7) [9](#0-8)

### Citations

**File:** modules/pallets/relayer/src/withdrawal.rs (L81-131)
```rust
	pub fn withdraw(withdrawal_data: WithdrawalInputData) -> DispatchResult {
		let address = match &withdrawal_data.signature {
			Signature::Evm { address, .. } => address.clone(),
			Signature::Sr25519 { public_key, .. } => public_key.clone(),
			Signature::Ed25519 { public_key, .. } => public_key.clone(),
		};

		let nonce = Nonce::<T>::get(address.clone(), withdrawal_data.dest_chain);
		let msg = message(nonce, withdrawal_data.dest_chain, withdrawal_data.beneficiary.clone());

		match &withdrawal_data.signature {
			Signature::Evm { address, .. } => {
				let eth_address = withdrawal_data
					.signature
					.verify(&msg, None)
					.map_err(|_| Error::<T>::InvalidSignature)?;
				if &eth_address != address {
					Err(Error::<T>::InvalidPublicKey)?
				}
			},
			Signature::Sr25519 { .. } => {
				// Verify signature with public key provided in signature enum
				withdrawal_data
					.signature
					.verify(&msg, None)
					.map_err(|_| Error::<T>::InvalidSignature)?;
			},
			Signature::Ed25519 { .. } => {
				// Verify signature with public key provided in signature enum
				withdrawal_data
					.signature
					.verify(&msg, None)
					.map_err(|_| Error::<T>::InvalidSignature)?;
			},
		};
		let available_amount = Fees::<T>::get(withdrawal_data.dest_chain, address.clone());

		if available_amount <
			Self::min_withdrawal_amount(withdrawal_data.dest_chain)
				.unwrap_or(MinWithdrawal::get())
		{
			Err(Error::<T>::NotEnoughBalance)?
		}

		let dispatcher = <T as Config>::IsmpHost::default();

		Nonce::<T>::try_mutate(address.clone(), withdrawal_data.dest_chain, |value| {
			*value += 1;
			Ok::<(), ()>(())
		})
		.map_err(|_| Error::<T>::ErrorCompletingCall)?;
```

**File:** modules/pallets/relayer/src/withdrawal.rs (L190-197)
```rust
/// Signed payload for [`WithdrawalInputData`]. Includes the per-relayer nonce so a captured
/// signature cannot be replayed.
pub fn message(nonce: u64, dest_chain: StateMachine, beneficiary: Option<Vec<u8>>) -> [u8; 32] {
	if let Some(beneficiary) = beneficiary {
		return sp_io::hashing::keccak_256(&(nonce, dest_chain, beneficiary).encode());
	}
	sp_io::hashing::keccak_256(&(nonce, dest_chain).encode())
}
```

**File:** modules/pallets/relayer/src/accumulate.rs (L48-147)
```rust
	pub fn accumulate(mut withdrawal_proof: WithdrawalProof) -> DispatchResult {
		// Reject duplicate commitments within the batch. The wire format is a
		// `Vec` and this extrinsic is unsigned, so this is the line of defence
		// against an attacker padding the batch with identical commitments to
		// double-claim fees.
		let mut seen = alloc::collections::BTreeSet::new();
		for key in withdrawal_proof.commitments.iter() {
			ensure!(seen.insert(key.encode()), Error::<T>::DuplicateCommitment);
		}

		// Filter out already-claimed / missing commitments
		withdrawal_proof.commitments = withdrawal_proof
			.commitments
			.into_iter()
			.filter(|req| match RequestCommitments::<T>::get(*req) {
				Some(leaf_meta) => !leaf_meta.claimed,
				// If request commitment does not exist in storage which should not be
				// possible, we skip it
				None => false,
			})
			.collect();
		ensure!(!withdrawal_proof.commitments.is_empty(), Error::<T>::MissingCommitments);
		let host = <T as Config>::IsmpHost::default();
		let source_sm = validate_state_machine(&host, withdrawal_proof.source_proof.height)
			.map_err(|_| Error::<T>::ProofValidationError)?;
		let dest_sm = validate_state_machine(&host, withdrawal_proof.dest_proof.height)
			.map_err(|_| Error::<T>::ProofValidationError)?;
		let state_machine = withdrawal_proof.source_proof.height.id.state_id;
		let source_keys = Self::source_fee_commitment_keys(
			state_machine,
			&*source_sm,
			&withdrawal_proof.commitments,
		);
		let dest_keys = dest_sm.receipts_state_trie_key(withdrawal_proof.commitments.clone());

		let source_result = Self::verify_withdrawal_proof(
			&*source_sm,
			&withdrawal_proof.source_proof,
			source_keys.clone(),
		)?;
		let dest_result = Self::verify_withdrawal_proof(
			&*dest_sm,
			&withdrawal_proof.dest_proof,
			dest_keys.clone(),
		)?;
		let (result, claimed_commitments) = Self::validate_results(
			&withdrawal_proof,
			source_keys,
			dest_keys,
			source_result,
			dest_result,
		)?;

		let mut entries = result.into_iter();
		let (delivery_address, total_fee) = entries.next().ok_or(Error::<T>::IncompleteProof)?;
		// Every commitment in the batch must share a single delivery address.
		ensure!(entries.next().is_none(), Error::<T>::MixedDeliveryAddressesInBatch);

		// Let's verify the beneficiary address
		let beneficiary_address = if let Some((beneficiary_address, signature)) =
			withdrawal_proof.beneficiary_details
		{
			let nonce = Nonce::<T>::get(&delivery_address, state_machine);
			let msg = beneficiary_message(nonce, state_machine, &beneficiary_address);
			match &signature {
				Signature::Evm { .. } => {
					let eth_address =
						signature.verify(&msg, None).map_err(|_| Error::<T>::InvalidSignature)?;
					if eth_address != delivery_address {
						Err(Error::<T>::InvalidPublicKey)?
					}
				},
				Signature::Sr25519 { .. } | Signature::Ed25519 { .. } => {
					// verify the signature with the delivery address from the state proof
					let _ = signature
						.verify(&msg, Some(delivery_address.clone()))
						.map_err(|_| Error::<T>::InvalidSignature)?;
				},
			}

			Nonce::<T>::try_mutate(&delivery_address, state_machine, |value| {
				*value += 1;
				Ok::<(), ()>(())
			})
			.map_err(|_: ()| Error::<T>::ErrorCompletingCall)?;

			let _ = Fees::<T>::try_mutate(state_machine, beneficiary_address.clone(), |inner| {
				*inner += total_fee;
				Ok::<(), ()>(())
			});

			beneficiary_address
		} else {
			let _ = Fees::<T>::try_mutate(state_machine, delivery_address.clone(), |inner| {
				*inner += total_fee;
				Ok::<(), ()>(())
			});

			delivery_address
		};
```

**File:** modules/pallets/relayer/src/accumulate.rs (L305-315)
```rust
/// Signed payload authorising a beneficiary redirect on a specific source chain.
/// Including the relayer nonce alongside the state machine keeps the signature usable for
/// exactly one accumulate call on that chain, mirroring how `withdraw_fees` binds its signed
/// payload.
pub fn beneficiary_message(
	nonce: u64,
	state_machine: StateMachine,
	beneficiary: &[u8],
) -> [u8; 32] {
	sp_io::hashing::keccak_256(&(nonce, state_machine, beneficiary).encode())
}
```
