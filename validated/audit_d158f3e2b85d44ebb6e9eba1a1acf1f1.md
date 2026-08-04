Based on my investigation, the most concrete local analog to the Nocturne "unrestricted setter" bug class is in the Hyper Fungible Token pallet's cross-chain calldata execution path.

### Title
Unauthenticated runtime-call dispatch as an attacker-chosen identity via unsigned HFT calldata - (File: `modules/pallets/hyper-fungible-token/src/module.rs`)

### Summary
`Pallet::on_accept` in the hyper-fungible-token ISMP module executes an optional embedded `runtime_call` from the incoming cross-chain message body [1](#0-0) . When the message carries no `substrate_data.signature`, the dispatch `origin` is derived *directly* from the `message.from` bytes embedded in the attacker/relayer-controlled request body, with zero cryptographic proof that the entity named in `from` actually authorized this call [2](#0-1) . The call is then dispatched with `RawOrigin::Signed(origin)` [3](#0-2) .

### Finding Description
The only two paths that establish the dispatch identity are:
1. `substrate_data.signature` present — cryptographically verified (ed25519/sr25519/ecdsa) against `beneficiary` [4](#0-3) .
2. `substrate_data.signature` absent — origin is taken from `message.from`, converted straight into an `AccountId` with **no signature check at all** [2](#0-1) .

This mirrors the Nocturne pattern exactly: a critical identity value (`spendKey` there, dispatch `origin` here) is accepted from an unauthenticated caller-supplied field and then trusted for a privileged operation (arbitrary `RuntimeCall::dispatch`). The only remaining guard is `BaseCallFilter::contains(&runtime_call)` [5](#0-4) , which filters *which pallets/calls* are reachable, not *who* is allowed to act as `origin`. As long as the call type isn't runtime-filtered (e.g. `Balances::transfer`, `Assets::transfer`, staking, governance voting calls, etc.), any account can be impersonated by simply crafting a message whose `from` field equals the victim's `AccountId` bytes and appending a malicious `runtime_call`.

`message.from` is also reused for refunds on timeout (`on_timeout` uses the same field to determine the refund beneficiary) [6](#0-5) , showing `from` is meant to represent "the account that owns/authorized this transfer" — a security-relevant identity — yet it is not authenticated for the calldata-execution branch.

### Impact Explanation
If the `from` field in the ABI-encoded `Message` is attacker-controllable when dispatching the cross-chain send from the EVM/source side (i.e., not hard-pinned to `msg.sender` on-chain before encoding), an attacker can impersonate any account on the destination Substrate chain and force it to sign (as `RawOrigin::Signed`) any call not blocked by `BaseCallFilter` — e.g. transferring the victim's own free balance/assets, voting, or triggering any other dispatchable reachable as a signed extrinsic. This is a direct "unauthorized transaction/execution" and potential "loss of funds" impact matching the bounty scope.

### Likelihood Explanation
This requires only sending a normal ISMP post request through the registered HFT contract path (a public, permissionless entrypoint — no relayer/prover collusion is needed, since it only requires an unprivileged user calling `send()`/dispatching from the source chain with a forged `from` value and non-empty, unsigned `data`). The severity hinges entirely on whether the EVM-side contract binds `from` to `msg.sender` before encoding, which I could not fully confirm within available tool calls before running out of iterations — this is the key open question that determines whether this is exploitable in production or is actually safe by construction on the Solidity side.

### Recommendation
Require `substrate_data.signature` to always be present and verified for calldata execution regardless of the `from` field's origin, or explicitly bind the unsigned path's `origin` derivation to a value that is provably tied to the authenticated source-chain sender (e.g., verify on the Solidity/EVM side that `from == msg.sender` and treat any mismatch as a hard revert before dispatch, then document/enforce this invariant on the Substrate side too, since the pallet cannot itself verify EVM-side authenticity).

### Proof of Concept
1. Attacker calls the source-chain HFT `send()` (or crafts an equivalent ISMP POST) with `to = attacker_address`, a nonzero `amount`, and `data` = SCALE-encoded `SubstrateCalldata { runtime_call: <victim-draining call>, signature: None }`, while setting the message's `from` field to the victim's `AccountId` bytes.
2. On `on_accept`, since `substrate_data.signature` is `None`, `origin` is computed purely from `message.from` = victim's bytes [2](#0-1) .
3. `runtime_call.dispatch(RawOrigin::Signed(victim).into())` executes as the victim with no signature ever checked [7](#0-6) .

**Note on confidence:** I was unable to verify, before exhausting my tool budget, whether the corresponding Solidity `send()`/`Message` encoding on the EVM side pins `from` to `msg.sender` or allows caller-supplied `from` bytes. If it's hard-pinned, this finding is not exploitable; if it's caller-suppliable, it is a full account-impersonation bug. I recommend a Devin session with repository access to inspect `sdk/packages/core/contracts/apps/HyperFungibleToken.sol`'s `send()`/`_buildDispatchPost` and the `Message` ABI struct to confirm this before treating it as confirmed-exploitable.

### Citations

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L119-122)
```rust
		// Execute optional calldata
		if !message.data.is_empty() {
			let substrate_data = SubstrateCalldata::decode(&mut &message.data[..])
				.map_err(HftError::CalldataDecodeError)?;
```

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L124-173)
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
```

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L176-187)
```rust
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

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L191-196)
```rust
			// Apply the runtime's base call filter so that cross-chain calls cannot
			// reach dispatchables that the runtime has otherwise filtered out (e.g.
			// during a maintenance mode or a SafeMode period).
			if !<T as frame_system::Config>::BaseCallFilter::contains(&runtime_call) {
				Err(HftError::CallFiltered)?
			}
```

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L197-202)
```rust
			use sp_runtime::traits::Dispatchable;
			runtime_call
				.dispatch(RawOrigin::Signed(origin.clone()).into())
				.map_err(|e| HftError::CallDispatchError(e.error))?;

			frame_system::Pallet::<T>::inc_account_nonce(origin);
```

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L220-233)
```rust
			Request::Post(PostRequest { body, to, dest, .. }) => {
				let message = Message::abi_decode(&body).map_err(HftError::DecodeError)?;

				// Refund the original sender
				let from_bytes = message.from.as_ref();
				let mut sender_bytes = [0u8; 32];
				if from_bytes.len() == 32 {
					sender_bytes.copy_from_slice(from_bytes);
				} else if from_bytes.len() == 20 {
					sender_bytes[12..].copy_from_slice(from_bytes);
				} else {
					Err(HftError::InvalidSenderLength(from_bytes.len()))?
				}
				let beneficiary: T::AccountId = sender_bytes.into();
```
