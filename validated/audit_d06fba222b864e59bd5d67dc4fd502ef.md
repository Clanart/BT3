### Title
Unauthenticated reward-recipient field lets any caller redirect relayer reputation minting to an arbitrary account - (File: `modules/pallets/state-coprocessor/src/impls.rs`)

### Summary
`Pallet::handle_get_requests` accepts a caller-supplied `address: Vec<u8>` field inside `GetRequestsWithProof` and, after verifying the GET request/response state proofs, mints reputation-asset rewards directly to that address with no check that it corresponds to the entity that actually delivered the proof. This is the same broken invariant as the reported ERC4626i bug: a value that controls who receives a payout/asset movement (there: `owner`/share burn target, here: the reputation mint beneficiary) is trusted from caller input instead of being bound to an authenticated identity.

### Finding Description
`handle_get_requests` performs legitimate, correctly-verified work — source membership proof, destination state proof, dedup/timeout checks — and then, based on that verified work, mints reputation tokens: [1](#0-0) [2](#0-1) 

The comment even documents the trust assumption baked into the code: *"Mint reputation tokens to the named relayer. The address is the relayer's raw 32-byte public key as supplied by the coprocessor."* — but nothing in the function cryptographically ties `address` to whoever actually retrieved/relayed the state proof. It is simply decoded and credited:

```rust
let rate = pallet_messaging_incentives::MintPerByte::<T>::get();
if !rate.is_zero() && total_bytes > 0 {
    if let Ok(bytes32) = <[u8; 32]>::try_from(address.as_slice()) {
        let relayer: T::AccountId = bytes32.into();
        ...
        <T as pallet_messaging_incentives::Config>::ReputationAsset::mint_into(&relayer, amount)
    }
}
```

This stands in direct contrast to the sibling reward path in the same codebase, `pallet-relayer`'s outbound-delivery claim, which deliberately requires a signature recovered from the claim and checked against the relayer address actually proven in the destination receipt before any payout occurs: [3](#0-2) 

`handle_get_requests` has no equivalent step: there is no signature, no receipt-derived relayer identity, and no check that `msg.sender`/origin equals `address`. Any account able to submit a `GetRequestsWithProof` (this class of proof-driven extrinsic in the codebase is invoked unsigned/permissionlessly, validated purely by the embedded state proofs — the same pattern as `pallet-ismp`'s request/response handlers) can set `address` to its own account and collect the reputation mint for proof-verification work regardless of who produced or relayed the underlying `GetResponse` data.

### Impact Explanation
This is a false-beneficiary / unauthorized reward-claim bug in Hyperbridge's messaging-incentives/reputation system: `ReputationAsset::mint_into` moves value (reputation) to a beneficiary chosen entirely by the untrusted caller rather than the entity whose work the reward is meant to compensate. This falls squarely under the bounty's "unauthorized transaction or execution" / "logic attacks" impact categories — funds/assets (reputation tokens, which back real relayer incentive value) move to the wrong beneficiary and in an amount the true delivering party never gets credited for. Repeated calls (each with legitimate proofs the attacker relays only nominally, or even proofs relayed by someone else) let the attacker siphon reputation minted purely based on `total_bytes`, with the `address` field as the sole, unchecked knob determining who gets paid.

### Likelihood Explanation
The proof-verification steps are legitimate and expensive to fake, but that is irrelevant to the attack: no forgery of proofs is needed. An attacker only needs to submit an otherwise-valid `GetRequestsWithProof` (which they can legitimately construct from real, publicly observable state proofs) and simply set `address` to their own 32-byte account before submission. No relayer, prover, admin, or privileged role compromise is required — this is exploitable by any unprivileged party capable of building/submitting the call, matching the "public entrypoint, unprivileged attacker" requirement of the impact gate.

### Recommendation
Do not trust a caller-supplied beneficiary field for reward minting. Either:
- Bind the reward recipient to the transaction's authenticated origin (`ensure_signed(origin)` and use that account instead of an arbitrary `address` field), or
- If the extrinsic must remain unsigned/proof-only, require a signature over the claim (commitment/height/address tuple) recoverable from a value that is itself proven on-chain (mirroring `outbound_request_delivery_claim`'s `signature` vs. `delivered_by` check), so the reward can only be credited to the party cryptographically shown to have done the work.

### Proof of Concept
Conceptual sequence (Rust/pallet-level, mirroring the ERC4626i PoC pattern of "call a fund-moving function with someone else's identity/parameter and receive the payout yourself"):
1. Observe/build a valid `GetRequestsWithProof` — real `GetRequest`s, a valid `source` membership proof, and a valid `response` state proof (these are publicly reconstructable from chain data, no privileged relayer role required).
2. Submit the extrinsic that reaches `Pallet::handle_get_requests` (unsigned/permissionless proof-submission path) with `address` set to the attacker's own 32-byte account instead of the account that actually retrieved the response data.
3. `handle_get_requests` verifies the proofs (succeeds, since they are real), computes `total_bytes`, and mints `rate * total_bytes` of the reputation asset directly to the attacker's `address`: [4](#0-3) 
4. Attacker now holds reputation-asset rewards for work it did not need to have exclusively performed, with no signature or origin check ever validating that `address` belongs to the actual delivering relayer.

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

**File:** modules/pallets/state-coprocessor/src/impls.rs (L156-184)
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
```

**File:** modules/pallets/relayer/src/outbound_request.rs (L169-184)
```rust
		let delivered_by = Self::decode_receipt_relayer(destination, &raw)?;

		let msg = outbound_request_delivery_message(commitment, destination, payee);
		let recovered = signature.verify(&msg, None).map_err(|_| Error::<T>::InvalidSignature)?;
		ensure!(recovered == delivered_by, Error::<T>::OutboundRequestSignerMismatch);

		let treasury: T::AccountId =
			<T as Config>::TreasuryPalletId::get().into_account_truncating();
		let payee_account: T::AccountId = payee.into();
		<<T as pallet_ismp::Config>::Currency as Mutate<T::AccountId>>::transfer(
			&treasury,
			&payee_account,
			reward,
			Preservation::Preserve,
		)
		.map_err(|_| Error::<T>::OutboundRequestRewardTransferFailed)?;
```
