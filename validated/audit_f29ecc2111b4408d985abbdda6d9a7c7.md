## Analysis

The seed bug (`rotateNodeRunnerOfSmartWallet`) reduces to: **a permissionless function accepts a signed/authorized action, but the enforcement of "when" and "in what context" that authorization is valid is weak enough that the same authorization token can be consumed out of order / in the wrong context**, breaking the invariant the caller relied on. Translating this to Hyperbridge's relayer fee pipeline, I found a concrete local analog: the `Nonce<T>` storage item and its signed-message format are **shared verbatim between two independent, permissionless (unsigned-origin) settlement flows** in `pallet-relayer` — fee-beneficiary redirection during `accumulate` and outright fee `withdraw`al. Because the signed payloads are byte-identical for the same `(nonce, state_machine/dest_chain, beneficiary)` triple, a signature authorized for one flow is valid for the other, and consuming it in either flow burns the nonce for both.

### Title
Cross-flow signature/nonce reuse between relayer fee-beneficiary redirection and fee withdrawal - (File: `modules/pallets/relayer/src/accumulate.rs`, `modules/pallets/relayer/src/withdrawal.rs`)

### Summary
`pallet-relayer` uses one `Nonce<T>` map (keyed by `(address, StateMachine)`) to authorize two functionally distinct signed actions: redirecting newly accumulated fees to a beneficiary during `accumulate`, and withdrawing a relayer's already-accumulated `Fees` balance via `withdraw_fees`. Both actions hash an identical tuple shape and share the same nonce counter, so a signature produced for one flow is a valid, unused authorization for the other.

