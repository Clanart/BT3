## Analysis

The SEDA bug's core primitive: an unprivileged actor supplies an attacker-controlled **length**/size value that is used to index/slice a buffer without validating it against the buffer's actual size, causing a deterministic **Rust panic inside consensus-critical host/runtime execution** — which halts every validator that processes it (not just a `revert`/`Err`, which would be gracefully handled).

Searching Hyperbridge for the same shape (unchecked slice arithmetic driven by attacker-controlled byte length, reachable from an unsigned/permissionless message-execution path) surfaces a live analog in `pallet-hyper-fungible-token`'s `on_accept` callback, which is invoked from `pallet_ismp::handle_unsigned` — an extrinsic **any relayer can submit**. [1](#0-0) 

### Title
Attacker-controlled `message.from` byte length panics `HyperFungibleToken::on_accept`, crashing block execution on all validators - (File: `modules/pallets/hyper-fungible-token/src/module.rs`)

### Summary
`pallet_hyper_fungible_token::on_accept` decodes an ABI-encoded `Message` from an incoming ISMP `PostRequest.body`. When the message carries optional calldata (`message.data`) whose decoded `SubstrateCalldata.signature` is `None`, the pallet derives the dispatch origin from `message.from` using unchecked slice arithmetic:
```rust
let from_bytes = message.from.as_ref();
if source.is_evm() {
    T::EvmToSubstrate::convert(H160::from_slice(&from_bytes[from_bytes.len() - 20..]))
} else {
    let mut account = [0u8; 32];
    account.copy_from_slice(from_bytes);
    account.into()
}
``` [2](#0-1) 

`message.from` is an ABI `bytes` field inside the attacker/relayer-supplied `Message`, decoded earlier via `Message::abi_decode(&body)` with no length constraint enforced. [3](#0-2) 

### Finding Description
The only authentication performed before this code runs is that the **top-level PostRequest sender contract** (`source`, `from` fields of the ISMP message) maps to a known asset via `ContractToAsset::<T>::get(source, &from)`. [4](#0-3) 

That check validates the *module address* that sent the cross-chain message — it does **not** constrain the *inner* `message.from` field, which is fully attacker-controlled ABI-encoded `bytes` inside the message body. Once the mint/transfer to `message.to` completes, the pallet unconditionally decodes `message.data` as `SubstrateCalldata` if non-empty, and when no signature is present, falls into the vulnerable branch that indexes `from_bytes` based on its own length:

- If `source.is_evm()` and `from_bytes.len() < 20`, `from_bytes.len() - 20` underflows (`usize` subtraction), producing either an overflow panic or a slice-index panic (`range end index N out of range for slice of length M`) when used as `&from_bytes[huge..]`.
- If `source` is **not** EVM (e.g. a Substrate/Kusama/Polkadot source) and `from_bytes.len() != 32`, `account.copy_from_slice(from_bytes)` panics deterministically with "source slice length does not match destination slice length" for *any* length other than exactly 32.

Both panics are triggered purely by the byte-length of an attacker-chosen field, exactly mirroring the SEDA bug's root cause: an unchecked length used to index/copy a buffer, with no bounds validation performed before the operation.

### Impact Explanation
`on_accept` executes inside `pallet_ismp::execute` → `handlers::request::handle` → module callback dispatch, which itself runs inside the unsigned, permissionless extrinsic `handle_unsigned` that any relayer may submit with a valid consensus/state proof for a legitimate, already-registered TokenGateway/HFT source contract. [5](#0-4) 

A Rust panic raised during extrinsic dispatch inside the runtime is not a graceful `Err` — it unwinds/traps the WASM execution of that block. Because the payload is deterministic and identical for every validator importing the same block, **every validator executing this extrinsic panics identically**, which is the same "crash the host, halt the chain" primitive described in the SEDA report, just triggered through a Substrate pallet callback instead of a WASM VM import.

### Likelihood Explanation
Likelihood is high for any relayer who can get a legitimate PostRequest accepted from an already-configured TokenGateway/HFT source contract (a normal, permissionless operational precondition — no malicious relayer/prover/admin needed, since the relayer is merely forwarding a message the attacker crafted on the source chain, or the attacker is the source-chain sender themselves dispatching the malicious `Message.from`/`data` payload). The only requirements are:
1. `ContractToAsset` mapping exists for the source chain/contract pair (this is normal production configuration, not a privileged bypass).
2. `message.data` is non-empty and decodes as `SubstrateCalldata` with `signature: None`.
3. `message.from` has a length other than 20 (EVM source) or 32 (non-EVM source).

All three are fully within an ordinary end-user/attacker's control when constructing the source-chain message that gets bridged.

### Recommendation
Validate `from_bytes.len()` explicitly before use and return `HftError::InvalidRecipientLength`-style errors instead of slicing/copying blindly, mirroring the length checks already applied to `message.to` earlier in the same function:
```rust
let from_bytes = message.from.as_ref();
let origin_account = if source.is_evm() {
    if from_bytes.len() < 20 {
        Err(HftError::InvalidRecipientLength(from_bytes.len()))?;
    }
    T::EvmToSubstrate::convert(H160::from_slice(&from_bytes[from_bytes.len() - 20..]))
} else {
    if from_bytes.len() != 32 {
        Err(HftError::InvalidRecipientLength(from_bytes.len()))?;
    }
    let mut account = [0u8; 32];
    account.copy_from_slice(from_bytes);
    account.into()
};
```

### Proof of Concept
1. Attacker (or any user) on a registered EVM source chain dispatches a cross-chain HFT transfer whose ABI-encoded `Message.data` is non-empty and decodes as `SubstrateCalldata { signature: None, runtime_call: <anything> }`.
2. Attacker sets `Message.from` to a `bytes` value of length, e.g., 5 (for an EVM source) or any length other than 32 (for a non-EVM source).
3. A relayer submits the resulting `PostRequest` via `pallet_ismp::handle_unsigned` with a valid proof (this is the normal, permissionless path).
4. `HyperFungibleToken::on_accept` mints/transfers to `message.to`, then reaches:
   - `&from_bytes[from_bytes.len() - 20..]` with `from_bytes.len() == 5` → `5usize - 20` underflows → slice indexing panics, or
   - `account.copy_from_slice(from_bytes)` with `from_bytes.len() != 32` → panics with slice-length mismatch.
5. The panic occurs inside block-execution of every validator applying this extrinsic, halting import of that block chain-wide. [2](#0-1)

### Citations

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L54-56)
```rust
		// Authenticate: look up which local asset this contract address maps to
		let local_asset_id = ContractToAsset::<T>::get(source, &from)
			.ok_or(HftError::UnknownSourceContract(source))?;
```

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L58-59)
```rust
		// Decode the Message
		let message = Message::abi_decode(&body).map_err(HftError::DecodeError)?;
```

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L119-187)
```rust
		// Execute optional calldata
		if !message.data.is_empty() {
			let substrate_data = SubstrateCalldata::decode(&mut &message.data[..])
				.map_err(HftError::CalldataDecodeError)?;

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
						let preimage = vec![
							format!("{ETHEREUM_MESSAGE_PREFIX}{}", payload.len())
								.as_bytes()
								.to_vec(),
							payload,
						]
						.concat();
						let msg = sp_io::hashing::keccak_256(&preimage);
						let pub_key = sp_io::crypto::secp256k1_ecdsa_recover(&sig.0, &msg)
							.map_err(|_| HftError::EcdsaRecoveryFailed)?;
						let eth_address =
							H160::from_slice(&sp_io::hashing::keccak_256(&pub_key[..])[12..]);
						let substrate_account = T::EvmToSubstrate::convert(eth_address);
						if substrate_account != beneficiary {
							Err(HftError::SignatureVerificationFailed)?
						}
					},
					MultiSignature::Eth(_) => Err(HftError::EthSignatureUnsupported)?,
				};

				beneficiary.clone()
			} else {
				let from_bytes = message.from.as_ref();
				if source.is_evm() {
					T::EvmToSubstrate::convert(H160::from_slice(
						&from_bytes[from_bytes.len() - 20..],
					))
				} else {
					let mut account = [0u8; 32];
					account.copy_from_slice(from_bytes);
					account.into()
				}
			};
```

**File:** modules/pallets/ismp/src/lib.rs (L358-382)
```rust
	#[pallet::call]
	impl<T: Config> Pallet<T> {
		/// Execute the provided batch of ISMP messages, this will short-circuit and revert if any
		/// of the provided messages are invalid. This is an unsigned extrinsic that permits anyone
		/// execute ISMP messages for free, provided they have valid proofs and the messages have
		/// not been previously processed.
		///
		/// The dispatch origin for this call must be an unsigned one.
		///
		/// - `messages`: the messages to handle or process.
		///
		/// Emits different message events based on the Message received if successful.
		#[pallet::weight(weight())]
		#[pallet::call_index(0)]
		#[frame_support::transactional]
		pub fn handle_unsigned(
			origin: OriginFor<T>,
			messages: Vec<Message>,
		) -> DispatchResultWithPostInfo {
			ensure_none(origin)?;

			Self::execute(messages.clone())?;

			Ok(().into())
		}
```
