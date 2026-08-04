Based on my investigation, I found a genuine local analog to the report's core issue — code that accepts multiple cryptographic parametrizations (signature "curves"/schemes) without cryptographically binding the claimed identity to the proven identity, exactly the "serialization code doesn't account for the variety it can be called with" pattern from the report.

### Title
Self-declared signer trusted without verification in `decode_receipt_relayer` breaks the recovered-identity binding used by outbound reward claims - (File: `modules/pallets/relayer/src/accumulate.rs`)

### Summary
Hyperbridge defines a generic, unrestricted `Signature` enum (`Evm`, `Sr25519`, `Ed25519`) in `modules/utils/crypto/src/verification.rs` [1](#0-0) , mirroring the report's "parametrization over arbitrary curves" pattern. This type exposes two very different accessors: `.verify()`, which cryptographically recovers/validates a signer, and `.signer()`, which merely returns the **self-declared** address/public-key field with no cryptographic check at all [2](#0-1) . `decode_receipt_relayer`, used by both fee accumulation and the outbound-request reward claim, silently switches between these two semantics depending on the raw byte length of the on-chain receipt value.

### Finding Description
`Pallet::decode_receipt_relayer` decodes the destination chain's `RequestReceipts[commitment]` value into "the delivering relayer's bytes": [3](#0-2) 

For substrate destinations, if the raw bytes are ≤32 bytes they are treated as a plain account id; but if they are longer than 32 bytes, the code decodes them as a full `Signature` and calls `.signer()` — which, for the `Evm` variant, is just `address.clone()`, and for `Sr25519`/`Ed25519` is just `public_key.clone()`. None of these are checked against the enclosed `signature` field at all in this code path; `.signer()` performs no cryptographic verification whatsoever [2](#0-1) .

This value (`delivered_by`) is then used as the trusted "ground truth" identity that the claim's signature is checked against in `process_outbound_request_delivery_claim`: [4](#0-3) 

Note the asymmetry with the sibling `accumulate.rs` beneficiary-redirect path, which correctly forces verification against the known address by passing `Some(delivery_address)` into `.verify()` [5](#0-4) . The outbound-request/consensus claim paths instead call `signature.verify(&msg, None)`, which lets the caller's own embedded public key be used as the basis for the check [6](#0-5) , [7](#0-6) .

Because `.signer()` in `decode_receipt_relayer`'s `len() > 32` branch never checks that the enclosed `signature` bytes are actually valid for the enclosed `address`/`public_key`, any destination-chain-stored value that happens to decode as a `Signature` (rather than a raw ≤32-byte account id) would let the *content of that receipt bytes* — not a cryptographic proof of delivery — dictate who `delivered_by` resolves to. Combined with `signature.verify(&msg, None)` on the claim side (which is satisfied by any self-signed message under an attacker-chosen key), the "recovered == delivered_by" check degenerates into comparing two attacker-influenced values against each other rather than tying the payout to a cryptographically proven relayer identity.

### Impact Explanation
If the `>32`-byte branch of `decode_receipt_relayer` is ever reachable with attacker-influenced receipt bytes (e.g., a future destination-chain encoding change, or any code path where the receipt value can be a serialized `Signature` rather than a bare account id), an attacker could redirect the outbound-request/consensus delivery reward to an arbitrary `payee` without having delivered anything, since neither `.signer()` nor `signature.verify(&msg, None)` binds the payout to a value the attacker does not control. This falls squarely under "wrong beneficiary" / "unauthorized transaction" for reward claims.

### Likelihood Explanation
I could not fully confirm, within the available tool calls, whether pallet-ismp's substrate request-handling path ever writes a `RequestReceipts` value longer than 32 bytes under attacker control (i.e., whether the `>32`-byte `Signature`-decode branch of `decode_receipt_relayer` is reachable today with a value the claimant/attacker influences, as opposed to only ever containing a 32-byte account id written by an honest handler). This is a genuine gap I was unable to close with the remaining budget — I recommend a background agent trace every call site that writes into `RequestReceipts` on substrate destinations (`child_trie.rs::RequestReceipts::insert`) to determine the exact value format and who controls it. The unauthenticated `.signer()` accessor itself, however, is confirmed dead-simple-to-misuse code sitting directly in the reward-payout trust chain, and the asymmetry against the safer `accumulate.rs` pattern is a real, provable design inconsistency.

### Recommendation
- Remove or clearly gate `.signer()` so it can never be used as a trust anchor without a companion `.verify()` call in the same code path.
- In `decode_receipt_relayer`, never derive `delivered_by` from an unauthenticated `.signer()`; if a `Signature` is embedded in the receipt, verify it against the message it was supposed to sign before trusting its `signer()`.
- Change `outbound_request.rs`/`outbound_consensus.rs` to always pass the already-proven identity (`delivered_by`/`evm_address`) as `Some(pubkey)` into `.verify()`, mirroring `accumulate.rs`'s beneficiary-redirect pattern, so the check can never degrade into "attacker's self-declared key equals attacker's self-declared key."

### Proof of Concept
Not fully constructible without confirming the destination-chain write path for `RequestReceipts` values >32 bytes (see Likelihood section). The provable code-level defect is:
1. `decode_receipt_relayer` decodes any >32-byte substrate receipt value as `Signature` and returns `.signer()` with zero cryptographic check [8](#0-7) .
2. `process_outbound_request_delivery_claim` calls `signature.verify(&msg, None)`, which is satisfiable by any self-generated keypair [6](#0-5) , then only checks `recovered == delivered_by` — both values traceable to attacker-influenced inputs if step 1's precondition holds.

### Citations

**File:** modules/utils/crypto/src/verification.rs (L20-30)
```rust
#[derive(
	Debug, Clone, Encode, Decode, DecodeWithMemTracking, scale_info::TypeInfo, PartialEq, Eq,
)]
pub enum Signature {
	/// An Evm Address and signature
	Evm { address: Vec<u8>, signature: Vec<u8> },
	/// An Sr25519 public key and signature
	Sr25519 { public_key: Vec<u8>, signature: Vec<u8> },
	/// An Ed25519 public key and signature
	Ed25519 { public_key: Vec<u8>, signature: Vec<u8> },
}
```

**File:** modules/utils/crypto/src/verification.rs (L53-56)
```rust
			Signature::Sr25519 { signature, public_key } => {
				Self::verify_sr25519(signature, public_key, msg, &public_key_op)?;
				Ok(public_key_op.unwrap_or(public_key.clone()))
			},
```

**File:** modules/utils/crypto/src/verification.rs (L109-115)
```rust
	pub fn signer(&self) -> Vec<u8> {
		match self {
			Signature::Evm { address, .. } => address.clone(),
			Signature::Sr25519 { public_key, .. } => public_key.clone(),
			Signature::Ed25519 { public_key, .. } => public_key.clone(),
		}
	}
```

**File:** modules/pallets/relayer/src/accumulate.rs (L107-126)
```rust
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
```

**File:** modules/pallets/relayer/src/accumulate.rs (L317-351)
```rust
impl<T: Config> Pallet<T> {
	/// Decode a proven `RequestReceipts[commitment]` value into the delivering
	/// relayer's bytes. EVM stores the address RLP encoded, substrate stores the
	/// signer bytes or a signature to recover the signer from. Used by both fee
	/// accumulation and the outbound request delivery claim.
	pub fn decode_receipt_relayer(state_id: StateMachine, raw: &[u8]) -> Result<Vec<u8>, Error<T>> {
		match state_id {
			s if crate::is_pharos(&s) =>
				if raw.len() == 32 {
					Ok(Address::from_slice(&raw[12..]).0.to_vec())
				} else {
					Err(Error::<T>::ProofValidationError)
				},
			s if s.is_evm() => {
				use alloy_rlp::Decodable;
				Ok(Address::decode(&mut &*raw)
					.map_err(|_| Error::<T>::ProofValidationError)?
					.0
					.to_vec())
			},
			s if s.is_substrate() => {
				use codec::Decode;
				let bytes =
					<Vec<u8>>::decode(&mut &*raw).map_err(|_| Error::<T>::ProofValidationError)?;
				Ok(if bytes.len() > 32 {
					Signature::decode(&mut &*bytes)
						.map_err(|_| Error::<T>::SignatureDecodingError)?
						.signer()
				} else {
					bytes
				})
			},
			_ => Err(Error::<T>::MismatchedStateMachine),
		}
	}
```

**File:** modules/pallets/relayer/src/outbound_request.rs (L169-173)
```rust
		let delivered_by = Self::decode_receipt_relayer(destination, &raw)?;

		let msg = outbound_request_delivery_message(commitment, destination, payee);
		let recovered = signature.verify(&msg, None).map_err(|_| Error::<T>::InvalidSignature)?;
		ensure!(recovered == delivered_by, Error::<T>::OutboundRequestSignerMismatch);
```
