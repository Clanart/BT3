## Analysis

The M‑12 report's core broken invariant is: **an atomic, all‑or‑nothing composition of independent sub‑operations, where one sub‑operation's success depends on a mutable, front‑runnable value (a nonce), lets an attacker cheaply invalidate the whole composed unit and destroy value/fees that belonged to the unrelated parts of the batch.**

The local analog is in `pallet-ismp`'s unsigned message-batch extrinsic combined with `hyper-fungible-token`'s `on_accept` handler, which optionally executes a nonce-signed runtime call.

### Title
Nonce-gated calldata in `HyperFungibleToken::on_accept` lets an attacker abort an entire `handle_unsigned` message batch, reverting unrelated mints/settlements - (File: modules/pallets/ismp/src/impls.rs)

### Summary
`pallet-ismp`'s `handle_unsigned` extrinsic is `#[frame_support::transactional]` and processes an entire relayer-submitted `Vec<Message>` atomically [1](#0-0) . Inside `Pallet::execute`, the per-message events from every request in the batch are flattened and collected with `collect::<Result<Vec<_>, _>>()` — if **any single event from any single message in the batch is an `Err`, the whole extrinsic fails** with `Error::<T>::InvalidMessage` [2](#0-1) . Because the call is transactional, this rolls back every other, unrelated message's state changes that were already applied earlier in the same batch (mints, transfers, consensus updates), and also skips fee payment to the relayer via `T::FeeHandler::on_executed`, which only runs after the collect succeeds [3](#0-2) .

`modules/ismp/core/src/handlers/request.rs::handle` itself does not fail the batch early on a per-request callback error — it captures the `on_accept` result as an `Err` inside the events vector for later `?`-propagation at the top level [4](#0-3) . This means the top-level `collect` in `impls.rs` is the sole atomicity boundary, and it spans the *entire batch*, not a single request.

`HyperFungibleToken::on_accept` provides a concrete, externally-triggerable way to make one message's `on_accept` fail *after* it has already minted/transferred tokens to a beneficiary: if the message body carries optional calldata (`SubstrateCalldata`) with a signature, that signature is checked against the **beneficiary account's current live nonce** (`frame_system::Pallet::<T>::account_nonce(beneficiary)`) [5](#0-4) . Any ordinary, unprivileged transaction submitted by (or on behalf of) that beneficiary account before the cross-chain message lands increments the account nonce, causing `SignatureVerificationFailed` (or a similar dispatch/decode error) once the message is finally relayed [6](#0-5) . This is exactly the "permission denied" / nonce-consumption griefing primitive described in the M-12 report, just replayed against a Substrate account nonce instead of an ERC-2612 permit nonce.

### Finding Description
1. A relayer batches several pending ISMP `Message`s (potentially from many unrelated users/apps and unrelated destinations) into one `handle_unsigned(messages)` extrinsic call for gas efficiency — this is the intended, permissionless relaying pattern (mirrored on the EVM side by `HandlerV2.batchCall`, which is explicitly documented as "atomic: any failure reverts the entire batch" [7](#0-6) ).
2. One of the batched messages targets `HyperFungibleToken`, carrying `message.data` with a `SubstrateCalldata` payload signed by the beneficiary over `(current_nonce, runtime_call)`.
3. `on_accept` first performs the token mint/transfer to the beneficiary, **then** verifies the embedded signature against the beneficiary's *live* nonce and dispatches the call [8](#0-7) .
4. An unprivileged attacker (or even the beneficiary's own unrelated wallet activity) submits any ordinary signed extrinsic from the beneficiary account before the cross-chain message is relayed, bumping `account_nonce`. When the batched message is finally processed, the nonce used at signing time no longer matches, so verification fails and `on_accept` returns `Err(HftError::SignatureVerificationFailed)`.
5. This `Err` surfaces as one failed event inside the flattened event vector in `Pallet::execute`, which fails the whole `collect::<Result<Vec<_>, _>>()` and returns `Error::<T>::InvalidMessage` for the **entire extrinsic** [9](#0-8) .
6. Because `handle_unsigned` is `#[frame_support::transactional]`, the failure rolls back *every* state change performed for *every other message* batched in that same call — including unrelated mints/transfers to other beneficiaries and any consensus-state updates riding along in the batch — and no relayer fee is paid for any of them.

Existing guards do not stop this: there is no per-message isolation/try-catch around `on_accept` at the pallet-ismp batch level (unlike the EVM `EvmHost.dispatchIncoming`, which explicitly catches failures with a low-level `.call` and lets other requests continue [10](#0-9) ). The Substrate path instead propagates any single request's failure into a hard, transactional revert of the whole batch, exactly the antipattern the M-12 report warns against for composed, front-runnable, nonce-checked sub-operations.

### Impact Explanation
An unprivileged attacker who can predict (or simply routinely transacts from) an account that will be the beneficiary of a signed-calldata cross-chain mint can, by submitting a cheap ordinary transaction, invalidate the nonce check and cause the entire relayer batch containing that message to revert. This:
- Blocks/delays settlement (mint/transfer) for every *other, unrelated* user whose message happened to be batched alongside the targeted one — a logic/availability attack on cross-chain fund delivery, not merely a gas-griefing nuisance, since it forces re-relay and repeated exposure to the same griefing vector.
- Denies relayer fee payment for the whole batch (`FeeHandler::on_executed` never runs on failure), directly costing relayers real value delivered on-chain for otherwise-successful work.
- Can be repeated cheaply and indefinitely against any batch containing a signed-calldata HFT message, since the attacker only needs to touch their own account nonce — no relayer/prover/admin compromise required.

### Likelihood Explanation
High. No special privileges, no compromised infrastructure, and no front-running of the relayer's transaction itself are needed — the attacker merely needs an ordinary transaction from the targeted beneficiary account to land before the cross-chain message is processed, which is trivial to arrange (self-grief or third-party grief against a known/observable beneficiary address, since account activity and pending ISMP messages are both publicly observable).

### Recommendation
- Do not allow a single request's `on_accept`/callback failure to abort the whole batch's transactional scope in `Pallet::execute`. Isolate each message's storage effects (e.g., wrap each `handle_incoming_message` call in its own `with_transaction`/`storage::transaction` boundary) so that one request's failure only rolls back that request, matching the documented "no partial state changes but no cross-message contamination" model already used in `IsmpModule::on_accept` per the ISMP request-handling docs (which describes per-request rollback via `delete_request_receipt`, not whole-batch rollback) [11](#0-10) .
- In `HyperFungibleToken::on_accept`, do not tie signature verification to a live, externally-mutable nonce that any third party can bump. Bind the signature to a value fixed at message-creation time on the source chain (e.g., the ISMP request's own `nonce`/commitment) instead of `frame_system::account_nonce`, removing the front-runnable dependency entirely.
- Alternatively/in addition, execute the optional calldata via a try/catch-style dispatch (capturing failure without propagating an `Err` from `on_accept`) so a bad/stale signature only skips the extra call while the token mint/transfer still finalizes, consistent with how EVM's `HyperFungibleToken(Upgradeable).onAccept` treats `ICallDispatcher.dispatch` as best-effort after the mint [12](#0-11) .

### Proof of Concept
1. Relayer observes N pending ISMP `PostRequest`s ready for delivery and submits `handle_unsigned([msg_1, ..., msg_k, ..., msg_N])` where `msg_k` targets `HyperFungibleToken` and carries `message.data` = signed `SubstrateCalldata` for beneficiary `B`, signed over `(nonce_at_signing, runtime_call)`.
2. Before the relayer's extrinsic is included, attacker (or anyone) submits any ordinary signed extrinsic from account `B` (or triggers any other action that increments `B`'s `frame_system` nonce).
3. The relayer's `handle_unsigned` extrinsic executes: `on_accept` for `msg_k` mints/transfers tokens to `B`, then fetches `account_nonce(B)`, which no longer equals `nonce_at_signing`; signature verification fails and `HftError::SignatureVerificationFailed` is returned.
4. `Pallet::execute`'s `collect::<Result<Vec<_>, _>>()` over the flattened events for *all* `N` messages hits this `Err` and returns `Error::<T>::InvalidMessage` for the whole call.
5. Because `handle_unsigned` is `#[frame_support::transactional]`, the mints/transfers for `msg_1..msg_{k-1}` and `msg_{k+1}..msg_N` (unrelated users) are rolled back, and the relayer receives no fee for any of the N messages, despite most of them having no defect at all.

### Citations

**File:** modules/pallets/ismp/src/lib.rs (L370-382)
```rust
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

**File:** modules/pallets/ismp/src/impls.rs (L43-76)
```rust
		let message_results = messages
			.iter()
			.map(|msg| handle_incoming_message(&host, msg.clone()))
			.collect::<Result<Vec<_>, _>>()
			.map_err(|err| {
				log::debug!(target: "ismp", "Handling Error {:#?}", err);
				Pallet::<T>::deposit_event(Event::<T>::Errors { errors: vec![err.into()] });
				Error::<T>::InvalidMessage
			})?;

		let messages_with_weights = message_results
			.iter()
			.zip(messages)
			.map(|(result, message)| MessageWithWeight { message, weight: result.weight() })
			.collect::<Vec<_>>();

		let events = message_results
			.into_iter()
			// check that requests will be successfully dispatched
			// so we can not be spammed with failing txs
			.map(|result| match result {
				MessageResult::Request { events, .. } |
				MessageResult::Response { events, .. } |
				MessageResult::Timeout { events, .. } => events,
				MessageResult::ConsensusMessage(events) => events.into_iter().map(Ok).collect(),
				MessageResult::FrozenClient(_) => vec![],
			})
			.flatten()
			.collect::<Result<Vec<_>, _>>()
			.map_err(|err| {
				log::debug!(target: "ismp", "Handling Error {:#?}", err);
				Pallet::<T>::deposit_event(Event::<T>::Errors { errors: vec![err.into()] });
				Error::<T>::InvalidMessage
			})?;
```

**File:** modules/pallets/ismp/src/impls.rs (L78-79)
```rust
		T::FeeHandler::on_executed(messages_with_weights, events.clone())
			.map_err(|_| Error::<T>::ErrorChargingFee)?;
```

**File:** modules/ismp/core/src/handlers/request.rs (L99-132)
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
				let res = cb.on_accept(request.clone()).map(|weight| {
					total_weights.saturating_accrue(weight);

					let commitment = hash_request::<H>(&wrapped_req);
					Event::PostRequestHandled(RequestResponseHandled {
						commitment,
						relayer: signer,
					})
				});
				// Delete receipt if module callback failed so it can be timed out
				if res.is_err() {
					host.delete_request_receipt(&wrapped_req)?;
				}
				Ok(res)
			};

			let res = lambda().and_then(|res| res);
			res
		})
		.collect::<Vec<_>>();
```

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L93-202)
```rust
		// Mint or transfer to beneficiary
		if local_asset_id == T::NativeAssetId::get() {
			<T as Config>::NativeCurrency::transfer(
				&Pallet::<T>::pallet_account(),
				&beneficiary,
				amount,
				ExistenceRequirement::AllowDeath,
			)
			.map_err(|e| HftError::TransferFailed(e.into()))?;
		} else {
			let is_native = NativeAssets::<T>::get(local_asset_id.clone());
			if is_native {
				<T as Config>::Assets::transfer(
					local_asset_id,
					&Pallet::<T>::pallet_account(),
					&beneficiary,
					amount.into(),
					Preservation::Expendable,
				)
				.map_err(|e| HftError::TransferFailed(e.into()))?;
			} else {
				<T as Config>::Assets::mint_into(local_asset_id, &beneficiary, amount.into())
					.map_err(|e| HftError::MintFailed(e.into()))?;
			}
		}

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
```

**File:** evm/src/core/HandlerV2.sol (L123-135)
```text
    /**
     * @dev Process a batch of encoded handler calls in a single transaction.
     * Uses delegatecall to self so msg.sender is preserved and storage writes
     * happen in this contract's context. Atomic, any failure reverts the entire batch.
     * @param calls - array of ABI-encoded handler function calls
     */
    function batchCall(bytes[] memory calls) external {
        uint256 len = calls.length;
        for (uint256 i = 0; i < len; ++i) {
            (bool success, bytes memory returnData) = address(this).delegatecall(calls[i]);
            if (!success) revert BatchCallFailed(i, returnData);
        }
    }
```

**File:** evm/src/core/EvmHost.sol (L794-818)
```text
    function dispatchIncoming(PostRequest memory request, address relayer) external restrict(_hostParams.handler) {
        address destination = _bytesToAddress(request.to);
        uint256 size;
        assembly {
            size := extcodesize(destination)
        }
        if (size == 0) {
            // instead of reverting the entire batch, early return here.
            return;
        }

        // replay protection
        bytes32 commitment = request.hash();
        _requestReceipts[commitment] = relayer;

        (bool success,) = address(destination)
            .call(abi.encodeWithSelector(IApp.onAccept.selector, IncomingPostRequest(request, relayer)));

        if (!success) {
            // so that it can be retried
            delete _requestReceipts[commitment];
            return;
        }
        emit PostRequestHandled({commitment: commitment, relayer: relayer});
    }
```

**File:** sdk/packages/core/contracts/apps/HyperFungibleToken.sol (L301-312)
```text

        if (message.data.length > 0) {
            ICallDispatcher(_dispatcher).dispatch(message.data);
        }

        emit Received({
            from: message.from,
            to: beneficiary,
            source: string(request.source),
            amount: message.amount
        });
    }
```
