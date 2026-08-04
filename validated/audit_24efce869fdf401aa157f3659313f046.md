## Title
Attacker-controlled `signer` field lets any message bypass the weight-based bandwidth fee, breaking bandwidth/fee accounting invariants - (File: `modules/pallets/ismp/src/fee_handler.rs`)

### Summary
`WeightFeeHandler::on_executed` only recognizes `Signature::Sr25519` when trying to recover the fee-paying account from a processed `Message::Request`/`Message::Response`. Since `verify_and_get_sr25519_pubkey` unconditionally errors for any other `Signature` variant, and since the `signer` bytes embedded in `RequestMessage`/`ResponseMessage` are supplied by the unprivileged message submitter itself, any submitter can trivially avoid the fee charge for a fully processed, weight-consuming message by encoding `signer` as `Signature::Evm`, `Signature::Ed25519`, or any undecodable bytes.

### Finding Description
`WeightFeeHandler::on_executed` computes `fee = W::weight_to_fee(&weight)` per message and tries to recover the payer account: [1](#0-0) 

The lookup relies on `Signature::verify_and_get_sr25519_pubkey`, which is hard-coded to reject every variant except `Sr25519`: [2](#0-1) 

The `signer` field of `RequestMessage`/`ResponseMessage` is populated by whoever constructs and submits the message (a relayer/submitter through the unsigned `handle_unsigned` extrinsic), not by any protocol-enforced identity — the docs even describe it as "should be their account identifier" (best-effort, not enforced): [3](#0-2) 

Because `handle_unsigned` messages are processed for free at the extrinsic-fee layer (this `FeeHandler` is the *only* mechanism debiting a real account for bandwidth usage), an unprivileged submitter can set `signer` to an `Evm`/`Ed25519`-typed `Signature` (or arbitrary garbage that fails `Signature::decode`). This makes `originator` resolve to `None`, so the `if let Some(originator_bytes) = originator { ... }` transfer block is skipped entirely, while the request/response is still verified, dispatched to `IsmpModule::on_accept`/`on_response`, and its receipt is persisted — i.e., the underlying cross-chain message succeeds normally.

The existing test confirms the intended, correct-path behavior (Sr25519 signer → debited, treasury credited): [4](#0-3) 

But nothing in `on_executed` forces `signer` to be an `Sr25519` signature, and no error is raised or message rejected when `originator` is `None` — the function silently proceeds and still returns `Ok(PostDispatchInfo { actual_weight: Some(total_weight), pays_fee: Pays::Yes })`.

### Impact Explanation
This breaks the "bandwidth balances must move exactly once and only to the rightful beneficiary and amount" invariant: the weight-consumption cost of processing the message is never collected, yet the message is fully executed as if it had paid. Every unprivileged caller of `handle_unsigned` can process arbitrarily weight-costly `Request`/`Response` batches at zero cost to themselves by simply choosing an `Evm`/`Ed25519` `Signature` type (or invalid bytes) for `signer`, permanently corrupting the fee/bandwidth accounting that is meant to fund the treasury and disincentivize spam.

### Likelihood Explanation
Trivial to trigger: the attacker only needs to control the `signer` bytes of a message they are already submitting through the standard unsigned message flow — no proof forgery, no privileged access, and no interaction with other users' funds required. The relevant "check" (`verify_and_get_sr25519_pubkey` only matching `Sr25519`) is deterministic and always reachable.

### Recommendation
- In `WeightFeeHandler::on_executed`, use `Signature::verify` (which supports all three variants) instead of `verify_and_get_sr25519_pubkey`, or explicitly branch on all `Signature` variants to recover a payer account.
- If `originator` cannot be resolved (decode/verify failure) for a message that has non-zero `fee`, reject/short-circuit the batch (return an error) rather than silently skipping the charge, so submitters cannot opt out of paying by supplying a malformed or wrong-type signer.

### Proof of Concept
1. Construct a `PostRequest`/`RequestMessage` as in `should_charge_fee_for_request`, but instead of a `Signature::Sr25519 {..}`, set `signer` to `Signature::Ed25519 { public_key, signature }.encode()` (or `Signature::Evm{..}`, or simply `vec![]`).
2. Call `pallet_ismp::Pallet::<Test>::handle_unsigned(RuntimeOrigin::none(), vec![Message::Request(request_message)])`.
3. Observe the request is processed successfully (module `on_accept` executed, receipt stored, events emitted), but `Balances::balance(&treasury_account)` is unchanged from `initial_treasury_balance`, despite `weight_to_fee(&weight)` being non-zero — confirming the fee/bandwidth accounting invariant is bypassed. [5](#0-4)

### Citations

**File:** modules/pallets/ismp/src/fee_handler.rs (L168-212)
```rust
	fn on_executed(
		messages: Vec<MessageWithWeight>,
		_events: Vec<Event>,
	) -> DispatchResultWithPostInfo {
		if !POLICY {
			return Ok(PostDispatchInfo { actual_weight: None, pays_fee: Pays::No })
		}
		let mut total_weight = Weight::zero();
		let treasury_account: AccountId = T::get().into_account_truncating();

		for message in &messages {
			let weight = message.weight;
			total_weight.saturating_accrue(weight);
			let fee = W::weight_to_fee(&weight);

			if fee.is_zero() {
				continue
			}

			let originator = match message.message.clone() {
				Message::Request(msg) => {
					let data = sp_io::hashing::keccak_256(&msg.requests.encode());
					Signature::decode(&mut &msg.signer[..])
						.ok()
						.and_then(|sig| sig.verify_and_get_sr25519_pubkey(&data, None).ok())
				},
				Message::Response(msg) => {
					let data = sp_io::hashing::keccak_256(&msg.requests.encode());
					Signature::decode(&mut &msg.signer[..])
						.ok()
						.and_then(|sig| sig.verify_and_get_sr25519_pubkey(&data, None).ok())
				},
				_ => None,
			};

			if let Some(originator_bytes) = originator {
				if let Ok(account) = AccountId::decode(&mut &originator_bytes[..]) {
					C::transfer(&account, &treasury_account, fee, ExistenceRequirement::KeepAlive)
						.map_err(|_| DispatchError::Other("Failed to transfer fee to treasury"))?;
				}
			}
		}

		Ok(PostDispatchInfo { actual_weight: Some(total_weight), pays_fee: Pays::Yes })
	}
```

**File:** modules/utils/crypto/src/verification.rs (L97-107)
```rust
	pub fn verify_and_get_sr25519_pubkey(
		&self,
		msg: &[u8; 32],
		public_key_op: Option<Vec<u8>>,
	) -> Result<[u8; 32], anyhow::Error> {
		match self {
			Signature::Sr25519 { public_key, signature } =>
				Self::verify_sr25519(signature, public_key, msg, &public_key_op),
			_ => Err(anyhow!("Signature is not of type Sr25519")),
		}
	}
```

**File:** docs/content/protocol/ismp/requests.mdx (L82-91)
```text
```rust showLineNumbers
/// A request message holds a batch of incoming requests and their proofs.
pub struct RequestMessage {
    /// POST requests from a source chain
    pub requests: Vec<PostRequest>,
    /// Membership batch proof for these requests
    pub proof: Proof,
    /// Signer information. Ideally should be their account identifier.
    pub signer: Vec<u8>,
}
```

**File:** modules/pallets/testsuite/src/tests/pallet_ismp.rs (L608-668)
```rust
#[test]
fn should_charge_fee_for_request() {
	new_test_ext().execute_with(|| {
		let host = Ismp::default();
		setup_mock_client::<_, Test>(&host);
		let id = StateMachineId {
			state_id: StateMachine::Evm(1),
			consensus_state_id: MOCK_CONSENSUS_STATE_ID,
		};

		let signer_pair = sp_core::sr25519::Pair::from_string("//Alice", None).unwrap();
		let signer_account: AccountId32 = signer_pair.public().into();
		let initial_balance = 1000 * UNIT;
		Balances::mint_into(&signer_account, initial_balance).unwrap();

		let treasury_pallet_id = TreasuryAccount::get();
		let treasury_account = treasury_pallet_id.into_account_truncating();
		let initial_treasury_balance = Balances::balance(&treasury_account);

		let post_request = PostRequest {
			source: id.state_id,
			dest: host.host_state_machine(),
			nonce: 0,
			from: vec![1; 32],
			to: vec![2; 32],
			timeout_timestamp: 0,
			body: b"body".to_vec(),
		};

		let requests = vec![post_request];
		let signed_data = keccak_256(&requests.encode());
		let signature = signer_pair.sign(&signed_data);
		let signature = Signature::Sr25519 {
			public_key: signer_pair.public().to_raw_vec(),
			signature: signature.to_raw_vec(),
		};

		let request_message = RequestMessage {
			requests,
			proof: Proof {
				height: StateMachineHeight {
					id: StateMachineId { state_id: id.state_id, consensus_state_id: *b"mock" },
					height: 3,
				},
				proof: vec![],
			},
			signer: signature.encode(),
		};

		let message = Message::Request(request_message);

		let expected_fee = 50 * UNIT;

		pallet_ismp::Pallet::<Test>::handle_unsigned(RuntimeOrigin::none(), vec![message]).unwrap();

		let final_signer_balance = Balances::balance(&signer_account);
		let final_treasury_balance = Balances::balance(&treasury_account);

		assert_eq!(final_signer_balance, initial_balance - expected_fee);
		assert_eq!(final_treasury_balance, initial_treasury_balance + expected_fee);
	});
```
