### Title
Beneficiary-redirect signature in `pallet-ismp-relayer` is not bound to specific commitments/amount, allowing stale authorization reuse - (File: modules/pallets/relayer/src/accumulate.rs)

### Summary
`pallet_ismp_relayer::accumulate` lets anyone submit a `WithdrawalProof` with an optional `beneficiary_details` field — a `(beneficiary_address, Signature)` pair produced by the relayer that delivered the request. The signed payload is only `keccak256(nonce, state_machine, beneficiary)`; it never binds the specific request commitments or fee amount that the relayer intended to redirect. Because the call is dispatched with `RuntimeOrigin::none()` (permissionless/unsigned), and the `Nonce` used to prevent replay is only bumped when a beneficiary-redirect signature is actually consumed (not when fees are accumulated normally), a relayer's redirect signature created for one batch of commitments remains valid and reusable for any later, unrelated batch of that relayer's commitments until the nonce is consumed — closely mirroring the Farcaster `IdRegistry` bug where a signature meant for one specific state transition wasn't invalidated when the signer's intended context changed.

### Finding Description
In `accumulate()`, the delivery/relayer address is recovered from the destination state proof, then optionally redirected to a beneficiary if a valid signature is supplied: [1](#0-0) 

The signed message is produced by `beneficiary_message`, which commits only to `(nonce, state_machine, beneficiary)` — not to the commitments being claimed nor the fee amount: [2](#0-1) 

The `Nonce` map is keyed by `(delivery_address, state_machine)` and is only incremented when a beneficiary signature is actually verified and consumed in this code path: [3](#0-2) 

Fees accumulated *without* a beneficiary signature (the normal, direct-to-relayer path) go through the `else` branch and never touch `Nonce`: [4](#0-3) 

The `accumulate_fees` extrinsic is dispatched permissionlessly (`RuntimeOrigin::none()` in the test-suite, confirming any caller can submit a `WithdrawalProof`), so anyone holding a previously produced signature can invoke it: [5](#0-4) 

This reproduces the exact broken invariant from the external report: a signature authorizing a state change (here, "redirect my currently-accrued fee batch to Bob") is not scoped to the specific state/context it was created for (a particular batch/amount), and the signer's later, unrelated actions (accumulating more fees directly to themselves via the non-redirect path) do not invalidate or consume the outstanding signature. Consequently, that stale signature can be replayed by anyone against an entirely different, later batch of the same relayer's commitments, redirecting fees the relayer never intended to send to that beneficiary.

### Impact Explanation
This allows unauthorized redirection of relayer fee earnings to a beneficiary address the relayer did not (at the time of the second batch) intend to pay, i.e., unauthorized transaction/fund redirection and wrong-beneficiary settlement — directly matching the bounty's "stealing or loss of funds" / "unauthorized transaction or execution" / "logic attack" categories. A relayer that signed a single beneficiary-redirect message (e.g., to redirect a specific small batch's fee to a payment processor or backup wallet) has no way to limit that authorization to just that batch; as long as `Nonce` for that `(address, state_machine)` pair has not advanced, the signature remains a standing authorization over any future commitments delivered by that same relayer on that chain.

### Likelihood Explanation
The attack requires no privileged role, malicious relayer/prover, or compromised keys beyond the fact that the "beneficiary" (or anyone who obtains the previously-published signature, e.g. from a prior on-chain/mempool submission or from the beneficiary party itself) can call the permissionless `accumulate_fees` extrinsic with a fresh, valid state proof for new commitments delivered by the same relayer, reusing the old signature. This is an unprivileged, publicly reachable entrypoint (`accumulate` / `accumulate_fees`), making the likelihood realistic wherever a relayer uses beneficiary redirection more than once without immediately exhausting or rotating its nonce.

### Recommendation
Bind the signed payload to the specific claim it authorizes rather than just `(nonce, state_machine, beneficiary)`. Include a commitment to the exact set of request commitments (or a hash of `withdrawal_proof.commitments`) and/or the resulting fee amount in `beneficiary_message`, so that a signature can only be replayed for the exact batch it was created for — analogous to the client's fix of folding the "current recovery address" into `CHANGE_RECOVERY_ADDRESS_TYPEHASH` so a stale signature is automatically invalidated once context changes.

### Proof of Concept
1. Relayer Alice delivers commitments `[A]` and creates `beneficiary_message(nonce=0, chain, Bob)`, signs it, intending to redirect only the fee from `[A]` to Bob.
2. Before Alice submits the redirect, Alice's relaying activity accrues additional fees from commitments `[B, C]` via ordinary `accumulate()` calls without `beneficiary_details` (the `else` branch) — `Nonce` is untouched by this path.
3. Bob (or anyone) submits a new `WithdrawalProof` covering `[B, C]` (or a superset including `[A]`) along with Alice's old signature and `nonce=0`. Since `Nonce::<T>::get(&Alice, chain)` is still `0`, the signature check in [6](#0-5)  passes, and the entirety of `[B, C]`'s `total_fee` is credited to Bob instead of Alice.
4. This confirms the signature is not scoped to the batch Alice intended, letting an unprivileged third party redirect fees Alice never authorized for that specific claim.

### Citations

**File:** modules/pallets/relayer/src/accumulate.rs (L106-137)
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
```

**File:** modules/pallets/relayer/src/accumulate.rs (L140-147)
```rust
		} else {
			let _ = Fees::<T>::try_mutate(state_machine, delivery_address.clone(), |inner| {
				*inner += total_fee;
				Ok::<(), ()>(())
			});

			delivery_address
		};
```

**File:** modules/pallets/relayer/src/accumulate.rs (L305-315)
```rust
/// Signed payload authorising a beneficiary redirect on a specific source chain.
/// Including the relayer nonce alongside the state machine keeps the signature usable for
/// exactly one accumulate call on that chain, mirroring how `withdraw_fees` binds its signed
/// payload.
pub fn beneficiary_message(
	nonce: u64,
	state_machine: StateMachine,
	beneficiary: &[u8],
) -> [u8; 32] {
	sp_io::hashing::keccak_256(&(nonce, state_machine, beneficiary).encode())
}
```

**File:** modules/pallets/testsuite/src/tests/pallet_ismp_relayer.rs (L732-736)
```rust
		pallet_ismp_relayer::Pallet::<Test>::accumulate_fees(
			RuntimeOrigin::none(),
			withdrawal_proof,
		)
		.unwrap();
```
