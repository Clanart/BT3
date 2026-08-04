Found a direct local analog: the relayer pallet's outbound delivery-reward claim signatures use a domain-less message hash — no chain/instance-binding domain separator like `EIP-712`'s `chainId`/`verifyingContract` — so a signature captured on one Hyperbridge coordination-chain deployment can be replayed to double-claim the same reward on another.

### Title
Outbound delivery-reward claim signatures lack a domain separator, enabling cross-instance replay/double-claim of relayer rewards - ([File: modules/pallets/relayer/src/outbound_consensus.rs])

### Summary
`OutboundConsensusDeliveryClaim` and `OutboundRequestDeliveryClaim` authorize reward payouts to a relayer based on an ECDSA/sr25519 signature over a bare `keccak256` hash of `(set_id, dest_chain, payee)` or `(commitment, dest_chain, payee)`. Neither payload binds the identity of the Hyperbridge instance (parachain/runtime) verifying the claim — there is no genesis hash, `host_state_machine()`, or any other domain separator analogous to EIP-712's `chainId`/`verifyingContract`. This is exactly the bug class from the external report: a signature is portable to any verifier that shares the same message-construction code, because the message never asserts which deployment it was produced for.

### Finding Description
`outbound_consensus_delivery_message` hashes only the destination chain, set id, and payee: [1](#0-0) 

`process_outbound_consensus_delivery_claim` recovers the signer from this message and checks it against the address proven via state-proof on the destination EVM chain, then pays the reward from the local treasury: [2](#0-1) 

The same pattern repeats for request-delivery rewards, where the signed message is `(commitment, dest_chain, payee)`: [3](#0-2) 

And again for the fee-accumulation beneficiary redirect, `(nonce, state_machine, beneficiary)`: [4](#0-3) 

None of these three payloads include any value identifying *which Hyperbridge chain* is doing the verifying/paying. The only "chain" referenced (`dest_chain`/`state_machine`) is the destination EVM/Substrate chain being delivered to — not the Hyperbridge coordination chain itself. Replay protection is purely local: `OutboundConsensusRotationsClaimed[destination, set_id]` and `OutboundRequestsClaimed[commitment]` prevent a *second* claim on the *same* pallet instance, but say nothing about a *different* pallet instance (a separate Hyperbridge deployment, e.g. a second production/coordination chain or a migrated/forked runtime that reuses the same `Config`) that independently tracks state commitments for the same real-world destination chain and configures a non-zero reward for the same module/destination.

Because relayer delivery to `dest_chain` is a real, independently-verifiable, on-chain fact (the state proof each instance checks is against that instance's own state commitment for the destination, which reflects genuinely-occurred consensus/request delivery), a signature produced once by a relayer's EVM/sr25519 key for a real delivery is valid evidence on *every* Hyperbridge instance that also tracks that destination and reward — with zero additional proof-of-work from the relayer. The claim extrinsics are permissionless/unsigned (`validate_unsigned`, per the accompanying design doc), so the signature and proof are public on submission and can be resubmitted verbatim to any other qualifying instance by anyone.

### Impact Explanation
This is a double-settlement of relayer/consensus/request delivery rewards: the same underlying delivery action is paid out multiple times from multiple treasuries using one replayed signature, i.e. direct loss of protocol treasury funds without a matching increase in delivered value — squarely in the "replay/double-claim/double-settlement" and "stealing or loss of funds" impact categories. It requires no malicious peer, relayer, or governance actor — only a legitimate relayer (or any observer copying a public extrinsic) submitting the identical claim payload to a second qualifying instance.

### Likelihood Explanation
Requires at least two live Hyperbridge coordination-chain deployments (e.g. current mainnet/testnet coordination chains, or any future migrated/forked instance) both configuring a non-zero `OutboundConsensusDeliveryReward`/`OutboundRequestDeliveryReward`/beneficiary-redirect for the same destination and both having advanced their state commitment past the same delivery height — a realistic operational condition given the project runs more than one coordination chain sharing this pallet code, not a contrived or purely theoretical setup.

### Recommendation
Include a Hyperbridge-instance domain separator in every signed claim payload — e.g. `host.host_state_machine()` (this chain's own state-machine identity) or the genesis hash of the coordination chain — inside `outbound_consensus_delivery_message`, `outbound_request_delivery_message`, and `beneficiary_message`, mirroring how EIP-712 binds `chainId`/`verifyingContract` to prevent cross-domain signature reuse.

### Proof of Concept
1. Relayer R delivers a consensus rotation `set_id` to EVM destination `D`, becoming `EvmHost._epochs[set_id] = R` on `D`.
2. R signs `outbound_consensus_delivery_message(set_id, D, payee)` and submits `OutboundConsensusDeliveryClaim{state_proof_A, set_id, payee, signature}` to Hyperbridge instance A, which verifies the state proof against A's own state commitment for `D` and pays `payee` from A's treasury; `OutboundConsensusRotationsClaimed::<A>[D, set_id]` is set.
3. Any party (R or an observer) resubmits the same `signature`, `set_id`, `payee`, together with a state proof against instance B's own state commitment for the same `D` height (`state_proof_B`), as `OutboundConsensusDeliveryClaim` on instance B, whose `OutboundConsensusRotationsClaimed::<B>[D, set_id]` is independently `None`.
4. Instance B independently verifies the (valid, real) state proof and the (valid, unbound) signature and pays `payee` again from B's treasury — a second payout for one physical delivery, using a signature never bound to a specific verifying instance.

### Citations

**File:** modules/pallets/relayer/src/outbound_consensus.rs (L167-188)
```rust
		// Replay protection comes from the `OutboundConsensusRotationsClaimed`
		let msg = outbound_consensus_delivery_message(set_id, destination, payee);
		let recovered = signature.verify(&msg, None).map_err(|_| Error::<T>::InvalidSignature)?;
		let recovered_address = Address::try_from(recovered.as_slice())
			.map_err(|_| Error::<T>::OutboundSignerMismatch)?;
		ensure!(recovered_address == evm_address, Error::<T>::OutboundSignerMismatch);

		let reward = OutboundConsensusDeliveryReward::<T>::get(destination);
		ensure!(reward > BalanceOf::<T>::default(), Error::<T>::OutboundNoRewardConfigured);

		let treasury: T::AccountId =
			<T as Config>::TreasuryPalletId::get().into_account_truncating();
		let payee_account: T::AccountId = payee.into();
		<<T as pallet_ismp::Config>::Currency as Mutate<T::AccountId>>::transfer(
			&treasury,
			&payee_account,
			reward,
			Preservation::Preserve,
		)
		.map_err(|_| Error::<T>::OutboundRewardTransferFailed)?;

		OutboundConsensusRotationsClaimed::<T>::insert(destination, set_id, ());
```

**File:** modules/pallets/relayer/src/outbound_consensus.rs (L224-229)
```rust
pub fn outbound_consensus_delivery_message(
	set_id: u64,
	dest_chain: StateMachine,
	payee: [u8; 32],
) -> [u8; 32] {
	sp_io::hashing::keccak_256(&(set_id, dest_chain, payee).encode())
```

**File:** modules/pallets/relayer/src/outbound_request.rs (L119-197)
```rust
	pub fn process_outbound_request_delivery_claim(
		claim: OutboundRequestDeliveryClaim,
	) -> DispatchResult {
		let OutboundRequestDeliveryClaim { request, state_proof, payee, signature } = claim;
		let destination = state_proof.height.id.state_id;

		let commitment = hash_request::<<T as Config>::IsmpHost>(&Request::Post(request.clone()));

		let host = <T as Config>::IsmpHost::default();
		ensure!(
			request.source == host.host_state_machine(),
			Error::<T>::OutboundRequestSourceNotHyperbridge,
		);

		ensure!(
			RequestCommitments::<T>::get(commitment).is_some(),
			Error::<T>::OutboundRequestNotKnown,
		);

		ensure!(
			!OutboundRequestsClaimed::<T>::contains_key(commitment),
			Error::<T>::OutboundRequestAlreadyClaimed,
		);

		let module_id: BoundedVec<u8, ModuleIdBound> = request
			.from
			.clone()
			.try_into()
			.map_err(|_| Error::<T>::OutboundRequestModuleIdTooLong)?;
		let reward = OutboundRequestDeliveryReward::<T>::get(&module_id);
		ensure!(reward > BalanceOf::<T>::default(), Error::<T>::OutboundRequestNoRewardConfigured);

		ensure!(destination == request.dest, Error::<T>::MismatchedStateMachine);

		let state_machine = ismp::handlers::validate_state_machine(&host, state_proof.height)
			.map_err(|_| Error::<T>::OutboundDestinationStateNotKnown)?;
		let receipt_key = state_machine
			.receipts_state_trie_key(vec![commitment])
			.into_iter()
			.next()
			.ok_or(Error::<T>::OutboundRequestUnsupportedDestination)?;
		let proof_results =
			Self::verify_withdrawal_proof(&*state_machine, &state_proof, vec![receipt_key.clone()])
				.map_err(|_| Error::<T>::OutboundDestinationStateNotKnown)?;
		let raw = proof_results
			.get(&receipt_key)
			.cloned()
			.flatten()
			.ok_or(Error::<T>::OutboundDeliveryNotProven)?;

		let delivered_by = Self::decode_receipt_relayer(destination, &raw)?;

		let msg = outbound_request_delivery_message(commitment, destination, payee);
		let recovered = signature.verify(&msg, None).map_err(|_| Error::<T>::InvalidSignature)?;
		ensure!(recovered == delivered_by, Error::<T>::OutboundRequestSignerMismatch);

		let treasury: T::AccountId =
			<T as Config>::TreasuryPalletId::get().into_account_truncating();
		let payee_account: T::AccountId = payee.into();
		<<T as pallet_ismp::Config>::Currency as Mutate<T::AccountId>>::transfer(
			&treasury,
			&payee_account,
			reward,
			Preservation::Preserve,
		)
		.map_err(|_| Error::<T>::OutboundRequestRewardTransferFailed)?;

		OutboundRequestsClaimed::<T>::insert(commitment, ());

		Self::deposit_event(Event::OutboundRequestDeliveryRewarded {
			commitment,
			state_machine: destination,
			module_id,
			relayer: payee_account,
			amount: reward,
		});

		Ok(())
	}
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
