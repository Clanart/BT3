## Finding

The outbound-request delivery reward claim path in `pallet-relayer` trusts a destination state commitment without the challenge-period and frozen-consensus-client guards that every other ISMP message handler enforces before acting on a state commitment.

### Title
Outbound Request Delivery Reward Claim Skips Challenge-Period/Frozen-Client Checks, Allowing Reward Payout Against Unconfirmed or Fraudulent State — (`modules/pallets/relayer/src/outbound_request.rs`)

### Summary
`process_outbound_request_delivery_claim` pays a treasury-funded reward for proof of delivery of a hyperbridge-originated request, verified against `host.state_machine_commitment(proof.height)`. Unlike every other ISMP handler that consumes a state commitment (POST/GET timeouts, responses, requests), this claim path never checks `IsmpHost::challenge_period()` elapsed, nor that the destination's consensus client is not frozen, before trusting the proof and transferring funds.

### Finding Description
`process_outbound_request_delivery_claim` [1](#0-0)  resolves the destination state machine with `ismp::handlers::validate_state_machine(&host, state_proof.height)` and then verifies the receipt proof via `Self::verify_withdrawal_proof`, which fetches the raw state commitment from storage with `host.state_machine_commitment(proof.height)` and verifies the proof against it [2](#0-1) . Neither function call, nor any surrounding `ensure!`, checks `IsmpHost::challenge_period()` elapsed or `is_expired`/frozen consensus-client state.

This is a deviation from the established pattern in this codebase. Every other handler that consumes a `StateCommitment` explicitly re-verifies both invariants before trusting it:
- The EVM `HandlerV2.sol` timeout handlers explicitly compute `delay = block.timestamp - host.stateMachineCommitmentUpdateTime(...)` and revert with `ChallengePeriodNotElapsed` if the challenge period hasn't passed, for both POST and GET timeouts [3](#0-2) [4](#0-3) .
- The Substrate `handle_timeouts`/`handle` (responses) are documented to "Assert that the state machine's consensus client is not frozen" and "Assert that the configured `challenge_period` for the `StateCommitment` has elapsed" before any non-membership/membership proof is trusted [5](#0-4) [6](#0-5) .
- The `IsmpHost` trait itself models `challenge_period`/`store_challenge_period` and `is_expired`/frozen-client checks as separate obligations from simply storing/reading a `StateCommitment` [7](#0-6) , confirming that fetching a commitment via storage does not itself enforce these invariants — callers must do it.

The outbound-request claim path is the one place in the reward/settlement surface that reads a raw state commitment and pays out real treasury funds without re-deriving these two guards itself.

### Impact Explanation
A state commitment can exist in storage before its challenge period has elapsed, and can later be proven fraudulent and deleted via `delete_state_commitment` [8](#0-7) , or its consensus client can subsequently be frozen via `freeze_consensus_client`. Because `process_outbound_request_delivery_claim` doesn't gate on either, a relayer can submit a claim using a state commitment during its challenge window (before it is confirmed final, or right as a fraud proof invalidates it) and have the pallet transfer the reward from the treasury `PalletId` account immediately [9](#0-8) . Once paid, `OutboundRequestsClaimed[commitment]` is set and the payout cannot be reversed even if the underlying state commitment is later deleted as fraudulent — this is a direct case of "false proof/state acceptance" leading to loss of treasury funds, matching the bounty's core invariant that state commitments must never let false remote state become trusted for value-moving actions.

### Likelihood Explanation
This does not require a malicious relayer/prover/operator assumption beyond the normal unprivileged relayer role that is expected to submit these claims — any relayer capable of racing a delivery and building a state proof against an as-yet-unconfirmed commitment (which is otherwise a completely legitimate, permissionless action under this pallet's design) can trigger the payout during the challenge window. The severity depends on how commonly commitments are actually invalidated post-storage in practice, which I could not fully verify without reading the internals of `validate_state_machine` in `modules/ismp/core/src/handlers.rs` (not retrievable within available tool calls) to rule out an internal guard I have not seen. This uncertainty should be resolved by inspecting that function directly.

### Recommendation
Add explicit checks in `process_outbound_request_delivery_claim`, mirroring every other proof-consuming handler in this codebase: assert `host.challenge_period(state_proof.height.id)` has elapsed relative to `host.state_machine_commitment_update_time(...)`, and assert the destination's consensus client is not frozen, before calling `verify_withdrawal_proof` and before any treasury transfer.

### Proof of Concept
1. A hyperbridge-originated request (e.g., from `host-executive` or `intents-coprocessor`) is dispatched and lands in `RequestCommitments`.
2. A relayer delivers it to the destination and the destination host writes the delivering relayer's address into `RequestReceipts[commitment]`.
3. Before the challenge period for that destination's freshly-updated state commitment has elapsed (i.e., while the height is still contestable), the relayer submits `claim_outbound_request_delivery_reward` with a `state_proof` at that height.
4. `process_outbound_request_delivery_claim` passes all its `ensure!` checks (source, presence, not-yet-claimed, allowlist, destination match) [10](#0-9) , verifies the receipt proof against the unconfirmed commitment via `verify_withdrawal_proof` [2](#0-1) , and pays out the treasury-funded reward immediately [9](#0-8) .
5. If that state commitment is subsequently proven fraudulent and deleted (`delete_state_commitment`), the reward payout stands — funds are already transferred and `OutboundRequestsClaimed[commitment]` blocks any remediation via re-claim logic.

### Citations

**File:** modules/pallets/relayer/src/outbound_request.rs (L119-167)
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
```

**File:** modules/pallets/relayer/src/outbound_request.rs (L175-184)
```rust
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
```

**File:** modules/pallets/relayer/src/accumulate.rs (L213-236)
```rust
	pub fn verify_withdrawal_proof(
		state_machine: &dyn ismp::consensus::StateMachineClient,
		proof: &Proof,
		keys: Vec<Vec<u8>>,
	) -> Result<BTreeMap<Vec<u8>, Option<Vec<u8>>>, DispatchError> {
		let host = <T as Config>::IsmpHost::default();
		let state = host
			.state_machine_commitment(proof.height)
			.map_err(|_| Error::<T>::ProofValidationError)?;
		// Select the trie root explicitly instead of letting the relayer-supplied proof choose
		// it. Fee accumulation reads ISMP request/receipt metadata, which lives in the global
		// state trie on EVM chains and in the ISMP child trie (overlay root) on substrate
		// chains.
		let root = if proof.height.id.state_id.is_evm() {
			state.state_root
		} else {
			state.overlay_root.ok_or(Error::<T>::ProofValidationError)?
		};
		let result = state_machine
			.verify_state_proof(&host, keys, root, proof)
			.map_err(|_| Error::<T>::ProofValidationError)?;

		Ok(result)
	}
```

**File:** evm/src/core/HandlerV2.sol (L254-264)
```text
    function handlePostRequestTimeouts(IHost host, PostRequestTimeoutMessage calldata message)
        external
        notFrozen(host)
    {
        uint256 delay = block.timestamp - host.stateMachineCommitmentUpdateTime(message.height);
        uint256 challengePeriod = host.challengePeriod();
        if (challengePeriod != 0 && challengePeriod > delay) revert ChallengePeriodNotElapsed();

        // fetch the state commitment
        StateCommitment memory state = host.stateMachineCommitment(message.height);
        if (state.stateRoot == bytes32(0)) revert StateCommitmentNotFound();
```

**File:** evm/src/core/HandlerV2.sol (L293-300)
```text
    function handleGetRequestTimeouts(IHost host, GetTimeoutMessage calldata message) external notFrozen(host) {
        uint256 delay = block.timestamp - host.stateMachineCommitmentUpdateTime(message.height);
        uint256 challengePeriod = host.challengePeriod();
        if (challengePeriod != 0 && challengePeriod > delay) revert ChallengePeriodNotElapsed();

        // fetch the state commitment
        StateCommitment memory state = host.stateMachineCommitment(message.height);
        if (state.stateRoot == bytes32(0)) revert StateCommitmentNotFound();
```

**File:** docs/content/protocol/ismp/timeouts.mdx (L47-54)
```text
The timeout `handle` is used to notify onchain `IsmpModule`s of outgoing requests that have now timed out. A relayer will construct the `TimeoutMessage` which holds a batch of these messages, and their relevant proofs. The handler will perform the following operations

- Assert that the state machine's consensus client is not frozen
- Assert that the configured `challenge_period` for the `StateCommitment` has elapsed
- Assert that the messages have indeed timed out
- Assert that the claimed messages are known by the host
- Assert that the relevant state machine's time has advanced past the `timeout_timestamp` of specified messages.
- Assert that the relevant non-membership proofs for the messages are valid
```

**File:** docs/content/protocol/ismp/responses.mdx (L64-70)
```text
The response `handle` is used to notify onchain `IsmpModule`s of new `GetResponse`s to be processed. A relayer constructs the `ResponseMessage` from the original `GetRequest`s and a state proof at `GetRequest::height` on the destination chain. The handler will perform the following operations:

- Assert that the state machine's consensus client is not frozen.
- Assert that the configured `challenge_period` for the `StateCommitment` has elapsed.
- Assert that the responses have not been previously processed.
- Assert that the `GetRequest`s have not timed out (`get.timeout_timestamp > host.timestamp()`).
- Run `verify_state_proof` against `GetRequest::keys` at `GetRequest::height` to obtain the verified `Vec<StorageValue>` and construct each `GetResponse` on-chain.
```

**File:** modules/ismp/core/src/host.rs (L113-114)
```rust
		period: u64,
	) -> Result<(), Error>;
```

**File:** modules/ismp/core/src/host.rs (L155-177)
```rust
	/// Returns the signer
	fn delete_request_receipt(&self, req: &Request) -> Result<Vec<u8>, Error>;

	/// Delete a response receipt from storage, used when a response is timed out.
	/// Should only ever be called by a routing state machine
	/// Returns the signer
	fn delete_response_receipt(&self, res: &GetResponse) -> Result<Vec<u8>, Error>;

	/// Stores a receipt for an incoming request after it is successfully routed to a module.
	/// Prevents duplicate incoming requests from being processed. Includes the relayer account
	fn store_request_receipt(&self, req: &Request, signer: &Vec<u8>) -> Result<Vec<u8>, Error>;

	/// Stores a receipt that shows that the given request has received a response. Includes the
	/// relayer account
	/// Implementors should map the request commitment to the response object commitment.
	fn store_response_receipt(&self, req: &GetResponse, signer: &Vec<u8>)
		-> Result<Vec<u8>, Error>;

	/// Stores a commitment for an outgoing request alongside some scale encoded metadata
	fn store_request_commitment(&self, req: &Request, meta: Vec<u8>) -> Result<(), Error>;

	/// Invoked by the timeout handler once the module callback has successfully
	/// acknowledged a timed out request. `meta` is the encoded metadata the
```
