Found the analog: `IntentGatewayV2._select` (`evm/src/apps/intentsv2/IntentsBase.sol` L500-512), backing `select()` in `evm/src/apps/IntentGatewayV2.sol` L392-394, mirrors the H-07 pattern almost exactly — a signed message that authorizes a privileged action but carries no expiry/deadline and no consumable nonce.

### Title
Indefinitely-replayable `SolverSelection` signature lets a stale session-key authorization be reused after order re-creation — `IntentGatewayV2` (`evm/src/apps/intentsv2/IntentsBase.sol`)

### Summary
`_select()` verifies an EIP-712 `SolverSelection(commitment, solver)` signature and stores `tstore(commitment, keccak256(solver, sessionKey))`, gating `fillOrder`'s solver-authorization check. [1](#0-0) 
The signed struct hash is only `(commitment, solver)` — no deadline, no nonce, no binding to order state (e.g. `_filled[commitment]`). [2](#0-1) 

### Finding Description
`commitment = keccak256(abi.encode(order))` is a pure hash of order fields, so it is fully deterministic and reproducible by anyone who knows the order data — a user (or a script) can trivially construct/re-place an order with identical fields (same `user`, `source`, `destination`, `deadline` if not yet passed by the time of reuse, `nonce`, `fees`, `session`, `predispatch`, `inputs`, `output`) to reproduce the exact same `commitment`. Because `_select` binds the signature only to `(commitment, solver)` with no deadline or one-time nonce, a `SolverSelection` signature produced by a session key for one order instance remains valid forever for any future order that hashes to the same commitment. `select()` is a public, unprivileged entrypoint — anyone can call it with an old signature. [3](#0-2) 

Unlike `withdraw()` and `accumulate_fees` in the relayer pallet, which explicitly bind their signed payloads to a monotonically-incrementing per-signer `Nonce` to guarantee single-use (the exact mitigation recommended for H-07), [4](#0-3) [5](#0-4) 
the `SolverSelection` signature has no such consumption mechanism — replay protection relies solely on `tstore`/transient-storage scoping within a single transaction and on `_filled[commitment]` being unset, not on invalidating the signature itself.

### Impact Explanation
If a user's session key ever signs a `SolverSelection` for a given solver (e.g. during a normal bidding flow that is later abandoned, cancelled, or that the user recreates with the same parameters after a cancel/refund cycle), that signature authorizes the same solver to satisfy `fillOrder`'s authorization check on any future order that reproduces the same commitment — without any fresh consent from the user. Given `solverSelection` mode exists specifically to prevent "unauthorized fills" by restricting who may fill an order, this undermines that guarantee: a solver holding a previously-issued signature can force selection on a re-submitted/logically-equivalent order the user did not intend to authorize that solver for again.

### Likelihood Explanation
Requires the attacker (an unprivileged solver or any caller) to already possess a previously-signed `SolverSelection` and for the exact same order fields to be reused later (e.g. resubmission after cancellation, an SDK/relayer replaying an order template, or a user re-issuing an identical order). This is a narrower trigger than H-07's "operator always has infinite allowance" scenario, but it is directly analogous: no deadline, no nonce, purely commitment-hash-bound signature, and the entrypoint (`select`) is fully public and unprivileged.

### Recommendation
Bind the `SolverSelection` typehash to a deadline and/or a per-session-key nonce (mirroring `pallet-relayer`'s `Nonce` design), and additionally bind it against order-specific unique state (e.g. require `_filled[commitment] == address(0)` be checked prior to accepting `select`, which it already implicitly is via `fillOrder`, but also invalidate/consume the *selection* signature itself, e.g. by using `tstore` plus a permanent per-commitment "selected" flag or by mixing block context/expiry into the signed struct) so a signature cannot be reused across independently re-created orders.

### Proof of Concept
1. User creates session key `S`, places order `O1` with fields `F`, computes `commitment = keccak256(abi.encode(O1))`.
2. User has `S` sign `SolverSelection(commitment, solverA)` and gives it to `solverA` (normal bidding flow).
3. Order `O1` is cancelled/expires/never filled; escrow is refunded.
4. Later, the user (or anyone constructing an order with identical field values `F`, including the same `session = S`) places a new order `O2` with the same encoded fields, producing the identical `commitment`.
5. `solverA` calls `select(SelectOptions{commitment, solver: solverA, signature})` reusing the old signature from step 2 — `_select` recovers `S` and stores the selection in transient storage, exactly as before, because nothing in the signed payload changed or expired. [1](#0-0) 
6. `solverA` then calls `fillOrder(O2, options)`; the `Unauthorized()` check passes against the stale selection, letting `solverA` fill `O2` even though the user never re-authorized them for this instance. [6](#0-5) 

**Confidence note:** I could not directly confirm within the index whether the SDK/off-chain order-placement flow ever produces field-identical orders in practice (this would need verification in a live Devin session against `sdk/packages/sdk` order-construction code), so the practical reachability of "identical order recreation" should be validated further; the on-chain contract logic itself, however, provides no independent safeguard against it.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L500-512)
```text
    function _select(SelectOptions calldata options) internal returns (address) {
        bytes32 structHash = keccak256(abi.encode(SELECT_SOLVER_TYPEHASH, options.commitment, options.solver));
        bytes32 digest = _hashTypedDataV4(structHash);
        address sessionKey = ECDSA.recover(digest, options.signature);

        bytes32 commitment = options.commitment;
        bytes32 selectionHash = keccak256(abi.encode(options.solver, sessionKey));
        assembly {
            tstore(commitment, selectionHash)
        }

        return sessionKey;
    }
```

**File:** evm/src/apps/IntentGatewayV2.sol (L392-394)
```text
    function select(SelectOptions calldata options) public returns (address) {
        return _select(options);
    }
```

**File:** evm/src/apps/IntentGatewayV2.sol (L428-436)
```text
        if (_params.solverSelection) {
            bytes32 storedSelectionHash;
            assembly {
                storedSelectionHash := tload(commitment)
            }

            bytes32 expectedSelectionHash = keccak256(abi.encode(msg.sender, order.session));
            if (storedSelectionHash != expectedSelectionHash) revert Unauthorized();
        }
```

**File:** modules/pallets/relayer/src/withdrawal.rs (L88-131)
```rust
		let nonce = Nonce::<T>::get(address.clone(), withdrawal_data.dest_chain);
		let msg = message(nonce, withdrawal_data.dest_chain, withdrawal_data.beneficiary.clone());

		match &withdrawal_data.signature {
			Signature::Evm { address, .. } => {
				let eth_address = withdrawal_data
					.signature
					.verify(&msg, None)
					.map_err(|_| Error::<T>::InvalidSignature)?;
				if &eth_address != address {
					Err(Error::<T>::InvalidPublicKey)?
				}
			},
			Signature::Sr25519 { .. } => {
				// Verify signature with public key provided in signature enum
				withdrawal_data
					.signature
					.verify(&msg, None)
					.map_err(|_| Error::<T>::InvalidSignature)?;
			},
			Signature::Ed25519 { .. } => {
				// Verify signature with public key provided in signature enum
				withdrawal_data
					.signature
					.verify(&msg, None)
					.map_err(|_| Error::<T>::InvalidSignature)?;
			},
		};
		let available_amount = Fees::<T>::get(withdrawal_data.dest_chain, address.clone());

		if available_amount <
			Self::min_withdrawal_amount(withdrawal_data.dest_chain)
				.unwrap_or(MinWithdrawal::get())
		{
			Err(Error::<T>::NotEnoughBalance)?
		}

		let dispatcher = <T as Config>::IsmpHost::default();

		Nonce::<T>::try_mutate(address.clone(), withdrawal_data.dest_chain, |value| {
			*value += 1;
			Ok::<(), ()>(())
		})
		.map_err(|_| Error::<T>::ErrorCompletingCall)?;
```

**File:** modules/pallets/relayer/src/accumulate.rs (L107-132)
```rust
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

			Nonce::<T>::try_mutate(&delivery_address, state_machine, |value| {
				*value += 1;
				Ok::<(), ()>(())
			})
			.map_err(|_: ()| Error::<T>::ErrorCompletingCall)?;
```
