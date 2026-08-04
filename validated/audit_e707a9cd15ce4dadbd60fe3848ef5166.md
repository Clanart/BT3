Confirmed: `message.from` in the `Message` struct is set on the EVM side by `_buildDispatchPost` as `abi.encodePacked(msg.sender)` — i.e., it is authenticated by the sender of the `send()` call on the source chain, not attacker-arbitrary data supplied inside a payload the attacker fully controls independent of the transaction sender. This is analogous to the ZRC20 report's "hook not triggered on the alternate path" pattern, but here the specific bypass is different: on the Substrate side (`modules/pallets/hyper-fungible-token/src/module.rs`), the `on_accept` calldata-execution branch has **two distinct authorization paths** for the `RuntimeCall::dispatch`:

1. **Signed path**: `substrate_data.signature = Some(sig)` → cryptographic signature checked against `beneficiary`, replay-protected via `frame_system::account_nonce`.
2. **Unsigned path**: `substrate_data.signature = None` → `origin` is derived directly from `message.from` (the cross-chain sender address, i.e. `msg.sender` from the EVM `send()` call, mapped to a Substrate account via `EvmToSubstrate::convert`) and the call is dispatched as `RawOrigin::Signed(origin)` **without any signature check**.

### Title
Unsigned calldata-execution path in `pallet-hyper-fungible-token::on_accept` dispatches arbitrary runtime calls as the cross-chain sender without cryptographic authorization - (File: modules/pallets/hyper-fungible-token/src/module.rs)

### Summary
`on_accept` allows an incoming cross-chain message to carry an optional `SubstrateCalldata { signature: Option<Vec<u8>>, runtime_call }`. When `signature` is `None`, the pallet skips all cryptographic verification and dispatches `runtime_call` under `RawOrigin::Signed(origin)`, where `origin` is derived purely from the message's `from` field (the EVM `msg.sender` who called `send()` on the source chain contract, converted to a Substrate `AccountId` via `T::EvmToSubstrate`).

### Finding Description
In the signed branch, the pallet properly verifies an Ed25519/Sr25519/Ecdsa signature over `(nonce, runtime_call)` before trusting the derived origin, providing account-level authorization independent of the ISMP transport layer. [1](#0-0) 

In the unsigned branch, there is no equivalent check — `origin` is set straight from `message.from` and used to dispatch the call: [2](#0-1) 

Because `message.from` in the EVM `Message` struct is populated by the source contract as `abi.encodePacked(msg.sender)`: [3](#0-2) 

any EVM account can call `send()` with an arbitrary `SendParams.data` payload containing a `SubstrateCalldata` with `signature: None` and any `runtime_call` bytes it wants. Once relayed and accepted by the destination Substrate chain (subject only to `BaseCallFilter`), the pallet dispatches that call as if the calling EVM account (mapped to Substrate) had signed it locally — with no on-chain signature, no session key, and no explicit substrate-side consent from that mapped account. The only "authentication" is that the message passed ISMP membership/state proofs, which prove the *transport* was legitimate, not that the mapped Substrate account authorized this specific `runtime_call`.

This differs qualitatively from the ZRC20 report's core defect (a check exists but is skipped on an alternate call path) in the same way: a security check (cryptographic signature/consent) exists on one path (`signature: Some`) but is entirely optional and skippable via a legitimate, unprivileged, permissionless entry point (`send()` on the EVM side, then relayed).

### Impact Explanation
If `T::EvmToSubstrate::convert` maps distinct EVM addresses to Substrate accounts that hold funds, permissions, or governance rights (e.g., accounts with existing balances, staking positions, or elevated call permissions reachable through `BaseCallFilter`), an attacker (any EVM address holder) can force arbitrary dispatchable calls to execute under that mapped account's authority without any cryptographic consent from the real controller of that Substrate key. This is unauthorized transaction/execution — the exact class the bounty targets — since it lets an unprivileged caller execute privileged operations (transfers, approvals, or any call permitted by `BaseCallFilter`) as another account.

### Likelihood Explanation
The path is reachable by any unprivileged caller: calling `send()` on the EVM `HyperFungibleToken` contract with a non-empty `data` field whose decoded `SubstrateCalldata.signature == None` is all that's required, followed by normal relaying (permissionless) of the resulting POST request. No malicious relayer, prover, or admin is needed — the vulnerability is in application logic reachable from a standard `send()` call.

### Recommendation
Require a valid signature for every calldata-execution request, or restrict the unsigned path to only allow calls where `origin` matches a call whose effects are safe regardless of consent (e.g., disallow `RawOrigin::Signed` dispatch entirely in the unsigned branch, or bind the unsigned branch strictly to calls that only affect the message's `beneficiary`/escrow account created by this same message, never an independently-funded pre-existing account). At minimum, treat the unsigned branch as fully attacker-controlled input and gate it by an explicit allowlist of dispatchables rather than the general `BaseCallFilter`.

### Proof of Concept
1. On the EVM source chain, call `HyperFungibleToken.send()` with `params.data` = ABI-encoded `Call[]`/`SubstrateCalldata` where `signature = None` and `runtime_call` = SCALE-encoded call, e.g. `Balances::transfer_allow_death` moving funds out of the Substrate account that `T::EvmToSubstrate::convert(msg.sender)` maps to.
2. A relayer submits the resulting `PostRequest` to the destination Substrate chain via `pallet-ismp`'s request handler; membership/state proof passes normally (transport is legitimate).
3. `pallet_hyper_fungible_token::on_accept` decodes `SubstrateCalldata`, sees `signature == None`, sets `origin = EvmToSubstrate::convert(msg.sender)`, and dispatches the call as `RawOrigin::Signed(origin)` — moving funds/executing state changes on that account with no signature ever produced by its actual Substrate-side key holder. [4](#0-3) 

**Uncertainty note:** I was not able to inspect `T::EvmToSubstrate::convert`'s concrete implementation used in production runtimes (gargantua/nexus set `type EvmToSubstrate = ()`), nor the exact `BaseCallFilter` configured for those runtimes, within this session — these determine the real-world blast radius (whether the mapped account can hold funds/permissions in practice). This should be verified directly in the runtime configuration before treating this as a confirmed critical finding.

### Citations

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

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L176-203)
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

			let runtime_call = T::RuntimeCall::decode(&mut &*substrate_data.runtime_call)
				.map_err(HftError::RuntimeCallDecodeError)?;
			// Apply the runtime's base call filter so that cross-chain calls cannot
			// reach dispatchables that the runtime has otherwise filtered out (e.g.
			// during a maintenance mode or a SafeMode period).
			if !<T as frame_system::Config>::BaseCallFilter::contains(&runtime_call) {
				Err(HftError::CallFiltered)?
			}
			use sp_runtime::traits::Dispatchable;
			runtime_call
				.dispatch(RawOrigin::Signed(origin.clone()).into())
				.map_err(|e| HftError::CallDispatchError(e.error))?;

			frame_system::Pallet::<T>::inc_account_nonce(origin);
		}
```

**File:** sdk/packages/core/contracts/apps/HyperFungibleToken.sol (L241-246)
```text
        bytes memory body = abi.encode(Message({
            from: abi.encodePacked(msg.sender),
            to: params.to,
            amount: params.amount,
            data: params.data
        }));
```
