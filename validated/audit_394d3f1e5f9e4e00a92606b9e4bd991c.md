### Title
Signature domain confusion between relayer fee `withdraw` and `accumulate` beneficiary-redirect lets a captured signature trigger the wrong on-chain action - (File: `modules/pallets/relayer/src/withdrawal.rs`, `modules/pallets/relayer/src/accumulate.rs`)

### Summary
The Paraspace report's core defect is an accounting/authorization function computing a value from an incomplete or wrongly-scoped input, causing state to diverge from what was actually authorized. The local analog is in `pallet-ismp-relayer`: two independent, permissionless, unsigned extrinsics — `withdraw_fees` and `accumulate_fees` (with a beneficiary redirect) — build their signed payloads using functions that serialize to **byte-identical preimages** for the same `(nonce, chain, beneficiary)` input, and they share the exact same `Nonce` storage double-map. A signature intended to authorize one action can therefore be replayed to trigger the other, unauthorized action on the shared `Fees` balance.

### Finding Description
`withdrawal::message()` builds the signed payload for `withdraw_fees`: [1](#0-0) 

`accumulate::beneficiary_message()` builds the signed payload for the beneficiary-redirect option inside `accumulate_fees`: [2](#0-1) 

Both encode the exact same SCALE tuple shape — `(u64 nonce, StateMachine chain, bytes beneficiary)` — producing an identical `keccak_256` preimage whenever `nonce`, `chain`, and `beneficiary` line up. There is no call-type discriminator, domain tag, or purpose byte in either payload.

Both call sites read from and write to the **same** `Nonce<T>` double-map keyed by `(address, StateMachine)`: [3](#0-2) 

`withdraw()` reads/uses `Nonce::<T>::get(address, dest_chain)`: [4](#0-3) 

`accumulate()`'s beneficiary branch reads/uses `Nonce::<T>::get(&delivery_address, state_machine)`: [5](#0-4) 

Both `accumulate_fees` and `withdraw_fees` are permissionless (`ensure_none(origin)`) — the submitter need not be the relayer; the embedded cryptographic signature is the only authorization: [6](#0-5) 

Because the two message formats collide, a signature a relayer creates to authorize "redirect the fee being accumulated in *this* batch to beneficiary B on chain C" is wire-identical to a signature authorizing "withdraw my entire current `Fees[C, address]` balance to beneficiary B." Anyone observing either signed extrinsic before it lands (both are unsigned/public-mempool transactions, and `pre_dispatch` is a no-op so the effectful function only really executes at block inclusion) can repackage the exact same `(nonce, signature)` pair into the other call. Whichever one is included first consumes the nonce and executes; the corrupted value is **which action fires and against what balance/beneficiary pairing**, not the value the signer actually intended.

### Impact Explanation
This is a "logic attack" / "transaction manipulation" class bug per the Hyperbridge bounty scope: a signature scoped for one purpose (crediting/redirecting an in-flight accumulate batch) is a valid, unmodified credential for a structurally different, higher-impact action (withdrawing the entire current `Fees[chain, address]` balance via `IHostManager.withdraw` / `WithdrawRelayerFees`). This can:
- Force a premature `withdraw_fees` that drains the whole accumulated balance for `(chain, address)` — which may be far larger than, and unrelated to, the specific batch the relayer intended to redirect — before the relayer chose to withdraw.
- Consume the nonce out of the intended sequence, causing the legitimate `accumulate_fees` (with beneficiary redirect) to subsequently fail signature verification (`InvalidPublicKey`/`InvalidSignature`) since the on-chain nonce has already advanced, denying the intended credit.
- Conversely, a `withdraw_fees` signature could be replayed into `accumulate_fees`'s beneficiary slot to redirect future fee crediting to an address the relayer only authorized for a one-time withdrawal.

Missing domain separation lets bridge revenue move via an action the relayer never authorized in that form, matching the "false authorization"/"transaction manipulation" impact class the bounty targets, and the funds path goes directly through `pallet_ismp_host_executive`/`IHostManager::withdraw` and `EvmHost.withdraw`: [7](#0-6) 

### Likelihood Explanation
Exploitation requires observing a not-yet-included signed unsigned-extrinsic payload (`accumulate_fees` with `beneficiary_details` set, or `withdraw_fees` with a `beneficiary` override) and re-submitting it as the other call before the original lands — a standard public-mempool race, not a compromised relayer, prover, or admin. `validate_unsigned` gives both a `priority: 100` and unlimited `longevity`, so a competing submission targeting the identical `provides` tag has ample opportunity window: [8](#0-7) 

Only relayers who use the optional beneficiary-redirect path on either call are exposed; the base flows (accumulate crediting `delivery_address` directly, or withdraw with `beneficiary: None`) don't collide because the 2-tuple and 3-tuple encodings differ in shape.

### Recommendation
Add explicit domain separation to both signed payloads (e.g., prefix each preimage with a distinct call-selector byte/string such as `b"ISMP-RLYR-WITHDRAW"` vs `b"ISMP-RLYR-ACCUM-BENEFICIARY"`) so no valid signature can ever verify against both `message()` and `beneficiary_message()`. Consider also separating the `Nonce` map per call-purpose, or including the acting pallet call index in the signed struct, to remove any possibility of cross-function replay even if future call types are added.

### Proof of Concept
1. Relayer `R` delivers a request whose fee sits in `Fees[ChainC, R]`. `R` wants to redirect just this accumulate batch's credit to cold wallet `B`, so it signs `beneficiary_message(nonce=N, ChainC, B)` and broadcasts `accumulate_fees(withdrawal_proof, beneficiary_details = Some((B, sig)))`.
2. Attacker `A` observes this pending extrinsic in the mempool (the signature and payload are plaintext) and immediately submits `withdraw_fees(WithdrawalInputData { signature: sig, dest_chain: ChainC, beneficiary: Some(B) })` with higher/equal priority.
3. Inside `Pallet::withdraw`, `address = signer(sig) = R`'s delivery key; `nonce = Nonce::<T>::get(R, ChainC) = N` (unchanged); `msg = message(N, ChainC, Some(B))` — identical bytes to `beneficiary_message(N, ChainC, B)` — so `sig.verify(msg, None)` succeeds.
4. `available_amount = Fees::<T>::get(ChainC, R)` (R's entire current balance, not scoped to the pending accumulate batch) is dispatched to beneficiary `B`, `Fees` is zeroed, and `Nonce` bumps to `N+1`.
5. R's original `accumulate_fees` now fails signature verification against nonce `N+1`, and the relayer's balance for `ChainC` was withdrawn earlier and to a scope R never explicitly authorized for a `withdraw` action.

### Citations

**File:** modules/pallets/relayer/src/withdrawal.rs (L88-89)
```rust
		let nonce = Nonce::<T>::get(address.clone(), withdrawal_data.dest_chain);
		let msg = message(nonce, withdrawal_data.dest_chain, withdrawal_data.beneficiary.clone());
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

**File:** modules/pallets/relayer/src/accumulate.rs (L107-111)
```rust
		let beneficiary_address = if let Some((beneficiary_address, signature)) =
			withdrawal_proof.beneficiary_details
		{
			let nonce = Nonce::<T>::get(&delivery_address, state_machine);
			let msg = beneficiary_message(nonce, state_machine, &beneficiary_address);
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

**File:** modules/pallets/relayer/src/lib.rs (L124-135)
```rust
	/// Latest nonce for each address and the state machine they want to withdraw from
	#[pallet::storage]
	#[pallet::getter(fn nonce)]
	pub type Nonce<T: Config> = StorageDoubleMap<
		_,
		Blake2_128Concat,
		Vec<u8>,
		Blake2_128Concat,
		StateMachine,
		u64,
		ValueQuery,
	>;
```

**File:** modules/pallets/relayer/src/lib.rs (L350-368)
```rust
		#[pallet::call_index(0)]
		#[pallet::weight({1_000_000})]
		pub fn accumulate_fees(
			origin: OriginFor<T>,
			withdrawal_proof: WithdrawalProof,
		) -> DispatchResult {
			ensure_none(origin)?;
			Self::accumulate(withdrawal_proof)
		}

		#[pallet::call_index(1)]
		#[pallet::weight({1_000_000})]
		pub fn withdraw_fees(
			origin: OriginFor<T>,
			withdrawal_data: WithdrawalInputData,
		) -> DispatchResult {
			ensure_none(origin)?;
			Self::withdraw(withdrawal_data)
		}
```

**File:** modules/pallets/relayer/src/lib.rs (L484-500)
```rust
			let encoding = match call {
				Call::accumulate_fees { withdrawal_proof } => withdrawal_proof.encode(),
				Call::withdraw_fees { withdrawal_data } => withdrawal_data.encode(),
				Call::claim_outbound_consensus_delivery_reward { claim } => claim.encode(),
				Call::claim_outbound_request_delivery_reward { claim } => claim.encode(),
				_ => unreachable!(),
			};

			let msg_hash = sp_io::hashing::keccak_256(&encoding).to_vec();

			Ok(ValidTransaction {
				priority: 100,
				requires: vec![],
				provides: vec![msg_hash],
				longevity: TransactionLongevity::MAX,
				propagate: true,
			})
```

**File:** evm/src/core/EvmHost.sol (L647-660)
```text
    /**
     * @dev withdraws host revenue to the given address, can only be called by cross-chain governance
     * @param params, the parameters for withdrawal
     */
    function withdraw(WithdrawParams memory params) external restrict(_hostParams.hostManager) {
        if (params.token == address(0)) {
            // this is safe because re-entrancy is mitigated before dispatching requests
            (bool sent,) = params.beneficiary.call{value: params.amount}("");
            if (!sent) revert WithdrawalFailed();
        } else {
            IERC20(params.token).safeTransfer(params.beneficiary, params.amount);
        }
        emit HostWithdrawal({beneficiary: params.beneficiary, amount: params.amount, token: params.token});
    }
```
