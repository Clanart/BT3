### Title
`on_accept` cross-chain calldata authorization signature omits chain/domain binding, enabling replay across independent HFT-pallet deployments - (File: `modules/pallets/hyper-fungible-token/src/module.rs`)

### Summary
The `HyperFungibleToken` pallet's `on_accept` handler lets a bridged ERC-20 transfer carry an optional `SubstrateCalldata` payload that dispatches an arbitrary `RuntimeCall` on behalf of the recipient, authorized by a signature the recipient produced off-chain. The signed message is only `(nonce, runtime_call)` — it contains no reference to the specific chain/pallet instance the authorization is meant for, mirroring the `Project.sol#updateProjectHash` flaw where `_data` lacked a reference to the project address.

### Finding Description
In `on_accept` [1](#0-0) , when `message.data` is non-empty, the pallet decodes a `MultiSignature` and constructs the signed payload as:

```rust
let nonce = frame_system::Pallet::<T>::account_nonce(beneficiary.clone());
let payload = (nonce, substrate_data.runtime_call.clone()).encode();
let msg = sp_io::hashing::keccak_256(&payload);
``` [2](#0-1) 

This is verified against the beneficiary's public key (Ed25519/Sr25519) or, for Ecdsa, converted to a substrate account via `EvmToSubstrate::convert` and compared to `beneficiary` [3](#0-2) . After dispatch, replay protection relies solely on `frame_system::Pallet::<T>::inc_account_nonce(origin)` [4](#0-3) , which mutates the account's *generic* `frame_system` nonce — the same counter used for the account's ordinary signed extrinsics on that chain.

Unlike a normal Substrate `SignedExtension`, which binds a transaction to a specific chain via `genesis_hash`/`spec_version` in the signed payload, this bespoke authorization scheme signs only `(nonce, runtime_call)`. There is no genesis hash, no state-machine/chain identifier, and no reference to the specific `HyperFungibleToken` pallet instance or source contract (`source`, `from`) that the authorization was intended for — exactly the missing binding that made `Project.sol::updateProjectHash`'s `_data` replayable across different `Project` instances sharing a builder/contractor and nonce.

Because the same sr25519/ed25519/ecdsa keypair is commonly reused by the same account across multiple chains (sibling parachains, testnet/mainnet pairs, or any two deployments running this pallet), a signature a user produces to authorize a call on Chain A remains valid to authorize the identical call on Chain B whenever the user's `frame_system` account nonce on Chain B happens to equal the nonce it was signed for on Chain A — a condition well within reach given that these nonces start at 0 and increment predictably.

### Impact Explanation
If exploited, this permits **unauthorized execution** of a runtime call as the victim's account on a chain the signature was never intended for, and — via `RawOrigin::Signed(origin)` dispatch — allows the call to move funds or invoke privileged extrinsics gated only by "signed by this account" (transfers, staking, governance votes bound to that account, etc.). This matches the bounty's "unauthorized transaction or execution" and "transaction manipulation" categories, since the invariant "one authorization → one intended context" is broken, exactly as in the referenced Project.sol finding.

### Likelihood Explanation
Exploitation requires: (1) the pallet or an identical runtime deployed on more than one chain reachable by Hyperbridge, and (2) the victim's account nonce coinciding across the two deployments (trivial for freshly created or low-activity accounts, and deterministic for accounts whose activity is otherwise known/observable). No relayer, prover, or admin compromise is needed — an ordinary observer who captures a legitimate cross-chain calldata message (data is public on-chain/in mempool) can resubmit the token transfer + calldata pair to a second HFT deployment where the nonce still matches. This is a moderate-likelihood but structurally real gap, not requiring privileged actors.

### Recommendation
Bind the signed payload to the specific execution context, mirroring how ISMP already binds `source`/`dest`/`nonce`/`from`/`to` in request commitments elsewhere in the codebase (see `postRequestCommitment` binding source/dest/nonce/from/to/timeout [5](#0-4) ). Concretely, include the chain's genesis hash (or `StateMachine` identifier), the `source` contract, and ideally the request commitment/`message` hash itself in the signed payload:
```rust
let payload = (
    frame_system::Pallet::<T>::block_hash(0u32.into()), // or a dedicated chain id
    source,
    nonce,
    substrate_data.runtime_call.clone(),
).encode();
```
Additionally, avoid overloading the general `frame_system` account nonce as the anti-replay counter for this feature; use a dedicated, pallet-local nonce map keyed by `(beneficiary, chain identifier)` instead, so ordinary transaction activity and bridge authorizations cannot be conflated or predicted across deployments.

### Proof of Concept
1. On Chain A (HFT pallet deployed), a user signs `(nonce=0, runtime_call=X)` off-chain and includes it as `SubstrateCalldata` in a bridged ERC-20 transfer to themselves; the relayer delivers it, `on_accept` executes `X` and bumps the user's `frame_system` nonce to 1 on Chain A.
2. The same user's keypair also controls an account on Chain B, where the same HFT pallet is deployed and the account's `frame_system` nonce is still 0 (freshly created, or the same nonce independently reached through the account's normal Chain B activity).
3. An attacker observes the calldata + signature from step 1 (public on-chain data) and submits an equivalent bridged transfer + identical `SubstrateCalldata` blob to Chain B's HFT instance.
4. `on_accept` on Chain B recomputes `nonce = account_nonce(beneficiary) = 0`, hashes `(0, X)`, and successfully verifies the same signature — since nothing in the signed payload differs between chains — and dispatches `X` as the victim on Chain B without the victim's consent for that chain.

### Citations

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L119-171)
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
```

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L198-202)
```rust
			runtime_call
				.dispatch(RawOrigin::Signed(origin.clone()).into())
				.map_err(|e| HftError::CallDispatchError(e.error))?;

			frame_system::Pallet::<T>::inc_account_nonce(origin);
```

**File:** sdk/packages/indexer/src/services/request.service.ts (L239-250)
```typescript
		// Convert source/dest from state-machine strings ("EVM-97" etc.) to bytes.
		const sourceByte = ethers.utils.toUtf8Bytes(source)
		const destByte = ethers.utils.toUtf8Bytes(dest)

		// Mirror the EVM host's commitment: keccak256(abi.encode(PostRequest)),
		// with the outer tuple wrapper. Field order matches the PostRequest struct
		// in core/libraries/Message.sol: source, dest, nonce, from, to, timeoutTimestamp, body.
		const encoded = ethers.utils.defaultAbiCoder.encode(
			["tuple(bytes,bytes,uint64,bytes,bytes,uint64,bytes)"],
			[[sourceByte, destByte, nonce, from, to, timeoutTimestamp, body]],
		)
		return ethers.utils.keccak256(encoded)
```
