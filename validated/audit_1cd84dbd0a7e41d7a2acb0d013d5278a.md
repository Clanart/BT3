### Title
Reputation minting in `pallet-messaging-incentives` is priced by raw byte count, not by relayer fee actually paid, letting a self-relaying attacker farm unlimited Collator-selection reputation for free - (File: `modules/pallets/messaging-incentives/src/lib.rs`)

### Summary
The external report's root cause is that a fee/reward proxy (`data.length`) is disconnected from the real economic cost/value of the transaction it is supposed to price, letting an actor pay less than intended. The same disconnect exists in Hyperbridge's `pallet-messaging-incentives`: it mints `ReputationAsset` purely as `bytes × MintPerByte`, with `bytes` taken only from `PostRequest.body.len()` (floored at 32), completely independent of whether any relayer fee was ever paid for that message. Because self-relaying with `fee: 0` is an explicitly supported, unprivileged workflow, a single actor can act as both the dispatching app and the delivering relayer and mint arbitrary amounts of reputation for the cost of gas alone.

### Finding Description
`pallet-messaging-incentives::on_executed` (the `FeeHandler` invoked by `pallet-ismp` after every batch) computes the mint amount from `Self::message_bytes(&mw.message)`, which sums `max(body.len(), 32)` across the delivered `PostRequest`s, and mints `rate * bytes` of `ReputationAsset` to whichever account's sr25519 signature is on the message's `signer` field: [1](#0-0) [2](#0-1) 

Nothing in this path checks `DispatchPost.fee`/`FeeMetadata` or requires that a relayer fee was ever collected for the message being minted against. The `relayer_for` helper simply recovers whoever signed the delivered `Message::Request` envelope — i.e., whoever ran the relayer client that submitted it to Hyperbridge: [3](#0-2) 

Hyperbridge's own documentation confirms self-relaying with a zero fee is a first-class, unprivileged option — "Apps that prefer to self-relay can leave the fee at zero," and relayer fees are entirely optional on both EVM and Substrate dispatch paths: [4](#0-3) [5](#0-4) 

This means a single unprivileged account can:
1. Deploy/act as a trivial ISMP application on any source chain and dispatch `PostRequest`s with large, arbitrary `body` payloads and `fee = 0`.
2. Run its own relayer/collator node, sign the resulting `Message::Request` envelope with its own sr25519 key, and deliver it to Hyperbridge.
3. Collect `bytes × MintPerByte` in `ReputationAsset` for every such delivery, with no requirement that any real relayer fee, bandwidth purchase, or other economic value backed the message.

`ReputationAsset` is not a cosmetic score — it is the sole input to Collator (block producer) selection in `pallet-collator-manager::new_session`, which ranks and selects the highest-reputation controllers each session and burns the winners' balances: [6](#0-5) 

Hyperbridge's own documentation states the model is meant to reward "proven economic activity" where reputation "mirrors your $BRIDGE earnings at a 1:1 ratio" for operators who deliver genuine, paid relaying/proving work: [7](#0-6) 

The code, however, mints reputation from raw byte counts of self-dispatched, unpaid messages — exactly the "undercounted/decoupled cost proxy" bug class from the external report, but here it manifests as an uncapped, cost-free *reward* rather than an underpriced fee.

### Impact Explanation
`ReputationAsset` directly determines who is selected into the Collator set, which produces blocks, signs BEEFY/consensus artifacts, and runs the in-node "fisherman" fraud-detection task that vetoes malicious state commitments. An attacker who can mint unlimited reputation for the price of gas (no real fee, no bandwidth purchase, no genuine relaying service) can outrank legitimate operators and get itself selected as Collator without ever having provided real economic value or reliable infrastructure to the network. This is a logic attack on validator/host-management selection integrity — an unprivileged actor obtaining a trusted, block-producing role through a purely mechanical accounting flaw rather than genuine stake or service, undermining the meritocratic security assumption the whole collator-selection design relies on.

### Likelihood Explanation
High. No privileged role, malicious peer, or compromised relayer is required — self-relaying with `fee = 0` is a documented, intended feature, and running a relayer client to sign and submit one's own messages is a normal permissionless operation. The only cost to the attacker is source-chain gas plus Hyperbridge execution weight for however many/large the padded `body` fields are, which is far cheaper than sustained, real, market-priced relaying service.

### Recommendation
Tie `pallet-messaging-incentives` minting to actual value paid/consumed rather than raw `body.len()`:
- Gate minting on the `FeeMetadata`/relayer fee actually collected for the message (e.g., mint proportional to fee paid, not bytes), or
- Require the message to have consumed real bandwidth (i.e., reuse the `pallet-bandwidth` deduction as proof of paid usage) before minting reputation, or
- At minimum, exclude self-relayed / zero-fee messages from reputation minting so that Collator-selection reputation cannot be farmed independent of genuine paid relaying activity.

### Proof of Concept
1. Deploy a trivial ISMP application on any connected source chain (or reuse `pallet_ismp_demo`).
2. Repeatedly dispatch `PostRequest`s to Hyperbridge with `fee = 0` and a large `body` (e.g., tens of KB), as shown in the test helper that builds `signed_request`: [8](#0-7) 
3. Run/operate the relayer that delivers the batch to Hyperbridge, signing the `Message::Request` with the attacker's own sr25519 controller key.
4. `MessagingRelayerIncentives::on_executed` mints `MintPerByte × bytes` of `ReputationAsset` to the attacker's controller account regardless of the `fee = 0`, as demonstrated by the existing test asserting proportional minting purely from body size: [9](#0-8) 
5. Repeat at will to accumulate `ReputationAsset` far beyond what any genuine paid relayer earns, then use that balance at the next session boundary to outrank legitimate candidates in `pallet_collator_manager::new_session`'s reputation-sorted selection.

### Citations

**File:** modules/pallets/messaging-incentives/src/lib.rs (L121-135)
```rust
	/// Same minimum-byte rule as the bandwidth gate (`max(body, 32)`),
	/// applied **per request** so packing requests into one envelope
	/// vs. splitting them across many produces identical mints.
	/// Applying the floor once per envelope would let a relayer inflate
	/// the mint by splitting (each split picks up its own 32-byte floor).
	fn message_bytes(message: &Message) -> u32 {
		match message {
			Message::Request(req) => req
				.requests
				.iter()
				.map(|p| core::cmp::max(p.body.len() as u32, 32))
				.sum::<u32>(),
			_ => 0,
		}
	}
```

**File:** modules/pallets/messaging-incentives/src/lib.rs (L137-153)
```rust
	/// Recover the relayer's account from the sr25519 signature on a
	/// `Message`'s `signer` field. Returns `None` if the message has
	/// no signer (e.g. consensus messages) or the signature is bad.
	fn relayer_for(message: &Message) -> Option<T::AccountId> {
		let (signer, signed) = match message {
			Message::Request(msg) =>
				(&msg.signer, sp_io::hashing::keccak_256(&msg.requests.encode())),
			Message::Response(msg) =>
				(&msg.signer, sp_io::hashing::keccak_256(&msg.requests.encode())),
			_ => return None,
		};
		Signature::decode(&mut &signer[..])
			.ok()?
			.verify_and_get_sr25519_pubkey(&signed, None)
			.ok()
			.map(T::AccountId::from)
	}
```

**File:** modules/pallets/messaging-incentives/src/lib.rs (L160-186)
```rust
	fn on_executed(
		messages: Vec<MessageWithWeight>,
		_events: Vec<IsmpEvent>,
	) -> DispatchResultWithPostInfo {
		let rate = MintPerByte::<T>::get();
		if !rate.is_zero() {
			for mw in &messages {
				let bytes = Self::message_bytes(&mw.message);
				let bytes_balance: BalanceOf<T> = (bytes as u128).saturated_into();
				let amount = rate.saturating_mul(bytes_balance);
				if amount.is_zero() {
					continue;
				}
				if let Some(relayer) = Self::relayer_for(&mw.message) {
					match T::ReputationAsset::mint_into(&relayer, amount) {
						Ok(_) =>
							Self::deposit_event(Event::ReputationMinted { relayer, bytes, amount }),
						Err(err) => log::warn!(
							target: "messaging-incentives",
							"reputation mint failed for {bytes}b: {err:?}",
						),
					}
				}
			}
		}
		Ok(PostDispatchInfo { actual_weight: None, pays_fee: Pays::No })
	}
```

**File:** docs/content/developers/polkadot/fees.mdx (L9-11)
```text
## Relayer Fees

The relayer fee is an optional incentive provided by applications initiating cross-chain transactions. It compensates Hyperbridge's decentralized relayers for delivering messages to the destination chain. Apps that prefer to self-relay can leave the fee at zero.
```

**File:** docs/content/developers/evm/messaging/post-requests.mdx (L81-90)
```text
## Fees

The fee model for POST requests is straightforward: you only pay an **optional relayer fee** to incentivize third-party relayers to deliver your message.

| Component | Description | Refundable |
|-----------|-------------|------------|
| **Relayer Fee** | Optional incentive for third-party relayers to deliver your message, set by the application in `DispatchPost.fee` | ✅ Yes (on timeout) |

The fee is collected by the `IDispatcher` contract from the caller.

```

**File:** modules/pallets/collator-manager/src/lib.rs (L503-542)
```rust
		fn new_session(_new_index: SessionIndex) -> Option<Vec<T::AccountId>> {
			T::IncentivesManager::reset_incentives();

			let desired_collators = core::cmp::max(
				pallet_collator_selection::DesiredCandidates::<T>::get(),
				<T as pallet_collator_selection::Config>::MinEligibleCollators::get(),
			) as usize;

			// Rank candidate controllers that have session keys by reputation, highest first.
			// We keep every eligible candidate, even those with no reputation, so the set never
			// shrinks below what's needed to keep producing blocks; reputation only orders them.
			let mut candidates = pallet_collator_selection::CandidateList::<T>::get()
				.into_iter()
				.map(|info| info.who)
				.filter(|stash_account| !Unbonding::<T>::contains_key(stash_account))
				.filter_map(|stash_account| Controller::<T>::get(&stash_account))
				.filter(|controller_account| {
					!RemovedValidators::<T>::contains_key(controller_account) &&
						pallet_session::NextKeys::<T>::get(controller_account.clone().into())
							.is_some()
				})
				.map(|controller_account| {
					(T::ReputationAsset::balance(&controller_account), controller_account)
				})
				.collect::<Vec<_>>();

			candidates.sort_by_key(|(balance, _)| *balance);

			// Invulnerables always collate unless root has removed them; the highest reputation
			// candidates fill the rest.
			let mut new_set: Vec<T::AccountId> =
				pallet_collator_selection::Invulnerables::<T>::get()
					.into_iter()
					.filter(|validator| !RemovedValidators::<T>::contains_key(validator))
					.collect();
			for (_, controller) in candidates.into_iter().rev().take(desired_collators) {
				if !new_set.contains(&controller) {
					new_set.push(controller);
				}
			}
```

**File:** docs/content/developers/network/collator.mdx (L60-67)
```text
- **The Hyperbridge Token (`$BRIDGE`)**: This is the primary token earned as a reward for operating network infrastructure. It is a real, transferable asset that reflects your earnings.
    - Your Stash account must bond `$BRIDGE` to become a candidate.
    - Your Controller account receives `$BRIDGE` rewards for producing blocks.
    - Your Controller account earns `$BRIDGE` for relaying messages, submitting consensus proofs, or generating BEEFY proofs.

- **The Reputation Asset**: This is a special, non-transferable asset that acts as a reputation score. It mirrors your `$BRIDGE` earnings at a 1:1 ratio. For every `$BRIDGE` token you earn through any of the three operator roles (messaging relayer, consensus relayer, or BEEFY prover), an equal amount of Reputation Asset is automatically minted to your Controller account.

**Key Insight**: Your reputation score is directly tied to your success as a network operator. The more messages you relay, consensus proofs you submit, or BEEFY proofs you generate, the more `$BRIDGE` you earn, the higher your reputation score becomes, and the better your chances of being selected as a Collator.
```

**File:** modules/pallets/testsuite/src/tests/pallet_messaging_incentives.rs (L44-80)
```rust
/// Builds a `MessageWithWeight` that the slim pallet's `on_executed`
/// will treat as relayer-signed — the relayer account derives from
/// the sr25519 signature on the encoded `requests`.
fn signed_request(relayer: &sr25519::Pair, body: Vec<u8>) -> MessageWithWeight {
	let post = PostRequest {
		source: SOURCE,
		dest: DEST,
		nonce: 0,
		from: vec![1; 32],
		to: vec![2; 32],
		timeout_timestamp: 100,
		body,
	};
	let requests = vec![post];
	let signed = keccak_256(&requests.encode());
	let sig = relayer.sign(&signed);
	let signer = Signature::Sr25519 {
		public_key: relayer.public().to_raw_vec(),
		signature: sig.to_raw_vec(),
	}
	.encode();

	MessageWithWeight {
		message: Message::Request(RequestMessage {
			requests,
			proof: Proof {
				height: StateMachineHeight {
					id: StateMachineId { state_id: SOURCE, consensus_state_id: *b"mock" },
					height: 1,
				},
				proof: vec![],
			},
			signer,
		}),
		weight: Weight::zero(),
	}
}
```

**File:** modules/pallets/testsuite/src/tests/pallet_messaging_incentives.rs (L99-115)
```rust
#[test]
fn on_executed_mints_reputation_proportional_to_bytes() {
	new_test_ext().execute_with(|| {
		let relayer_pair = sr25519::Pair::from_seed(&[7u8; 32]);
		let relayer_account = AccountId32::new(relayer_pair.public().0);
		setup_relayer_and_asset(&relayer_account);

		let rate: u128 = 3;
		MessagingRelayerIncentives::set_mint_per_byte(RuntimeOrigin::root(), rate).unwrap();

		let body = vec![0u8; 100];
		let msg = signed_request(&relayer_pair, body.clone());
		MessagingRelayerIncentives::on_executed(vec![msg], vec![]).unwrap();

		assert_eq!(relayer_balance(&relayer_account), rate * 100);
	});
}
```
