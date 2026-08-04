Based on my investigation, I found a genuine local analog of the reported bug class, though I want to be precise about its scope and limits.

### Title
Length-heuristic decoding of `RequestReceipts` relayer bytes lets an ambiguous 32-byte value bypass the signature-recovery check for beneficiary redirect — ([File: modules/pallets/relayer/src/accumulate.rs])

### Summary
`decode_receipt_relayer` (shared by `accumulate()` and `process_outbound_request_delivery_claim`) decides, purely from the *byte length* of a proven storage value, whether the substrate destination stored a raw relayer-account id or a `Signature` enum to recover a signer from: [1](#0-0) 

### Finding Description
`store_request_receipt(&self, req: &Request, signer: &Vec<u8>)` on the substrate ISMP host stores whatever `signer` bytes the handler passes in (typically the account id of whoever submitted the `RequestMessage`), SCALE-encoded as a `Vec<u8>`: [2](#0-1) 

`decode_receipt_relayer` then re-interprets that raw value using a length threshold, `bytes.len() > 32`, to decide whether to treat it as a plain account identifier or as a `Signature` enum from which a signer is *recovered*: [3](#0-2) 

This is the same bug class as the BLS report: a parser branches on the *length* of untrusted/self-controlled bytes rather than an explicit, unambiguous type tag or fixed schema, so the meaning of the payload ("this is a signer identity" vs "this is something to cryptographically verify") is inferred rather than authenticated. In the BLS case this let extra padding bytes silently change what was verified; here, whichever code path stores the relayer identifier in `RequestReceipts` determines whether downstream logic treats a 32-byte value as an already-authenticated account id or requires signature recovery — and both fee-accumulation (`accumulate`) and the newer `claim_outbound_request_delivery_reward` treat *any* substrate `RequestReceipts` value ≤32 bytes as already-authenticated, no signature needed.

Concretely: `accumulate()`'s beneficiary-redirect branch and `process_outbound_request_delivery_claim` both call `decode_receipt_relayer` to get `delivered_by`/`delivery_address`, then separately verify a caller-supplied `Signature` against a message and check the recovered signer equals `delivered_by`: [4](#0-3) [5](#0-4) 

The security of both flows rests entirely on the assumption that `RequestReceipts[commitment]` for substrate destinations is *always* ≤32 raw bytes identifying the real relayer, and never something an unprivileged party gets to shape into a ≤32-byte value that decodes cleanly under a different interpretation. I could not find, within the indexed portion of the codebase, the concrete substrate handler call site that sets `msg.signer` when a `RequestMessage`/`ResponseMessage` is submitted (i.e., whether `signer` is cryptographically tied to the submitting account or is an attacker-suppliable field in the message). Without that call site, I cannot conclusively demonstrate that an unprivileged relayer can inject an alternate ≤32-byte value that bypasses `Signature::decode` and is credited as `delivered_by`/`delivery_address` without ever having proven possession of a matching private key.

### Impact Explanation
If the `signer: &Vec<u8>` value stored via `store_request_receipt`/`store_response_receipt` is attacker-influenced at the point of message delivery (e.g., taken from `RequestMessage.signer`, which the ISMP docs describe only as "Signer information. Ideally should be their account identifier," with no stated on-chain proof of key possession at delivery time), then:
- `accumulate()` would credit fees to `delivery_address` bytes chosen by the delivering relayer without ever validating a real signature over those bytes at delivery time — the pallet only demands a signature later, for the *optional* beneficiary redirect, and even then only checks that a recovered signer equals whatever `delivery_address` was decoded, which is circular if `delivery_address` itself was attacker-chosen.
- `process_outbound_request_delivery_claim` has an explicit signature check against `delivered_by`, which is stronger — but it still relies on `delivered_by` being the *actual* deliverer identity rather than an arbitrary short byte string the relayer set.

This maps to the bounty's "wrong beneficiary or amount" / "unauthenticated message flow" impact category if the receipt's `signer` field is not independently authenticated at write time.

### Likelihood Explanation
I rate this **uncertain/low confidence** rather than confirmed, because I was unable to locate and read the exact substrate message-handling code path (`modules/ismp/core/src/handlers/request.rs` or equivalent) that determines how `msg.signer` is derived when a `RequestMessage` is submitted to a substrate destination. If that value is cryptographically bound to the submitting extrinsic's origin (e.g., `ensure_signed(origin)?.encode()`), this finding is not exploitable — the length-based branch in `decode_receipt_relayer` is then a display-format ambiguity only (raw account id vs. wrapped `Signature`), not an authentication bypass, since both possible "shapes" would already be tied to a real signer. Given the extensive length-check hardening already present elsewhere in this codebase (`ByteVector<N>::decode`, `dedup_requests`, exact-length pharos/EVM branches), it is plausible the request-handling path already authenticates `signer` properly, which would make this a non-issue.

### Recommendation
- Confirm the call site that populates `signer` in `store_request_receipt`/`store_response_receipt` for substrate destinations and verify it is always derived from an authenticated origin (e.g., `ensure_signed`), never from unauthenticated message payload bytes.
- Regardless, replace the `bytes.len() > 32` heuristic in `decode_receipt_relayer` with an explicit, unambiguous encoding (e.g., a SCALE enum discriminant distinguishing "raw account id" from "signature-to-recover", matching how `crypto_utils::verification::Signature` is already tagged elsewhere) so the interpretation of the receipt value is never inferred from its length.

### Proof of Concept
Not constructible with the information available in the indexed codebase — the exploit path depends on the substrate `RequestMessage`/`ResponseMessage` handler internals (specifically whether `signer` bytes are attacker-controlled or derived from a verified extrinsic origin), which I could not locate. A background Devin session with full repository access should inspect `modules/ismp/core/src/handlers/request.rs` (or wherever `handle<H>(host, msg: RequestMessage)` calls `host.store_request_receipt(&req, &msg.signer)`) to determine whether `msg.signer` is authenticated before this finding can be confirmed or ruled out.

### Citations

**File:** modules/pallets/relayer/src/accumulate.rs (L106-126)
```rust
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

**File:** modules/ismp/core/src/host.rs (L163-165)
```rust
	/// Stores a receipt for an incoming request after it is successfully routed to a module.
	/// Prevents duplicate incoming requests from being processed. Includes the relayer account
	fn store_request_receipt(&self, req: &Request, signer: &Vec<u8>) -> Result<Vec<u8>, Error>;
```

**File:** modules/pallets/relayer/src/outbound_request.rs (L169-173)
```rust
		let delivered_by = Self::decode_receipt_relayer(destination, &raw)?;

		let msg = outbound_request_delivery_message(commitment, destination, payee);
		let recovered = signature.verify(&msg, None).map_err(|_| Error::<T>::InvalidSignature)?;
		ensure!(recovered == delivered_by, Error::<T>::OutboundRequestSignerMismatch);
```
