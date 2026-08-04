### Title
Unvalidated destination `StateMachine` in `dispatch_request` permanently locks escrowed relayer fees - (File: `modules/pallets/ismp/src/dispatcher.rs`, `modules/pallets/ismp/src/impls.rs`)

### Summary
The `RequestOracle` report's core defect is: a public dispatch entrypoint accepts an attacker/user-controlled destination parameter with no whitelist or existence check, letting requests be sent to non-existing chains and wasting resources. The local analog is `pallet_ismp`'s `IsmpDispatcher::dispatch_request` / `Pallet::<T>::dispatch_request`, which accepts an arbitrary `dest: StateMachine` from any caller and neither validates that a consensus client exists for it nor bounds `to`/`from`. Because the timeout path (`ismp::handlers::timeout::handle`) requires a valid consensus-client proof for that exact `dest`/`state_id` before a refund can be processed, dispatching to a non-existent/unregistered `StateMachine` makes the request forever undeliverable AND forever untimeoutable, permanently locking the escrowed relayer fee.

### Finding Description
`dispatch_request` in `modules/pallets/ismp/src/dispatcher.rs` (lines 92-151) collects a relayer fee up front: [1](#0-0) 
and builds a `PostRequest`/`GetRequest` using the caller-supplied `dispatch_post.dest` / `dispatch_get.dest` verbatim, with no check that this `StateMachine` corresponds to a chain the host actually has a consensus client for.

It then calls `Pallet::<T>::dispatch_request`, which only checks for a duplicate commitment before storing the request and its `FeeMetadata` (containing the escrowed fee) in `RequestCommitments`: [2](#0-1) 

No check exists anywhere in this dispatch path that `dest_chain` matches a `consensus_client.state_machine(dest_chain)` entry (the same check that IS performed defensively elsewhere, e.g. `check_state_machine_client` in `modules/ismp/core/src/handlers/timeout.rs` lines 40-45 and `modules/ismp/core/src/handlers/request.rs` lines 40-45).

The only way to recover the escrowed fee is via the timeout path, which requires `validate_state_machine(host, timeout_proof.height)` to succeed — this in turn requires `host.consensus_client_id(proof_height.id.consensus_state_id)` to resolve to a real, configured consensus client for that state machine: [3](#0-2) 
and additionally in `timeout::handle`, the destination must either equal the proof's `state_id` directly or be an allowed proxy with a state-machine client: [4](#0-3) 

If `dest` is a bogus/unregistered `StateMachine` (e.g. an EVM chain-id or Substrate para-id that Hyperbridge never configured a client for), no relayer can ever produce a valid delivery proof (no chain exists to deliver to) and no timeout proof can ever be constructed (no consensus client exists to prove non-membership on that "chain"). The request commitment — and the fee locked with it in `RequestCommitments` — remains permanently stuck with no code path to reclaim it.

This is reachable by any unprivileged, signed account through any pallet built on `IsmpDispatcher`, e.g. `pallet_ismp_demo::transfer`/`dispatch_to_evm` (`modules/pallets/demo/src/lib.rs` lines 131-183, 216-239) or the documented `send_message` extrinsic pattern (`docs/content/developers/polkadot/dispatching.mdx` lines 57-76), all of which forward user-supplied `dest`/`params.destination` straight into `dispatch_request` with a non-zero fee and no destination validation.

### Impact Explanation
This is a direct, permanent loss of user funds (the escrowed relayer fee, and for token-transfer-style dispatchers like `pallet_ismp_demo::transfer`, potentially burned/transferred principal that can never be delivered or refunded because `on_timeout` — which would revert the burn — can never be triggered). It matches the bounty's "stealing or loss of funds" and "false state acceptance" categories: the protocol accepts and commits to a request for a state machine that was never validated to exist, silently converting a temporary escrow into a permanent loss. No malicious relayer, prover, or governance actor is required — a single unprivileged extrinsic call is sufficient.

### Likelihood Explanation
High. This requires only a single signed extrinsic call with an out-of-range or unconfigured `StateMachine` id (e.g., an EVM chain ID or parachain ID that isn't in Hyperbridge's active consensus-client set) and a non-zero fee/timeout. It requires no relayer collusion, no proof forgery, and no privileged access — it is directly reachable by any end user via `pallet_ismp_demo` or any application pallet built on the standard `IsmpDispatcher` interface, and there is no on-chain safeguard preventing it (unlike the request/timeout *inbound* handlers, which do defensively check `check_state_machine_client`, the outbound dispatch path skips this entirely).

### Recommendation
Add an explicit destination-validation step in `Pallet::<T>::dispatch_request` (and/or in the `IsmpDispatcher` impl in `modules/pallets/ismp/src/dispatcher.rs`) that rejects any `dest` for which `host.consensus_clients().iter().find_map(|c| c.state_machine(dest).ok())` returns `None`, mirroring the `check_state_machine_client` logic already used defensively in the inbound request/timeout handlers. This closes the exact gap the external report calls out (no whitelist/existence check on the destination parameter of a fee-escrowing dispatch entrypoint) before any fee is collected or commitment stored.

### Proof of Concept
1. Deploy/observe a Hyperbridge parachain runtime with `pallet_ismp` configured with consensus clients only for chains A and B.
2. Call `pallet_ismp_demo::dispatch_to_evm` (or any `IsmpDispatcher`-based extrinsic) with `params.destination` set to an EVM chain id that has no registered consensus client on this host (e.g. `999999`), and a non-zero `fee`.
3. `dispatch_request` (in `modules/pallets/ismp/src/dispatcher.rs`/`impls.rs`) succeeds unconditionally: fee is transferred to `RELAYER_FEE_ACCOUNT`, and a `RequestCommitments` entry is stored keyed by the request commitment, with no check that chain `999999` is registered.
4. No relayer can ever deliver this request (chain doesn't exist/isn't tracked).
5. Attempt to submit a `TimeoutMessage::Post` for this request: `validate_state_machine` in `modules/ismp/core/src/handlers.rs` fails because `host.consensus_client_id` cannot resolve a consensus client for `dest = 999999`, so the timeout handler errors out before ever reaching the refund logic in `timeout::handle`.
6. The fee (and, for pallets like `pallet_ismp_demo::transfer` that burn tokens before dispatch, the burned principal) is permanently locked with no code path to reclaim it — confirmed by inspection of `modules/ismp/core/src/handlers/timeout.rs` (lines 50-67) and `modules/ismp/core/src/handlers.rs` (lines 121-148), which require a resolvable consensus client for the exact `dest`/`state_id` before any timeout/refund logic can run.

### Citations

**File:** modules/pallets/ismp/src/dispatcher.rs (L92-106)
```rust
	fn dispatch_request(
		&self,
		request: DispatchRequest,
		fee: FeeMetadata<T>,
	) -> Result<H256, anyhow::Error> {
		// collect payment for the request
		if fee.fee != Zero::zero() {
			T::Currency::transfer(
				&fee.payer,
				&RELAYER_FEE_ACCOUNT.into_account_truncating(),
				fee.fee,
				Preservation::Expendable,
			)
			.map_err(|err| IsmpError::Custom(format!("Error withdrawing request fees: {err:?}")))?;
		}
```

**File:** modules/pallets/ismp/src/impls.rs (L90-121)
```rust
	pub fn dispatch_request(request: Request, meta: FeeMetadata<T>) -> Result<H256, ismp::Error> {
		let commitment = hash_request::<Pallet<T>>(&request);

		if RequestCommitments::<T>::contains_key(commitment) {
			Err(ismp::Error::Custom("Duplicate request".to_string()))?
		}

		let (dest_chain, source_chain, nonce) =
			(request.dest_chain(), request.source_chain(), request.nonce());
		let leaf_index_and_pos = T::OffchainDB::push(Leaf::Request(request));
		// Deposit Event
		Pallet::<T>::deposit_event(Event::Request {
			request_nonce: nonce,
			source_chain,
			dest_chain,
			commitment,
		});

		RequestCommitments::<T>::insert(
			commitment,
			RequestMetadata {
				offchain: LeafIndexAndPos {
					leaf_index: leaf_index_and_pos.index,
					pos: leaf_index_and_pos.position,
				},
				fee: meta,
				claimed: false,
			},
		);

		Ok(commitment)
	}
```

**File:** modules/ismp/core/src/handlers.rs (L121-148)
```rust
pub fn validate_state_machine<H>(
	host: &H,
	proof_height: StateMachineHeight,
) -> Result<Box<dyn StateMachineClient>, Error>
where
	H: IsmpHost,
{
	// Ensure consensus client is not frozen
	let consensus_client_id = host.consensus_client_id(proof_height.id.consensus_state_id).ok_or(
		Error::ConsensusStateIdNotRecognized {
			consensus_state_id: proof_height.id.consensus_state_id,
		},
	)?;
	let consensus_client = host.consensus_client(consensus_client_id)?;
	// Ensure client is not frozen
	host.is_consensus_client_frozen(proof_height.id.consensus_state_id)?;

	// Ensure delay period has elapsed
	if !verify_delay_passed(host, &proof_height)? {
		return Err(Error::ChallengePeriodNotElapsed {
			state_machine_id: proof_height.id,
			current_time: host.timestamp(),
			update_time: host.state_machine_update_time(proof_height)?,
		});
	}

	consensus_client.state_machine(proof_height.id.state_id)
}
```
