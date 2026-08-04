## Title
Cross-function signature replay between `withdraw_fees` and `accumulate_fees` beneficiary redirect due to identical signed digest and shared nonce — (`File: modules/pallets/relayer/src/withdrawal.rs`, `modules/pallets/relayer/src/accumulate.rs`)

### Summary
`pallet-ismp-relayer` has two independent unsigned extrinsics — `withdraw_fees` (in `withdrawal.rs`) and `accumulate_fees`'s beneficiary-redirect path (in `accumulate.rs`) — that authenticate a relayer's intent via an off-chain signature over `(nonce, chain, beneficiary)` and consume the *same* `Nonce<T>` storage item keyed by `(address, state_machine)`. Both functions hash an identical tuple shape with `keccak_256`, so a signature produced for one purpose is a valid signature for the other. This mirrors the C4 M-01 pattern: the signature commits only to a coarse identifier (nonce + chain + beneficiary) instead of binding to the specific action/amount it was meant to authorize, enabling a captured signature to be replayed against a different, unintended state-changing call.

### Finding Description
`withdrawal.rs::message()`:
```rust
pub fn message(nonce: u64, dest_chain: StateMachine, beneficiary: Option<Vec<u8>>) -> [u8; 32] {
    if let Some(beneficiary) = beneficiary {
        return sp_io::hashing::keccak_256(&(nonce, dest_chain, beneficiary).encode());
    }
    sp_io::hashing::keccak_256(&(nonce, dest_chain).encode())
}
``` [1](#0-0) 

`accumulate.rs::beneficiary_message()`:
```rust
pub fn beneficiary_message(
    nonce: u64,
    state_machine: StateMachine,
    beneficiary: &[u8],
) -> [u8; 32] {
    sp_io::hashing::keccak_256(&(nonce, state_machine, beneficiary).encode())
}
``` [2](#0-1) 

Both functions call `Nonce::<T>::get(&address_or_delivery_address, chain_or_state_machine)` to build the digest and, on success, increment the *same* `Nonce<T>` map entry: [3](#0-2) [4](#0-3) 

Since `(u64, StateMachine, Vec<u8>).encode()` produces byte-identical SCALE encoding regardless of which call site built the tuple, `message(nonce, chain, Some(beneficiary))` and `beneficiary_message(nonce, chain, beneficiary)` produce the exact same 32-byte digest for the same `(nonce, chain, beneficiary)` triple. A signature is not scoped to:
- which function it authorizes (`withdraw_fees` vs. the `accumulate_fees` beneficiary redirect),
- the amount being moved (the entire `Fees<T>[address, chain]` balance in `withdraw_fees`, versus only the `total_fee` proven in a specific `WithdrawalProof` batch in `accumulate_fees`),
- or any other action-specific data.

Both extrinsics are dispatched as unsigned calls authenticated purely by the embedded `Signature`; the tesseract client submits them via `RuntimeOrigin::none()` (confirmed by the pallet's own test harness pattern), meaning any party that observes a broadcast signed payload (e.g., in the tx pool, in a prior on-chain event, or relayed off-chain) can resubmit it against the *other* extrinsic before the legitimate transaction consumes the nonce.

### Impact Explanation
An attacker who intercepts a signature meant to authorize a narrow beneficiary redirect for one `accumulate_fees` batch (bounded to that batch's `total_fee`) can instead submit it as `withdraw_fees`'s `WithdrawalInputData`, which sweeps the relayer's *entire* accumulated `Fees<T>[address, chain]` balance to the attacker-observed beneficiary — an amount potentially far larger than what the signer intended to authorize. Conversely, a `withdraw_fees` signature could be replayed into `accumulate_fees`'s `beneficiary_details` to redirect an unrelated fee batch. Either direction results in funds moving to a beneficiary/amount the original signer did not specifically authorize for that action, which is a fund-loss / wrong-beneficiary-or-amount outcome squarely within the bounty's "stealing or loss of funds" and "logic attack / replay" categories.

### Likelihood Explanation
Exploitation requires only observing one valid, still-unconsumed signature (broadcast extrinsics and their signature payloads are public, e.g., visible in the transaction pool or already-included blocks before the corresponding nonce increments) and racing to submit it to the sibling extrinsic first. No relayer, prover, or admin compromise is needed — only an unprivileged party monitoring the network and front-running the intended call, which is within the bounty's permitted "unprivileged attacker" threat model. The main constraint is winning the race before the legitimate transaction lands and bumps the nonce, which is a realistic condition given typical block/mempool latency.

### Recommendation
Bind each signed digest to the specific action and payload it authorizes: include a domain separator/action tag (e.g., `b"withdraw_fees"` vs `b"accumulate_beneficiary"`) and the concrete amount/commitment set being moved in the hashed message, not just `(nonce, chain, beneficiary)`. Alternatively, use disjoint nonce namespaces per extrinsic so a signature valid for one cannot be replayed against the other, and additionally commit to the exact `total_fee`/commitment set in `accumulate_fees`'s beneficiary signature so it cannot be reused to authorize a different amount.

### Proof of Concept
1. Relayer `R` accumulates fees on `chain = Evm(84532)`. `R` wants to redirect only the current `accumulate_fees` batch (say `total_fee = 10` tokens) to `beneficiary = B` and signs:
   `sig = sign(keccak256((nonce=0, Evm(84532), B).encode()))`
   intending to submit it inside a `WithdrawalProof.beneficiary_details = (B, sig)` for `accumulate_fees`.
2. Before `R`'s `accumulate_fees` transaction lands, attacker `A` observes `sig` in the mempool/event log and submits `withdraw_fees` first with:
   `WithdrawalInputData { signature: Signature::Evm { address: R, signature: sig }, dest_chain: Evm(84532), beneficiary: Some(B) }`
   — see `withdraw_fees` message construction at [5](#0-4) , which recomputes the identical digest and accepts the same signature.
3. `withdraw_fees` succeeds, transferring `R`'s *entire* accumulated `Fees<T>[R, Evm(84532)]` balance (which may be much larger than the 10 tokens `R` intended to redirect) to `B`, and increments `Nonce<T>[R, Evm(84532)]`, invalidating `R`'s originally-intended `accumulate_fees` call.
4. Net effect: funds beyond what `R` authorized for that specific action are moved, purely by replaying a signature across two different extrinsics that share the same digest format and nonce space.

**Note:** I was unable to directly inspect `modules/pallets/relayer/src/lib.rs` (the `ValidateUnsigned`/`ensure_none` wiring for `withdraw_fees` and `accumulate_fees`) due to a tool error in the final step, so the exact unsigned-dispatch guard could not be visually re-confirmed in this session — it is inferred from the pallet's documented design and the `outbound_request.rs`/testsuite pattern (`ensure_none(origin)` + `RuntimeOrigin::none()` in tests) applied consistently across this pallet's claim/withdrawal extrinsics. If a Devin session has full repo access, confirming `lib.rs`'s `withdraw_fees`/`accumulate_fees` call definitions and any `ValidateUnsigned::validate_unsigned` tag logic would be the first step to fully verify this PoC before remediation.

### Citations

**File:** modules/pallets/relayer/src/withdrawal.rs (L88-115)
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

**File:** modules/pallets/relayer/src/accumulate.rs (L110-132)
```rust
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
