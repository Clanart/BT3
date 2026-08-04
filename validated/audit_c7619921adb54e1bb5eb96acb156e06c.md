This confirms the mechanism: `validate_state_machine` calls `host.is_consensus_client_frozen(...)` before permitting any timeout proof, request, or response to be processed against that consensus state, and freezing is permanent (per the docs: "Frozen consensus clients cannot be unfrozen") [1](#0-0) [2](#0-1) .

### Title
Frozen consensus client permanently locks in-flight relayer fees with no refund path - (File: `modules/ismp/core/src/handlers/timeout.rs`, `modules/ismp/core/src/handlers.rs`)

### Summary
`freeze_client` is a permissionless, fisherman-triggered fraud-proof mechanism that irreversibly marks a consensus state as frozen [3](#0-2) . Once frozen, `validate_state_machine` rejects every subsequent request, response, and **timeout** message tied to that consensus state, because it unconditionally calls `host.is_consensus_client_frozen(...)` before any proof verification [4](#0-3) . `TimeoutMessage::Post` handling calls exactly this same `validate_state_machine` gate against `timeout_proof.height.id.state_id` before it will delete the request commitment and trigger `host.on_request_timeout`, which is what actually refunds the escrowed relayer fee back to the payer [5](#0-4) [6](#0-5) . This is structurally the same broken invariant as the reported `killGauge` bug: a normal, correctly-authorized protocol action (killing a gauge / freezing a client) irrecoverably strands value (`claimable[_gauge]` / the escrowed `RELAYER_FEE_ACCOUNT` balance) that was accounted for under the assumption the entity stays "alive."

### Finding Description
When a `PostRequest` is dispatched with a non-zero relayer fee, the fee is transferred from the payer into `RELAYER_FEE_ACCOUNT` at dispatch time and the commitment/fee metadata is stored under `RequestCommitments` [7](#0-6) . There are exactly two ways this escrow is ever released: (1) successful delivery is acknowledged on the destination and the relayer claims the fee via the accumulation flow, or (2) the request times out, at which point `on_request_timeout` transfers the fee back to the original payer [6](#0-5) .

Both paths for a request whose destination is `state_id` require passing `validate_state_machine(host, height)` for that state machine's consensus state, and this function fails hard the instant that consensus state is frozen [4](#0-3) . `freeze_client` is intentionally permissionless — it is designed to be invokable by any "fisherman" who submits a valid fraud proof of a consensus fault (double signing, eclipse attack, etc.), and the resulting frozen state is explicitly documented as unrecoverable: "Frozen consensus clients cannot be unfrozen and a new consensus client must be initialized" [2](#0-1) .

Consequently, any `PostRequest` whose destination consensus state gets frozen **after** dispatch but **before** either delivery or timeout processing has its escrowed relayer fee stranded forever in `RELAYER_FEE_ACCOUNT` (or the equivalent EVM host contract balance): delivery is blocked because incoming request handling also runs through `validate_state_machine`, and the timeout path that would otherwise refund the payer is blocked by the exact same frozen-client check. Unlike `killGauge` in the original report, this path requires no admin: `freeze_client` is a normal, permissionless, protocol-sanctioned action that any fisherman can trigger the moment a genuine consensus fault is proven — an event entirely outside the request-dispatching user's control.

### Impact Explanation
This is a fund-lock bug fitting the "stealing or loss of funds" bounty category: user-paid relayer fees (and, transitively, the underlying escrowed request funds in flight to that destination) become permanently unrecoverable once the destination's consensus client is frozen, with no code path to sweep, refund, or otherwise recover them. Because consensus freezing is a designed, permissionless safety mechanism (not a bug itself), any legitimate fraud-proof event against a destination chain instantly converts every in-flight relayer fee targeting that chain into permanently locked funds.

### Likelihood Explanation
Likelihood depends on a genuine consensus fault occurring on a destination chain (double-signing/eclipse attack), which is a rare but explicitly anticipated and designed-for event in this protocol (hence the dedicated `freeze_client`/fisherman mechanism). Given that freezing is irreversible by design, any request in flight to that destination at the moment of freezing is affected with certainty — there is no race the payer can win to escape it, since both the delivery and timeout paths use the identical `is_consensus_client_frozen` gate.

### Recommendation
Introduce a dedicated recovery path for requests/responses whose destination consensus state has been frozen: e.g., allow `on_request_timeout`-style refunds to bypass (or use a variant of) `validate_state_machine` that only checks the frozen flag to authorize an unconditional fee refund to the payer, without requiring a (now-unobtainable) non-membership proof against the frozen state. Alternatively, emit the `FrozenClient` event with enough context (affected `StateMachineId`) so that a governance- or protocol-level sweep function can be added to refund `RequestCommitments` fee metadata whose destination matches a frozen consensus state, mirroring Velodrome's fix of returning `claimable` to the `Minter` when a gauge is killed.

### Proof of Concept
1. User dispatches `DispatchPost` to `dest = StateMachine::Evm(D)` with `fee = X`, paid by `payer`. Fee is escrowed to `RELAYER_FEE_ACCOUNT`; `RequestCommitments[commitment]` stores `FeeMetadata { payer, fee: X }` [7](#0-6) .
2. Before the request is delivered or times out, a fisherman submits a valid `FraudProofMessage` for chain `D`'s consensus state; `freeze_client` succeeds and marks that consensus state frozen (irreversibly) [3](#0-2) .
3. Delivery attempts for the request now fail `validate_state_machine`'s frozen check.
4. When the request's `timeout_timestamp` elapses, a relayer submits `TimeoutMessage::Post { requests, timeout_proof }` where `timeout_proof.height.id.state_id = D`; `timeout::handle` calls `validate_state_machine(host, timeout_proof.height)` first, which fails with the frozen-client error before ever reaching `host.delete_request_commitment`/`host.on_request_timeout` [5](#0-4) .
5. `RequestCommitments[commitment]` and its associated fee `X` remain in `RELAYER_FEE_ACCOUNT` permanently — there is no other code path in the reviewed modules that releases it.

Note: I was not able to fully trace the EVM-side (`EvmHost.sol`) equivalent freeze-then-timeout interaction from the index alone (only `notFrozen`/`FrozenStatus` for the host's own freeze state was retrievable, not the EVM light-client freeze-on-fraud-proof code path), so this analysis is grounded primarily in the Substrate `pallet-ismp` / `modules/ismp/core` code shown above; a full audit should verify whether the EVM consensus client implementations exhibit the identical gap.

### Citations

**File:** modules/ismp/core/src/handlers.rs (L128-147)
```rust
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
```

**File:** docs/content/protocol/ismp/consensus.mdx (L196-197)
```text

The `freeze_client` method is used to prove the existence of a consensus fault to an onchain consensus client. This message will be sent by offchain parties, colloquially known as _fishermen_ when they detect the existence of two conflicting views of the network backed by consensus proofs. This may arise from double signing or eclipse attacks. The consensus client after successfully verifying the validity of the conflicting views of the network will go into a frozen state. In this state it can no longer process new consensus messages as well as new requests & responses. Frozen consensus clients cannot be unfrozen and a new consensus client must be initialized through the `create_client` method instead.
```

**File:** modules/ismp/core/src/handlers/consensus.rs (L124-145)
```rust
/// Freeze a consensus client by providing a valid fraud proof.
pub fn freeze_client<H>(host: &H, msg: FraudProofMessage) -> Result<MessageResult, anyhow::Error>
where
	H: IsmpHost,
{
	let consensus_client_id = host
		.consensus_client_id(msg.consensus_state_id)
		.ok_or_else(|| Error::Custom("Unknown Consensus State Id".to_string()))?;

	host.is_consensus_client_frozen(msg.consensus_state_id)?;

	let consensus_client = host.consensus_client(consensus_client_id)?;
	let trusted_state = host.consensus_state(msg.consensus_state_id)?;

	consensus_client.verify_fraud_proof(host, trusted_state, msg.proof_1, msg.proof_2)?;

	host.freeze_consensus_client(msg.consensus_state_id)?;

	host.store_consensus_update_time(msg.consensus_state_id, host.timestamp())?;

	Ok(MessageResult::FrozenClient(msg.consensus_state_id))
}
```

**File:** modules/ismp/core/src/handlers/timeout.rs (L48-52)
```rust
	let results = match msg {
		TimeoutMessage::Post { requests, timeout_proof } => {
			let state_machine = validate_state_machine(host, timeout_proof.height)?;
			let state = host.state_machine_commitment(timeout_proof.height)?;

```

**File:** modules/pallets/ismp/src/host.rs (L322-335)
```rust
	fn on_request_timeout(&self, _req: &Request, meta: Vec<u8>) -> Result<(), Error> {
		let leaf_meta = RequestMetadata::<T>::decode(&mut &*meta)
			.map_err(|_| Error::Custom("Failed to decode leaf metadata".to_string()))?;
		if leaf_meta.fee.fee > Zero::zero() {
			T::Currency::transfer(
				&RELAYER_FEE_ACCOUNT.into_account_truncating(),
				&leaf_meta.fee.payer,
				leaf_meta.fee.fee,
				Preservation::Expendable,
			)
			.map_err(|err| Error::Custom(format!("Failed to refund relayer fee: {err:?}")))?;
		}
		Ok(())
	}
```

**File:** modules/pallets/testsuite/src/tests/pallet_ismp.rs (L438-453)
```rust
		assert_eq!(Balances::balance(&account), Default::default());
		Balances::mint_into(&account, 10 * UNIT).unwrap();
		assert_eq!(Balances::balance(&account), 10 * UNIT);

		host.dispatch_request(
			DispatchRequest::Get(msg.clone()),
			// lets pay 10 units
			FeeMetadata { payer: account.clone().into(), fee: 10 * UNIT },
		)
		.unwrap();

		// we should no longer have it
		assert_eq!(Balances::balance(&account), Default::default());

		// now pallet-ismp has it
		assert_eq!(Balances::balance(&RELAYER_FEE_ACCOUNT.into_account_truncating()), 10 * UNIT);
```