### Finding Description
`accumulate.rs` verifies a relayer-signed beneficiary redirect using:
```rust
let nonce = Nonce::<T>::get(&delivery_address, state_machine);
let msg = beneficiary_message(nonce, state_machine, &beneficiary_address);
...
Nonce::<T>::try_mutate(&delivery_address, state_machine, |value| { *value += 1; ... })
``` [1](#0-0) 

with
```rust
pub fn beneficiary_message(nonce: u64, state_machine: StateMachine, beneficiary: &[u8]) -> [u8; 32] {
	sp_io::hashing::keccak_256(&(nonce, state_machine, beneficiary).encode())
}
``` [2](#0-1) 

`withdrawal.rs`'s `withdraw` (the `withdraw_fees` extrinsic, called with `RuntimeOrigin::none()`, i.e. permissionless/unsigned) verifies a signature against the *same* `Nonce<T>` map for `(address, dest_chain)` and the *same* message shape:
```rust
let nonce = Nonce::<T>::get(address.clone(), withdrawal_data.dest_chain);
let msg = message(nonce, withdrawal_data.dest_chain, withdrawal_data.beneficiary.clone());
...
Nonce::<T>::try_mutate(address.clone(), withdrawal_data.dest_chain, |value| { *value += 1; ... })
``` [3](#0-2) 

```rust
pub fn message(nonce: u64, dest_chain: StateMachine, beneficiary: Option<Vec<u8>>) -> [u8; 32] {
	if let Some(beneficiary) = beneficiary {
		return sp_io::hashing::keccak_256(&(nonce, dest_chain, beneficiary).encode());
	}
	sp_io::hashing::keccak_256(&(nonce, dest_chain).encode())
}
``` [4](#0-3) 

Both `beneficiary_message` and `message` (when `beneficiary` is `Some`) hash the identical SCALE-encoded tuple `(u64, StateMachine, bytes)`, and both read/increment the identical `Nonce::<T>` entry keyed by `(relayer_address, state_machine)`. `accumulate` is imported and reused via `Nonce` in both modules (`use crate::{..., Nonce, Pallet};`) confirming it is the same storage item, not two separate counters [5](#0-4) .

Because there is no domain-separation tag (no discriminant byte identifying "this signature authorizes an accumulate-beneficiary-redirect" vs "this signature authorizes a withdrawal"), a relayer's signature intended for one flow is a fully valid signature for the other flow at the same nonce. Both `accumulate` (submitted as part of an unsigned `WithdrawalProof` extrinsic) and `withdraw_fees` are permissionless entry points (`ensure_none`/`RuntimeOrigin::none()`), so **any unprivileged third party** who observes a relayer's signed payload (broadcast in the mempool as calldata of either extrinsic) can extract it and resubmit it through the *other* extrinsic before the relayer's originally intended transaction lands. Whichever lands first consumes the nonce; the second submission's signature no longer matches the now-advanced nonce and the enclosing extrinsic errors out (`Error::<T>::InvalidSignature`), aborting an otherwise-valid, already-proven fee accumulation or withdrawal.

### Impact Explanation
This breaks the "one-time receipt handling" / commitment-uniqueness guarantee the Hyperbridge Pivots require for reward/settlement paths: a single signed authorization is not scoped to a single settlement action, so it can be redirected across settlement mechanisms by an unauthenticated third party. Concretely:
- A newly-proven batch of delivered request commitments in `accumulate` can be forced to fail entirely (the whole extrinsic reverts via `?` before any `Fees`/`RequestCommitments::claimed` state is written) if an attacker races a stolen `withdraw_fees` signature through first, consuming the shared nonce.
- Conversely, a relayer's beneficiary-redirect signature (signed for a specific `accumulate` batch) can be extracted and replayed through `withdraw_fees` ahead of schedule, forcing early settlement of the relayer's already-accrued `Fees[state_machine][relayer]` balance and invalidating the relayer's pending `accumulate` submission.
This is a logic/settlement-ordering flaw rooted in missing domain separation between two distinct signed protocols sharing one nonce namespace — not merely a generic front-run, since the *root cause* is a cryptographic ambiguity (identical message pre-image across two unrelated authorization schemes) rather than pure transaction-ordering luck.

### Likelihood Explanation
Both `accumulate` and `withdraw_fees` are unsigned/permissionless extrinsics, so no privileged relayer, prover, or admin role is required to exploit this — any chain observer can extract calldata from either pending extrinsic and resubmit it via the other pallet call. The condition is also self-triggering in normal operation any time a relayer signs both a beneficiary-redirect payload and a withdrawal payload for the same `(address, state_machine)` pair with adjacent nonces, since the two functions silently compete for the same counter.

### Recommendation
Introduce a domain-separation prefix/discriminant byte (e.g., `b"ACCUMULATE_BENEFICIARY"` vs `b"WITHDRAW"`) into both `beneficiary_message` and `message` before hashing, and/or split `Nonce<T>` into two independent per-purpose counters (e.g., `AccumulateNonce` and `WithdrawNonce`) so a signature produced for one flow can never be a valid, nonce-consuming input to the other.

### Proof of Concept
1. Relayer signs `beneficiary_message(N, StateMachine::Evm(X), beneficiary_bytes)` and submits it embedded in a `WithdrawalProof.beneficiary_details` via the unsigned `accumulate` extrinsic (`modules/pallets/relayer/src/accumulate.rs:107-147`).
2. Before this extrinsic is included, an observer extracts `(signature, beneficiary_bytes)` from the pending transaction pool and constructs a `WithdrawalInputData { signature, dest_chain: StateMachine::Evm(X), beneficiary: Some(beneficiary_bytes) }`, computing the identical digest via `message(N, StateMachine::Evm(X), Some(beneficiary_bytes))` (`modules/pallets/relayer/src/withdrawal.rs:192-197`), and submits it as `withdraw_fees` (`RuntimeOrigin::none()`).
3. `withdraw_fees` verifies successfully against `Nonce[address, X] == N`, pays out the relayer's current `Fees[X][address]` balance, and bumps `Nonce[address, X]` to `N+1` (`modules/pallets/relayer/src/withdrawal.rs:88-131`).
4. The originally submitted `accumulate` extrinsic now reads `Nonce[address, X] == N+1`, recomputes `beneficiary_message(N+1, ...)`, which no longer matches the relayer's original signature over `N`, and reverts with `Error::<T>::InvalidSignature`, aborting the entire batch's fee crediting (`modules/pallets/relayer/src/accumulate.rs:110-132`).

### Citations

**File:** modules/pallets/relayer/src/accumulate.rs (L25-25)
```rust
use crate::{withdrawal::WithdrawalProof, Config, Error, Event, Fees, Nonce, Pallet};
```

**File:** modules/pallets/relayer/src/accumulate.rs (L106-132)
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

**File:** modules/pallets/relayer/src/withdrawal.rs (L192-197)
```rust
pub fn message(nonce: u64, dest_chain: StateMachine, beneficiary: Option<Vec<u8>>) -> [u8; 32] {
	if let Some(beneficiary) = beneficiary {
		return sp_io::hashing::keccak_256(&(nonce, dest_chain, beneficiary).encode());
	}
	sp_io::hashing::keccak_256(&(nonce, dest_chain).encode())
}
```
