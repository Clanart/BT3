## Title
Cross-chain runtime-call authorization signature omits chain/pallet domain binding, enabling replay of a signed call on an unintended destination chain - (File: modules/pallets/hyper-fungible-token/src/module.rs)

### Summary
This is the same broken-invariant class as the reported EIP-712 domain-separator bug: a signature is validated against a payload that omits the contextual "domain" data (there, the token name; here, the chain/pallet identity), so a signature produced for one execution context remains valid and is accepted in a different, unintended context. In `pallet-hyper-fungible-token`'s `on_accept`, the signed payload authorizing an arbitrary `runtime_call` dispatch is `(nonce, runtime_call).encode()` — it binds neither the destination chain, nor the pallet, nor the source chain/contract, allowing the same signature to authorize execution of the same call as the same beneficiary on any other chain running this pallet where the beneficiary's account nonce matches.

### Finding Description
`send` in `modules/pallets/hyper-fungible-token/src/lib.rs:241-325` lets any signed account attach fully attacker/user-controlled `call_data` (`SubstrateCalldata { signature, runtime_call }`) to a cross-chain token message, with no restriction on its contents: [1](#0-0) 

On the receiving side, `on_accept` decodes this and, when a signature is present, verifies it against a payload that consists **only** of `(nonce, runtime_call)` — no chain id, no genesis hash, no destination `StateMachine`, no pallet identifier, no source contract address: [2](#0-1) 

The `nonce` used is simply `frame_system::Pallet::<T>::account_nonce(beneficiary)` on the destination chain at execution time: [3](#0-2) 

Because the beneficiary is derived purely from the `to` field of the ABI-encoded `Message` (fully attacker-controlled in a self-crafted `send` call), and because `ContractToAsset` authentication only gates *which asset* is credited — not the *content* of the signed payload — an attacker can:
1. Observe (from any public chain data / relayer traffic) a valid `SubstrateCalldata{signature, runtime_call}` that some victim previously signed and used in a legitimate cross-chain send to chain A (e.g., `Balances::transfer_allow_death` moving the victim's own funds).
2. Call `send` themselves toward chain B (any other chain configured with this pallet and a registered HFT contract), setting `recipient` = the victim's account bytes and `call_data` = the exact same signature+runtime_call bytes.
3. If the victim's `account_nonce` on chain B is still 0 (or otherwise matches the nonce baked into the reused signature — trivially true for any account that has never transacted on chain B, which is the common case for a bridge-only account), `on_accept` on chain B recomputes the identical `keccak256((nonce, runtime_call).encode())` digest, the signature check passes, and the `runtime_call` is dispatched with `RawOrigin::Signed(beneficiary)` on chain B — a chain and context the victim never authorized.

This mirrors the report's root cause exactly: the domain-binding value (there, `name` feeding the EIP-712 domain separator; here, chain identity/pallet id feeding the signed digest) is absent from what is actually checked, so a signature validated in one context is silently accepted in another.

### Impact Explanation
This allows **unauthorized execution** of a runtime call under a victim's signed authority on a chain/context they did not intend, satisfying the bounty's "unauthorized transaction or execution" and "replay ... double-settlement" categories. Since `runtime_call` is an arbitrary `T::RuntimeCall` (subject only to the base call filter, not to any binding with the specific bridge transfer or chain), the blast radius depends on what dispatchables are enabled in the destination runtime's call filter (e.g., `Balances::transfer_allow_death`, staking, governance voting, XCM-related calls) — potentially moving or misusing the victim's funds/authority on an unintended chain. No malicious relayer, prover, or admin is required: any ordinary user can perform the replay by calling the permissionless `send` extrinsic (or its EVM contract mirror) themselves.

### Likelihood Explanation
Requires only that (a) a user has previously used `call_data` with a signed `runtime_call` on one hyper-fungible-token deployment, and (b) the same beneficiary account nonce on another connected chain has not yet advanced past the value used in that signature — true by default for any freshly-created cross-chain-only account, which is the primary intended use case for this feature (asset transfer + calldata execution to a fresh/low-activity account). The attacker needs no privileged role; they only need to observe the previously broadcast signed payload (public request body) and submit their own ordinary `send` call. Likelihood is moderate-to-high for accounts whose nonce hasn't diverged across chains, and the feature is explicitly documented and tested (`should_receive_asset_with_calldata`) as production functionality.

### Recommendation
Bind the signed payload to the full execution context, analogous to fixing an EIP-712 domain separator to track the mutable field it's derived from:
- Include the destination `StateMachine` (or a chain id/genesis hash), the pallet's `PALLET_ID`, and ideally the source `StateMachine`/contract address in the hashed payload: `(chain_id_or_genesis, PALLET_ID, source, nonce, runtime_call).encode()`.
- Consider also binding the request `commitment`/nonce from the ISMP `PostRequest` itself, so a given signed authorization can only ever satisfy exactly one specific cross-chain message, not be replayed via a fresh, attacker-authored message.

### Proof of Concept
Extending the existing test `should_receive_asset_with_calldata` in `modules/pallets/testsuite/src/tests/pallet_hyper_fungible_token.rs` (lines 255-322) demonstrates the mechanism: [4](#0-3) 

1. Victim signs `payload = (0u64, runtime_call).encode()` and includes it (via `SubstrateCalldata`) in a `Message` sent, in the existing test, from `StateMachine::Evm(1)` to `StateMachine::Kusama(100)`.
2. `module.on_accept(post)` dispatches `runtime_call` as the victim's origin (as shown, `final_balance == SEND_AMOUNT`), and the existing test confirms same-chain replay is blocked only because the nonce was incremented (`module.on_accept(post)` a second time fails, as shown at the end of the test).
3. The gap: construct a **second, distinct** `PostRequest` (different `source`/`dest`, e.g. `StateMachine::Evm(2)` → `StateMachine::Kusama(200)`, or any other destination chain instance running this pallet with the same `beneficiary` at nonce 0) reusing the identical `substrate_data` bytes (same `signature`, same `runtime_call`). Because the verified digest `(nonce, runtime_call)` is unchanged and the beneficiary's nonce on that second chain is still 0, `on_accept` for the second message succeeds and dispatches the same `runtime_call` again under the victim's signed authority — despite it never being intended for that chain. This is not exercised by any existing test, which only checks same-chain nonce-based replay protection, not cross-chain/cross-context domain binding.

### Citations

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L292-302)
```rust
			// Encode the Message body
			let sender: [u8; 32] = who.clone().into();
			let amount: u128 = params.amount.into();
			let erc20_amount = convert_to_erc20(amount, erc_decimals, decimals);

			let token_message = Message {
				from: sender.to_vec().into(),
				to: params.recipient.to_vec().into(),
				amount: alloy_primitives::U256::from_be_bytes(erc20_amount.to_big_endian()),
				data: params.call_data.unwrap_or_default().into(),
			};
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

**File:** modules/pallets/testsuite/src/tests/pallet_hyper_fungible_token.rs (L280-320)
```rust
		// Sign with sr25519
		let (pair, ..) = sp_core::sr25519::Pair::generate();
		let beneficiary_bytes = pair.public().0;
		let payload = (0u64, runtime_call.clone()).encode();
		let message_hash = sp_io::hashing::keccak_256(&payload);
		let raw_signature = pair.sign(&message_hash);
		let multisignature = MultiSignature::Sr25519(raw_signature).encode();

		let substrate_data = SubstrateCalldata { signature: Some(multisignature), runtime_call };

		let module = HyperFungibleToken::default();
		let post = PostRequest {
			source: StateMachine::Evm(1),
			dest: StateMachine::Kusama(100),
			nonce: 0,
			from: hft_contract(),
			to: pallet_hyper_fungible_token::PALLET_ID.to_bytes(),
			timeout_timestamp: 1000,
			body: {
				let msg = Message {
					from: alloy_primitives::Bytes::from(vec![0x11u8; 20]),
					to: alloy_primitives::Bytes::from(beneficiary_bytes.to_vec()),
					amount: {
						let bytes = convert_to_erc20(SEND_AMOUNT, 18, 10).to_big_endian();
						alloy_primitives::U256::from_be_bytes(bytes)
					},
					data: alloy_primitives::Bytes::from(substrate_data.encode()),
				};
				Message::abi_encode(&msg)
			},
		};

		module.on_accept(post.clone()).unwrap();

		// The calldata transferred tokens from beneficiary to final_recipient
		let final_balance = pallet_balances::Pallet::<Test>::free_balance(final_recipient);
		assert_eq!(final_balance, SEND_AMOUNT);

		// Replay should fail (nonce incremented)
		let result = module.on_accept(post);
		assert!(result.is_err());
```
