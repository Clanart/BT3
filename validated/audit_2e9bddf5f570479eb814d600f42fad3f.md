## Analysis

Reducing the external report to its core broken invariant: **a signature is requested/verified without binding to the specific transaction context (account, origin, target) the signer believes they are approving, allowing execution under conditions the signer never intended.**

The closest local analog is in the `SubstrateCalldata` execution path of `pallet-hyper-fungible-token`'s `on_accept` handler.

### Title
Calldata-execution signature is not bound to the triggering transfer, allowing attacker-controlled execution of a beneficiary's pre-signed call via an unrelated incoming message - (File: `modules/pallets/hyper-fungible-token/src/module.rs`)

### Summary
When an inbound HFT (Hyper Fungible Token) message carries optional `data`, the pallet decodes a `SubstrateCalldata{ signature, runtime_call }` and, if a `signature` is present, verifies it over `(account_nonce(beneficiary), runtime_call)` only [1](#0-0) . The signed payload never includes the asset, amount, source/destination chain, or the specific `PostRequest` that carries it. If the signature checks out, the pallet dispatches `runtime_call` with `RawOrigin::Signed(beneficiary)` [2](#0-1) .

### Finding Description
The `data` field of an incoming `Message` is fully attacker-controlled: any caller of `send()`/the EVM `HyperFungibleToken` contract can set an arbitrary `data` payload for any beneficiary and any (even dust) `amount`/`asset` [3](#0-2) . There is no cryptographic linkage between:
- the `signature` a user creates (which only commits to `(nonce, runtime_call)`), and
- the actual transfer (`asset_id`, `amount`, `source`, `dest`) it ends up attached to.

Because the signature is domain-agnostic (no chain id, no HFT instance/module id, no amount/asset binding — contrast with `outbound_request_delivery_message`/`outbound_consensus_delivery_message` elsewhere in the codebase which explicitly bind `(commitment, dest_chain, payee)` or `(set_id, dest_chain, payee)` as domain separators) [4](#0-3) [5](#0-4) , a beneficiary who signs `(nonce, runtime_call)` believing they are authorizing a call *in the context of* a specific expected token receipt has no on-chain guarantee that assumption holds. Replay protection is enforced solely by `frame_system` account nonce increment after dispatch [6](#0-5) , not by binding to the triggering message.

An attacker who obtains a beneficiary's `(nonce, runtime_call)` signature (e.g. because the signing UI/flow — like the FilSnap dialog before its fix — never surfaced the amount/asset/chain being bridged as the actual authorizing context) can race the legitimate transfer: submit their own minimal-value HFT transfer to the same beneficiary, attach the captured signature+call in `data`, and have the pallet execute the beneficiary's `runtime_call` as `Signed(beneficiary)` before the beneficiary's own nonce advances via their intended transaction. This decouples "what the user thinks they're approving" from "what actually gets executed under their account," which is exactly the invariant the FilSnap report flags as broken — insufficient signing context leading to unintended authorization.

### Impact Explanation
This allows unauthorized execution of an arbitrary previously-signed runtime call under the victim's account, triggered by a transfer the victim never controls (amount, timing, or source chain), i.e., transaction manipulation / unauthorized execution — matching the "unauthorized transaction or execution" and "logic attacks" impact categories. Since the dispatched call runs with `RawOrigin::Signed(beneficiary)` [7](#0-6) , it can move the beneficiary's own funds (e.g. a pre-signed `Balances::transfer`) under conditions the beneficiary did not consent to.

### Likelihood Explanation
Exploitation requires the attacker to have already obtained a valid `(nonce, runtime_call)` signature from the victim — this is the same "confused signing" precondition as the FilSnap bug (a dialog/flow that doesn't show full context can trick a user into producing such a signature, or a legitimate signature meant for one specific bridging flow gets exposed/reused). Given that, no privileged role, relayer collusion, or governance action is needed: any unprivileged party can craft the triggering `send()` call with attacker-chosen dust amount/asset and race the pallet's `on_accept`.

### Recommendation
Bind the signed payload to the specific triggering context: include `asset_id`, `amount`, `source` state machine, and ideally the request `commitment`/nonce-from-message in the signed message, e.g. sign `(account_nonce, source, asset_id, amount, runtime_call)` instead of `(account_nonce, runtime_call)` alone, mirroring the domain separation already used in `outbound_request_delivery_message`/`outbound_consensus_delivery_message`.

### Proof of Concept
1. Victim signs `sig = sign((nonce=N, call=C))` off-chain, believing it authorizes `C` only as part of receiving a specific bridged amount `A` of asset `X` from chain `S` (per an app's UI flow).
2. Attacker observes/captures `sig` (leaked via a UI that — like unpatched FilSnap — doesn't bind or display the true scope of what's being signed).
3. Before victim's intended transfer lands, attacker calls the EVM `HyperFungibleToken.send()` (or equivalent peer) with `to = victim`, an arbitrary dust `amount`, any registered `asset`, and `data = abi.encode(SubstrateCalldata{ signature: sig, runtime_call: C })`.
4. `on_accept` in `modules/pallets/hyper-fungible-token/src/module.rs` credits the victim the dust amount, verifies `sig` against current `account_nonce(victim)` (still `N`) and `C` — both match since neither depends on the dust transfer's amount/asset/source — and dispatches `C` as `Signed(victim)` [8](#0-7) [2](#0-1) .
5. `C` executes exactly as it would have under the victim's originally intended (and differently-scoped) transfer, but now triggered by an attacker-controlled, unrelated dust transfer.

### Citations

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L50-72)
```rust
	fn on_accept(
		&self,
		PostRequest { body, from, source, .. }: PostRequest,
	) -> Result<Weight, anyhow::Error> {
		// Authenticate: look up which local asset this contract address maps to
		let local_asset_id = ContractToAsset::<T>::get(source, &from)
			.ok_or(HftError::UnknownSourceContract(source))?;

		// Decode the Message
		let message = Message::abi_decode(&body).map_err(HftError::DecodeError)?;

		// Convert recipient bytes to substrate AccountId
		// If 32 bytes: use directly. If 20 bytes: left-pad with zeros.
		let mut beneficiary_bytes = [0u8; 32];
		let to_bytes = message.to.as_ref();
		if to_bytes.len() == 32 {
			beneficiary_bytes.copy_from_slice(to_bytes);
		} else if to_bytes.len() == 20 {
			beneficiary_bytes[12..].copy_from_slice(to_bytes);
		} else {
			Err(HftError::InvalidRecipientLength(to_bytes.len()))?;
		}
		let beneficiary: T::AccountId = beneficiary_bytes.into();
```

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L124-154)
```rust
			let origin = if let Some(signature) = substrate_data.signature {
				let multi_signature = MultiSignature::decode(&mut &*signature)
					.map_err(HftError::SignatureDecodeError)?;

				let nonce = frame_system::Pallet::<T>::account_nonce(beneficiary.clone());

				match multi_signature {
					MultiSignature::Ed25519(sig) => {
						let payload = (nonce, substrate_data.runtime_call.clone()).encode();
						let msg = sp_io::hashing::keccak_256(&payload);
						let pub_key = beneficiary_bytes
							.as_slice()
							.try_into()
							.map_err(|_| HftError::SignatureVerificationFailed)?;
						if !sp_io::crypto::ed25519_verify(&sig, msg.as_ref(), &pub_key) {
							Err(HftError::SignatureVerificationFailed)?
						}
					},
					MultiSignature::Sr25519(sig) => {
						let payload = (nonce, substrate_data.runtime_call.clone()).encode();
						let msg = sp_io::hashing::keccak_256(&payload);
						let pub_key = beneficiary_bytes
							.as_slice()
							.try_into()
							.map_err(|_| HftError::SignatureVerificationFailed)?;
						if !sp_io::crypto::sr25519_verify(&sig, msg.as_ref(), &pub_key) {
							Err(HftError::SignatureVerificationFailed)?
						}
					},
					MultiSignature::Ecdsa(sig) => {
						let payload = (nonce, substrate_data.runtime_call.clone()).encode();
```

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L197-202)
```rust
			use sp_runtime::traits::Dispatchable;
			runtime_call
				.dispatch(RawOrigin::Signed(origin.clone()).into())
				.map_err(|e| HftError::CallDispatchError(e.error))?;

			frame_system::Pallet::<T>::inc_account_nonce(origin);
```

**File:** modules/pallets/relayer/src/outbound_request.rs (L203-209)
```rust
pub fn outbound_request_delivery_message(
	commitment: H256,
	dest_chain: StateMachine,
	payee: [u8; 32],
) -> [u8; 32] {
	sp_io::hashing::keccak_256(&(commitment, dest_chain, payee).encode())
}
```

**File:** modules/pallets/relayer/src/outbound_consensus.rs (L224-230)
```rust
pub fn outbound_consensus_delivery_message(
	set_id: u64,
	dest_chain: StateMachine,
	payee: [u8; 32],
) -> [u8; 32] {
	sp_io::hashing::keccak_256(&(set_id, dest_chain, payee).encode())
}
```
