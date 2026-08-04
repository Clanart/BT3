### Title
Self-attributed reputation minting in `handle_get_requests` lets an attacker capture relayer rewards meant for genuine third-party delivery - (File: `modules/pallets/state-coprocessor/src/impls.rs`)

### Summary
The external report's core broken invariant is: a reward that is supposed to be earned by *genuine, costly* participation (real trading activity) is instead minted based on an action an attacker can trigger cheaply and attribute to themselves, with no mechanism verifying that the credited party actually did the work the reward is meant to compensate. The local analog is `pallet-state-coprocessor::handle_get_requests`, which mints `ReputationAsset` to an **attacker-supplied `address` field** rather than to a cryptographically-recovered relayer identity, unlike the sibling `pallet-messaging-incentives` which recovers the payee from a signature.

### Finding Description
`GetRequestsWithProof` carries a free-form `address: Vec<u8>` field described only as "Address that should be credited with fees": [1](#0-0) 

After verifying the source membership proof and the destination state proof (which is legitimate cryptographic verification of the *content*, not of *who delivered it*), the pallet mints reputation tokens directly to whatever account is encoded in that caller-supplied `address`, scaled only by the total response payload size: [2](#0-1) 

Contrast this with the sibling incentive pallet, `pallet-messaging-incentives`, which mints the same `ReputationAsset` but derives the recipient by cryptographically recovering the signer from the message itself, so the minted party is provably the one that produced the signed delivery: [3](#0-2) [4](#0-3) 

`handle_get_requests` has no equivalent binding: the state and membership proofs establish that the *requests and their storage values* are genuine, but nothing establishes that `address` is the account that actually performed the relaying/response-construction work. Any account capable of assembling a valid `GetRequestsWithProof` (i.e., anyone who can obtain the source membership proof and destination state proof — which is public chain data, not a privileged secret) can name itself as `address` and collect the full reputation mint for the batch, exactly as the FTC exploit let an attacker mint the position-increase reward for themselves while performing the round trip alone. There is no requirement that `address` correspond to a distinct, disinterested relayer, and no clawback if the "work" turns out to be self-dealt (e.g., an attacker dispatching cheap or self-controlled `GetRequest`s from a source chain purely to farm the `total_bytes`-proportional mint).

### Impact Explanation
`ReputationAsset` is the same asset pallet-messaging-incentives mints to relayers for genuine message delivery, and reputation feeds relayer standing (used elsewhere in the incentive/collator-manager selection logic). If reputation is a scarce, protocol-weighted signal of "who delivered real cross-chain work" — the same shape as the FTC/TLC share used to distribute pooled rewards — an attacker who can self-attribute mints via `handle_get_requests` can accumulate a disproportionate share of that signal without performing genuine, costly relaying, diluting or displacing the rewards/standing that honest relayers earn. This is a false/unearned reward-accrual bug: the wrong beneficiary (self-declared, not cryptographically proven) receives protocol-minted value, matching the "wrong beneficiary or amount" and "logic attack on reward/bandwidth accounting" categories in the impact gate.

### Likelihood Explanation
Medium. Exploitation requires only assembling a valid `GetRequestsWithProof` (something any unprivileged party with access to state proofs for a supported source/destination pair can do) and naming any address, including one they control, as the fee/reputation-credit target. No relayer key, admin/governance action, or malicious peer is required — the "attacker" is simply an unprivileged submitter of this coprocessor message who mislabels the credited address. This mirrors the C4 finding's low-cost, single-entity extraction pattern: the barrier is legitimate proof construction, not attribution.

### Recommendation
Bind the reputation-credit recipient to a cryptographically verified identity the same way `pallet-messaging-incentives::relayer_for` does — recover the submitter/relayer from a signature over the batch (or the transaction's signed origin) instead of trusting the caller-supplied `address` field in `GetRequestsWithProof`. If `address` must remain caller-suppliable for legacy fee-routing reasons, decouple it from `ReputationAsset` minting entirely, or require it to equal the signed origin of the extrinsic that invokes `handle_get_requests`.

### Proof of Concept
1. Attacker (any unprivileged account, `Alice`) constructs a `GetRequestsWithProof` for a batch of `GetRequest`s whose responses are known/available on the destination chain (they don't need to be the one who originally delivered them cross-chain — the proofs are public chain data at a given height).
2. Alice sets `address = Alice`'s own 32-byte account and submits the extrinsic that invokes `handle_get_requests`.
3. `handle_get_requests` verifies the source membership proof and destination state proof (both succeed since the underlying data is real), computes `total_bytes` from the response sizes, and mints `rate * total_bytes` of `ReputationAsset` straight to Alice: [5](#0-4) 
4. No check exists anywhere in the function verifying that Alice is a bonded/whitelisted relayer or that she performed any delivery work distinguishable from simply calling this extrinsic with a valid proof — unlike `pallet-messaging-incentives`, which requires a verifiable signature to attribute the mint.
5. Repeating this against inexpensive/self-controlled `GetRequest` batches lets Alice accumulate reputation disproportionate to any real relaying cost, capturing value meant to reward the broader relayer set.

### Citations

**File:** modules/pallets/state-coprocessor/src/impls.rs (L42-55)
```rust
/// Message for processing state queries
#[derive(
	Debug, Clone, Encode, Decode, DecodeWithMemTracking, PartialEq, Eq, scale_info::TypeInfo,
)]
pub struct GetRequestsWithProof {
	/// The associated Get requests
	pub requests: Vec<GetRequest>,
	/// Proof of these requests on the source chain
	pub source: Proof,
	/// State proof of the requested values in the Get requests.
	pub response: Proof,
	/// Address that should be credited with fees
	pub address: Vec<u8>,
}
```

**File:** modules/pallets/state-coprocessor/src/impls.rs (L157-186)
```rust
		// Mint reputation tokens to the named relayer. The address is the
		// relayer's raw 32-byte public key as supplied by the coprocessor.
		// A zero rate disables minting and a malformed address simply skips
		// the mint — we don't want a non-32-byte address to fail the whole
		// batch since the response insertion below has no dependency on it.
		// The per-byte rate and reputation asset are inherited from
		// `pallet-messaging-incentives` so both pallets share one source of truth.
		let rate = pallet_messaging_incentives::MintPerByte::<T>::get();
		if !rate.is_zero() && total_bytes > 0 {
			if let Ok(bytes32) = <[u8; 32]>::try_from(address.as_slice()) {
				let relayer: T::AccountId = bytes32.into();
				let bytes_balance: BalanceOf<T> = (total_bytes as u128).saturated_into();
				let amount = rate.saturating_mul(bytes_balance);
				if !amount.is_zero() {
					match <T as pallet_messaging_incentives::Config>::ReputationAsset::mint_into(
						&relayer, amount,
					) {
						Ok(_) => Pallet::<T>::deposit_event(Event::ReputationMinted {
							relayer,
							bytes: total_bytes,
							amount,
						}),
						Err(err) => log::warn!(
							target: "ismp",
							"state-coprocessor: reputation mint failed for {total_bytes}b: {err:?}",
						),
					}
				}
			}
		}
```

**File:** modules/pallets/messaging-incentives/src/lib.rs (L137-153)
```rust
	/// Recover the relayer's account from the sr25519 signature on a
	/// `Message`'s `signer` field. Returns `None` if the message has
	/// no signer (e.g. consensus messages) or the signature is bad.
	fn relayer_for(message: &Message) -> Option<T::AccountId> {
		let (signer, signed) = match message {
			Message::Request(msg) =>
				(&msg.signer, sp_io::hashing::keccak_256(&msg.requests.encode())),
			Message::Response(msg) =>
				(&msg.signer, sp_io::hashing::keccak_256(&msg.requests.encode())),
			_ => return None,
		};
		Signature::decode(&mut &signer[..])
			.ok()?
			.verify_and_get_sr25519_pubkey(&signed, None)
			.ok()
			.map(T::AccountId::from)
	}
```

**File:** modules/pallets/messaging-incentives/src/lib.rs (L160-186)
```rust
	fn on_executed(
		messages: Vec<MessageWithWeight>,
		_events: Vec<IsmpEvent>,
	) -> DispatchResultWithPostInfo {
		let rate = MintPerByte::<T>::get();
		if !rate.is_zero() {
			for mw in &messages {
				let bytes = Self::message_bytes(&mw.message);
				let bytes_balance: BalanceOf<T> = (bytes as u128).saturated_into();
				let amount = rate.saturating_mul(bytes_balance);
				if amount.is_zero() {
					continue;
				}
				if let Some(relayer) = Self::relayer_for(&mw.message) {
					match T::ReputationAsset::mint_into(&relayer, amount) {
						Ok(_) =>
							Self::deposit_event(Event::ReputationMinted { relayer, bytes, amount }),
						Err(err) => log::warn!(
							target: "messaging-incentives",
							"reputation mint failed for {bytes}b: {err:?}",
						),
					}
				}
			}
		}
		Ok(PostDispatchInfo { actual_weight: None, pays_fee: Pays::No })
	}
```
