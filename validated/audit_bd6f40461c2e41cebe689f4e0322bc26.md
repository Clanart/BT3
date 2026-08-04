## Title
Cross-function signature replay lets an attacker force `withdraw()` to drain a relayer's accumulated fees using a signature the relayer only authorized for `accumulate()`'s beneficiary redirect - ([File: modules/pallets/relayer/src/withdrawal.rs] and [File: modules/pallets/relayer/src/accumulate.rs])

### Summary
`pallet-relayer` authorizes two very different privileged actions — `withdraw()` (dispatch the relayer's *entire currently accumulated* fee balance to a beneficiary) and `accumulate()`'s optional beneficiary redirect (credit a *specific delivery's* fee to a beneficiary instead of the delivering relayer) — using two "different" signed payload constructors that are byte-for-byte identical and share the same replay-protection nonce counter. A signature a relayer creates to authorize the low-stakes accumulate-time redirect is therefore also a valid, fully-formed authorization for the high-stakes `withdraw()` call, and vice versa.

### Finding Description
`withdrawal.rs::message()` builds the signed payload for `withdraw()`: [1](#0-0) 

`accumulate.rs::beneficiary_message()` builds the signed payload for the beneficiary redirect option inside `accumulate()`: [2](#0-1) 

Both functions hash the SCALE-encoding of `(nonce: u64, state_machine: StateMachine, beneficiary: bytes)`. `Encode` for `Vec<u8>` and `&[u8]` produce identical length-prefixed byte sequences, so `message(nonce, chain, Some(beneficiary))` and `beneficiary_message(nonce, chain, &beneficiary)` are the exact same 32-byte digest for the same `(nonce, chain, beneficiary)` triple.

Both consumers also key their replay-protection nonce from the *same* storage map, `crate::Nonce<T>`, indexed by `(address, state_machine)`:
- `withdraw()` reads/increments `Nonce::<T>::get(address, dest_chain)` before dispatching the payout: [3](#0-2) 
- `accumulate()`'s beneficiary branch reads/increments the identical `Nonce::<T>::get(&delivery_address, state_machine)`: [4](#0-3) 

Because the message format and nonce namespace are identical, there is no domain separator (no call-specific tag/discriminant) distinguishing "authorize a beneficiary override for one delivery's fee credit" from "authorize an immediate withdrawal of my entire accumulated fee balance." Both extrinsics accept signatures from anyone (unsigned/permissionless submission, with the on-chain authorization coming solely from the embedded cryptographic signature), matching this Hyperbridge repo's existing pattern of unsigned+signature-authorized calls documented elsewhere (e.g. `claim_outbound_request_delivery_reward` in `outbound_request.rs`, which is explicitly `ensure_none`).

This is the direct analog of the report's core flaw: an authorization artifact produced for one narrow, low-impact intent is accepted, without additional binding, as authorization for a broader, higher-impact action performed by a different code path — exactly like `tx.origin` being reused to authorize an unrelated 0x order in the original TradeCallee bug.

### Impact Explanation
A relayer who signs a `beneficiary_message(nonce, dest_chain, beneficiary)` — intending only to redirect the fee credit from a *specific pending delivery* being accumulated via `accumulate()` — has unknowingly produced a fully valid `withdrawal_data.signature` for `withdraw()`. Anyone who observes that signature (it must be broadcast on-chain or in the mempool to reach `accumulate()`) can front-run it into `withdraw()` with the same `(dest_chain, beneficiary)` pair. Since `withdraw()` pays out `Fees::<T>::get(dest_chain, address)` — the relayer's entire currently accumulated balance for that chain, not just the amount tied to the delivery the relayer meant to redirect — this can force disbursement of the relayer's full fee balance to the specified beneficiary and simultaneously burn the nonce, preventing the relayer's originally intended `accumulate()` beneficiary redirect from succeeding (or vice versa: a `withdraw()`-intended signature could be replayed into `accumulate()` to override fee crediting for deliveries the relayer never meant to redirect). This is unauthorized execution / fund redirection through legitimate-looking authorization data reused outside its intended scope — squarely inside the bounty's "unauthorized transaction," "logic attack," and "false proof/state acceptance"-adjacent categories (state mutation authorized under a different premise than the signer intended).

### Likelihood Explanation
No admin, governance, relayer-compromise, or malicious-peer assumption is required beyond the relayer's own normal act of signing a beneficiary-redirect message and broadcasting it (which is inherent to how `accumulate()`'s permissionless/unsigned submission model works — the signed payload must be public to reach the chain). Any observer of that payload can submit it to the sibling extrinsic. The bug is triggered purely by existing, intended protocol usage patterns (a relayer using the documented beneficiary-redirect feature) colliding with the sibling function's identical signature scheme — no cryptographic break, no privileged access, no front-run-only speculative assumption beyond normal mempool visibility of a valid, publicly-submittable payload.

### Recommendation
Add a call-specific domain separator/discriminant byte (or distinct struct tag) into both `message()` and `beneficiary_message()` before hashing, e.g. `keccak256((b"withdraw", nonce, dest_chain, beneficiary))` vs `keccak256((b"accumulate_beneficiary", nonce, state_machine, beneficiary))`, and/or use separate nonce namespaces per call so a signature valid for one extrinsic can never be replayed as valid input to the other.

### Proof of Concept
1. Relayer R has accrued `Fees[dest_chain][R] = 100` from prior deliveries and is about to submit a delivery proof via `accumulate()`, wanting to redirect only this delivery's new fee credit to beneficiary `B`. R signs `beneficiary_message(nonce=N, dest_chain, B)` and forms the `accumulate` extrinsic with `beneficiary_details = Some((B, sig))`.
2. Before R's `accumulate()` transaction lands, an observer extracts `sig` from the mempool/broadcast payload and submits `withdraw(WithdrawalInputData { signature: sig, dest_chain, beneficiary: Some(B) })`.
3. In `withdraw()`, `message(N, dest_chain, Some(B))` hashes identically to `beneficiary_message(N, dest_chain, B)`, so signature verification against R's recovered address succeeds. `withdraw()` reads `available_amount = Fees[dest_chain][R] = 100`, dispatches disbursement of the full 100 to `B`, zeroes `Fees[dest_chain][R]`, and increments `Nonce(R, dest_chain)` to `N+1`.
4. R's original `accumulate()` call, still carrying nonce `N` in its signature, now fails signature verification (nonce mismatch) or, if resubmitted, no longer represents R's intended state — R has lost their full 100-fee balance to `B` via a call they never authorized, triggered solely by reuse of a signature meant for a different, narrower purpose. [1](#0-0) [2](#0-1) [3](#0-2) [5](#0-4)

### Citations

**File:** modules/pallets/relayer/src/withdrawal.rs (L81-133)
```rust
	pub fn withdraw(withdrawal_data: WithdrawalInputData) -> DispatchResult {
		let address = match &withdrawal_data.signature {
			Signature::Evm { address, .. } => address.clone(),
			Signature::Sr25519 { public_key, .. } => public_key.clone(),
			Signature::Ed25519 { public_key, .. } => public_key.clone(),
		};

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

		let beneficiary_address = withdrawal_data.beneficiary.clone().unwrap_or(address.clone());
```

**File:** modules/pallets/relayer/src/withdrawal.rs (L192-197)
```rust
pub fn message(nonce: u64, dest_chain: StateMachine, beneficiary: Option<Vec<u8>>) -> [u8; 32] {
	if let Some(beneficiary) = beneficiary {
		return sp_io::hashing::keccak_256(&(nonce, dest_chain, beneficiary).encode());
	}
	sp_io::hashing::keccak_256(&(nonce, dest_chain).encode())
}
```

**File:** modules/pallets/relayer/src/accumulate.rs (L106-147)
```rust
		// Let's verify the beneficiary address
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

			let _ = Fees::<T>::try_mutate(state_machine, beneficiary_address.clone(), |inner| {
				*inner += total_fee;
				Ok::<(), ()>(())
			});

			beneficiary_address
		} else {
			let _ = Fees::<T>::try_mutate(state_machine, delivery_address.clone(), |inner| {
				*inner += total_fee;
				Ok::<(), ()>(())
			});

			delivery_address
		};
```

**File:** modules/pallets/relayer/src/accumulate.rs (L309-315)
```rust
pub fn beneficiary_message(
	nonce: u64,
	state_machine: StateMachine,
	beneficiary: &[u8],
) -> [u8; 32] {
	sp_io::hashing::keccak_256(&(nonce, state_machine, beneficiary).encode())
}
```
