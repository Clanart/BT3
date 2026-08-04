### Title
`message.from` inside the HFT cross-chain payload is trusted as the dispatch origin for arbitrary runtime calls, bypassing signature verification - ([File: modules/pallets/hyper-fungible-token/src/module.rs])

### Summary
`pallet-hyper-fungible-token`'s `on_accept` handler authenticates an incoming ISMP `PostRequest` only at the envelope level — it checks that `(source, from)` (the chain and the *sending contract's* address) is a registered `ContractToAsset` mapping. It never authenticates the `from` field embedded *inside* the ABI-decoded `Message` body. When the message carries optional calldata and no `signature` is attached, that inner, payload-controlled `message.from` is converted directly into the `RawOrigin::Signed` account used to dispatch an arbitrary `RuntimeCall` — exactly the "trust a user-controlled field for a privileged decision" pattern described in the external report (`owner_address` vs. authenticated sender).

### Finding Description
In `modules/pallets/hyper-fungible-token/src/module.rs::on_accept`: [1](#0-0) 

authenticates only that `source`/`from` (the contract that dispatched the ISMP `PostRequest`) is a known token contract: [2](#0-1) 

The `Message` struct decoded from `body` (`from`, `to`, `amount`, `data`) is entirely payload data chosen by whatever contract produced it — it is not part of the cryptographically-committed ISMP request metadata. Later, when calldata (`substrate_data.runtime_call`) is present but no per-call `signature` was supplied, the pallet derives the dispatch origin straight from this unauthenticated `message.from`: [3](#0-2) 

```rust
} else {
    let from_bytes = message.from.as_ref();
    if source.is_evm() {
        T::EvmToSubstrate::convert(H160::from_slice(&from_bytes[from_bytes.len() - 20..]))
    } else {
        let mut account = [0u8; 32];
        account.copy_from_slice(from_bytes);
        account.into()
    }
};
...
runtime_call.dispatch(RawOrigin::Signed(origin.clone()).into())
```

The audited reference EVM implementations always populate `from` as `abi.encodePacked(msg.sender)`: [4](#0-3) 

but this is only a convention of one particular sender implementation — nothing on the receiving pallet side enforces that `message.from` equals the actual caller who triggered the send. `ContractToAsset` authenticates the *contract address* that dispatched the request, not the value the contract chose to write into the `from` field of its own message body. Any registered token-contract deployment (and the HFT model is explicitly designed to be permissionless — "each token is its own bridge application, with no governance overhead, no shared custody pool, and no token-governor") can populate `message.from` with an arbitrary 20/32-byte value instead of the true caller. Combined with the no-signature branch, this lets whoever controls that contract's send path choose the account under which an arbitrary `RuntimeCall` is dispatched.

This is a structural analog of the reported bug: a field inside a message payload (`owner_address` in the TON pool / `message.from` here) is trusted for an authorization decision (lock bypass / dispatch origin) instead of a value bound to the authenticated sender.

### Impact Explanation
If an attacker controls (or can register) a contract that satisfies `ContractToAsset`, they can craft a `Message` whose `data` contains a `SubstrateCalldata` with a `runtime_call` and no `signature`. The pallet will dispatch that call as `RawOrigin::Signed(message.from)` for any `message.from` the attacker chooses (bounded by `T::BaseCallFilter`), rather than requiring proof of control over that account. This is unauthorized execution / potential fund theft: the dispatched call runs with the privileges of whatever account the attacker names, not the true message originator.

### Likelihood Explanation
The severity is gated by exactly how `ContractToAsset` entries get populated (permissioned vs. permissionless per-asset registration) — this could not be fully confirmed from the code surfaced in this session, since the registration/`add_supported_chain`-style extrinsic in `pallet-hyper-fungible-token` was not located. If registration is permissionless (consistent with the documented "no governance overhead" design), likelihood is high, since any attacker can stand up their own compliant EVM contract. If registration is gated behind a privileged origin, exploitation would additionally require a governance-approved but buggy/malicious contract, lowering likelihood. This uncertainty should be resolved by inspecting the pallet's registration extrinsic before treating this as confirmed-exploitable.

### Recommendation
Never derive a dispatch origin from `message.from` (payload data). Require a valid signature for every runtime-call execution path, or, if a signature-less path must exist, restrict the origin to a value cryptographically derived from the authenticated envelope (e.g., `PostRequest.from`/`source`, or a per-message commitment verifiable independent of the sender-supplied body), consistent with the report's recommendation to key access control off authenticated sender data rather than user-supplied payload fields.

### Proof of Concept
1. Attacker registers (or already controls) a `HyperFungibleToken`-compatible contract instance that is a valid entry in `ContractToAsset` for some `(source, from)` pair.
2. Attacker's contract dispatches an ISMP `PostRequest` whose ABI-encoded `Message.data` contains `SubstrateCalldata { signature: None, runtime_call: <arbitrary call, e.g. Balances::transfer_all to attacker> }`, and sets `Message.from` to the bytes of a victim/target account (e.g. a well-funded pallet sovereign account or any substrate/EVM address the attacker wants to impersonate).
3. On delivery, `on_accept` passes the `ContractToAsset` check (attacker's contract is legitimately registered), decodes the message, sees no signature, and computes `origin = T::EvmToSubstrate::convert(H160::from(message.from))` (or the 32-byte substrate variant).
4. `runtime_call.dispatch(RawOrigin::Signed(origin))` executes the attacker's chosen call as the impersonated account, per [5](#0-4) , with no proof that the impersonated account authorized anything.

### Citations

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L50-59)
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
```

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L176-200)
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
