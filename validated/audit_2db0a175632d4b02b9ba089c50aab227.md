Found the critical vulnerability: `extract_signer` in `modules/pallets/ismp/src/host.rs` does not cryptographically verify the signature against the request/response content — it only decodes the signature structure and extracts the embedded public key/address, without checking that the corresponding private key actually signed anything tied to the message.### Title
Unverified relayer `signer` field lets any caller forge delivery attribution and mint/claim rewards for messages they never relayed - (File: `modules/pallets/ismp/src/host.rs`)

### Summary
`RequestMessage`/`ResponseMessage` carry a free-form `signer: Vec<u8>` field [1](#0-0)  that is written straight into `RequestReceipts`/`ResponseReceipts` via `store_request_receipt`/`store_response_receipt`. The helper `extract_signer` only *decodes* the SCALE `Signature` enum to pull out the embedded public key/address — it never calls `Signature::verify` against the message content, so there is no cryptographic check that the named account actually produced or relayed anything [2](#0-1) [3](#0-2) .

### Finding Description
`handle_unsigned` (the ISMP message entrypoint) accepts a `RequestMessage`/`ResponseMessage` whose `signer` bytes are attacker-controlled — any account can submit a validly proven request/response batch and set `signer` to any `Signature::Sr25519 { public_key, signature }` (or Evm/Ed25519) value they like, with the `signature` bytes being garbage. `extract_signer` never validates that `signature` is a valid signature over anything:

```rust
fn extract_signer(signer: &[u8]) -> Result<Vec<u8>, Error> {
	if signer.len() > 32 {
		Signature::decode(&mut signer.as_ref())
			.map(|sig| sig.signer())      // <-- just returns embedded pubkey, no verify()
			.map_err(|_| Error::SignatureDecodingFailed)
	} else {
		Ok(signer.to_vec())
	}
}
``` [3](#0-2) 

Compare this with `Signature::signer()` vs `Signature::verify()` in the crypto-utils crate: `signer()` is a pure decode with no cryptographic check, while `verify()` actually recovers/validates the signature against a message hash [4](#0-3) [5](#0-4) . `extract_signer` only ever calls the unverified `signer()` path.

This "relayer" value flows into two reward-bearing systems that trust it as ground truth of who delivered the message:

1. `pallet-messaging-incentives::relayer_for` recovers a relayer account from `msg.signer` and mints reputation tokens to it based on payload size, gated only by `Signature::verify_and_get_sr25519_pubkey` — this call *does* verify the sr25519 signature over `keccak_256(&msg.requests.encode())` [6](#0-5) , so this particular consumer is not directly exploitable — the mint requires a real signature over the request payload.
2. However, `store_request_receipt`/`store_response_receipt` persist `extract_signer(signer)` — the **unverified** signer — into `RequestReceipts`/`ResponseReceipts` on the destination chain [7](#0-6) . This same receipt is later read back as the authoritative "who delivered this" value by the fee-accumulation and outbound-request-delivery-reward pipelines: `decode_receipt_relayer` decodes `RequestReceipts[commitment]` and treats it as the delivering relayer's identity for both `accumulate_fees` and `claim_outbound_request_delivery_reward` [8](#0-7) . Those downstream flows *do* additionally require a fresh signature over `outbound_request_delivery_message(commitment, destination, payee)` matching the value read from the receipt [9](#0-8)  — so an attacker who plants an arbitrary "delivered_by" value in the receipt can subsequently sign that same value themselves and pass the `OutboundRequestSignerMismatch` check, since the check only requires `recovered == delivered_by`, and `delivered_by` itself was never proven to be the entity that produced the receipt.

In short: the receipt's `relayer`/`signer` field — the on-chain record used everywhere downstream to decide "who gets paid for this delivery" — is written unauthenticated. Any account (not just a legitimate relayer) can be recorded as the deliverer of any request/response it can front-run past a real relayer (since `on_accept`/`on_response` dedupe by receipt existence, whoever's message lands first wins the attribution), and then claim the associated reward for a delivery it did not actually perform the off-chain relaying work for.

### Impact Explanation
This breaks the "reward exactly the rightful beneficiary" invariant for relayer incentives: an unprivileged account can submit a `RequestMessage`/`ResponseMessage` with a forged `signer` and cause `RequestReceipts`/`ResponseReceipts` to record an address of its choosing (its own) as the delivering party, even without doing meaningful relaying work beyond copying a valid proof someone else already produced or observed. Because downstream reward/fee-accumulation logic (`accumulate_fees`, `claim_outbound_request_delivery_reward`) trusts this receipt value as the sole ground truth for "who delivered", this allows unauthorized diversion of relayer fees/treasury rewards to an attacker-chosen account.

### Likelihood Explanation
Moderate-to-high: `handle_unsigned` extrinsics are unsigned/permissionless by design (any account can submit a valid `RequestMessage` with a real membership proof), and `extract_signer` performs zero cryptographic verification before persisting the attacker-supplied identity as the receipt's relayer. The main constraint is that only the *first* successfully-processed message for a given commitment gets its receipt stored (subsequent ones are rejected as `DuplicateRequest`/`DuplicateResponse`), so exploitation requires racing/front-running the legitimate relayer's submission with the same (or self-copied) valid proof and a self-signed `signer` field — feasible for any actor watching the mempool/relayer traffic.

### Recommendation
`extract_signer` (and any code path storing `RequestReceipts`/`ResponseReceipts`) must call `Signature::verify` against a message hash that is bound to the actual request/response content (e.g. the same `keccak_256(&msg.requests.encode())` scheme already used in `pallet-messaging-incentives::relayer_for`), not merely decode and return the embedded public key. Reject the message if verification fails, so a stored receipt's relayer identity is cryptographically tied to a signature over the delivered payload, closing the gap between "who submitted a message" and "who is credited/paid for delivering it."

### Proof of Concept
1. Observe (or independently reconstruct) a valid `RequestMessage` — `{ requests, proof }` — that a legitimate relayer is about to submit or has already submitted for a not-yet-delivered request on a destination chain.
2. Craft `signer = Signature::Sr25519 { public_key: attacker_pubkey, signature: <arbitrary 64 bytes> }.encode()`. No valid signature is required because `extract_signer` never calls `verify`.
3. Submit `Ismp::handle_unsigned([Message::Request(RequestMessage { requests, proof, signer })])` before the legitimate relayer's transaction lands (or simultaneously with a faster propagation path).
4. `modules/ismp/core/src/handlers/request.rs::handle` verifies the state proof (which is valid/reusable, independent of `signer`), finds no existing receipt, and calls `host.store_request_receipt(&wrapped_req, &msg.signer)` [10](#0-9) , which stores `attacker_pubkey` as `RequestReceipts[commitment]` via `extract_signer` without ever validating the bogus signature [2](#0-1) .
5. The legitimate relayer's identical follow-up submission for the same commitment is now rejected with `DuplicateRequest`.
6. Any subsequent reward flow that reads `RequestReceipts[commitment]` as the "delivering relayer" (fee accumulation, outbound-request delivery reward) now attributes the delivery to the attacker's `attacker_pubkey`, which the attacker can additionally re-sign to satisfy the later `OutboundRequestSignerMismatch` check, diverting the reward to itself.

### Citations

**File:** modules/ismp/core/src/messaging.rs (L116-127)
```rust
/// A request message holds a batch of requests to be dispatched from a source state machine
#[derive(
	Debug, Clone, Encode, DecodeWithMemTracking, Decode, scale_info::TypeInfo, PartialEq, Eq,
)]
pub struct RequestMessage {
	/// Requests from source chain
	pub requests: Vec<PostRequest>,
	/// Membership batch proof for these requests
	pub proof: Proof,
	/// Signer information. Ideally should be their account identifier
	pub signer: Vec<u8>,
}
```

**File:** modules/pallets/ismp/src/host.rs (L261-283)
```rust
	fn store_request_receipt(&self, req: &Request, signer: &Vec<u8>) -> Result<Vec<u8>, Error> {
		let signer = extract_signer(signer)?;

		let hash = hash_request::<Self>(req);
		child_trie::RequestReceipts::<T>::insert(hash, &signer);
		Ok(signer)
	}

	fn store_response_receipt(
		&self,
		res: &GetResponse,
		signer: &Vec<u8>,
	) -> Result<Vec<u8>, Error> {
		let signer = extract_signer(signer)?;

		let hash = hash_request::<Self>(&res.request());
		let response = hash_response::<Self>(&res);
		child_trie::ResponseReceipts::<T>::insert(
			hash,
			ResponseReceipt { response, relayer: signer.clone() },
		);
		Ok(signer)
	}
```

**File:** modules/pallets/ismp/src/host.rs (L351-359)
```rust
fn extract_signer(signer: &[u8]) -> Result<Vec<u8>, Error> {
	if signer.len() > 32 {
		Signature::decode(&mut signer.as_ref())
			.map(|sig| sig.signer())
			.map_err(|_| Error::SignatureDecodingFailed)
	} else {
		Ok(signer.to_vec())
	}
}
```

**File:** modules/utils/crypto/src/verification.rs (L32-56)
```rust
impl Signature {
	/// verify the signature with the public key in the enum or optionally provide a public key
	/// to be used to verify the signature
	pub fn verify(
		&self,
		msg: &[u8; 32],
		public_key_op: Option<Vec<u8>>,
	) -> Result<Vec<u8>, anyhow::Error> {
		match self {
			Signature::Evm { signature, .. } => {
				if signature.len() != 65 {
					Err(anyhow!("Invalid Signature"))?
				}

				let mut sig = [0u8; 65];
				sig.copy_from_slice(&signature);
				let pub_key = sp_io::crypto::secp256k1_ecdsa_recover(&sig, msg)
					.map_err(|_| anyhow!("Signature Verification failed"))?;
				let signer = sp_io::hashing::keccak_256(&pub_key[..])[12..].to_vec();
				Ok(signer)
			},
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

**File:** modules/ismp/core/src/handlers/request.rs (L99-112)
```rust
		.map(|request| {
			let wrapped_req = Request::Post(request.clone());
			let mut lambda = || {
				let cb = router.module_for_id(request.to.clone())?;
				// Re-check the receipt right before dispatch. The up-front pass above
				// runs before any callback executes; a prior request's on_accept in
				// this same batch could have stored a receipt for this request
				// (directly or by re-entering the handler), and we must not invoke
				// on_accept a second time.
				if host.request_receipt(&wrapped_req).is_some() {
					Err(Error::DuplicateRequest { meta: wrapped_req.clone().into() })?
				}
				// Store request receipt to prevent reentrancy attack
				let signer = host.store_request_receipt(&wrapped_req, &msg.signer)?;
```
