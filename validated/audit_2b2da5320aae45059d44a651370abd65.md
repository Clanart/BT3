### Title
Per-request 32-byte floor in `pallet-messaging-incentives` lets a relayer multiply reputation-token minting by splitting payloads into many minimal requests - ([File: modules/pallets/messaging-incentives/src/lib.rs])

### Summary
`pallet-messaging-incentives::on_executed` mints `ReputationAsset` to the relayer that delivered a message, proportional to `MintPerByte × bytes`, where `bytes` is computed **per individual request** with a floor of `max(body.len(), 32)`. Because the floor is applied per request rather than per delivered byte of actual content, an attacker can split a small logical payload into many tiny (or empty) requests, each independently hitting the 32-byte floor, and collect a multiple of the reputation that the real content size would justify. This is the same "split to exploit a fixed floor/base component" primitive as the external DittoETH report's issue 2/3 (splitting redemptions to exploit the fixed 0.5% base fee).

### Finding Description
`Pallet::message_bytes` sums `max(p.body.len() as u32, 32)` over every request inside a `Message::Request`: [1](#0-0) 

The pallet's own doc comment acknowledges the "one envelope vs many envelopes" case is neutral ("applying the floor once per envelope would let a relayer inflate the mint by splitting"), but it only guards against envelope-level splitting. It does **not** guard against **request-level splitting of the underlying payload**: if a relayer (or the sender they cooperate with) breaks a logically-single small message into `N` separate requests with tiny/empty bodies, each of those `N` requests is independently floored to 32 bytes in `message_bytes`, and `on_executed` mints `rate × 32 × N` instead of `rate × max(total_actual_bytes, 32)`: [2](#0-1) 

For example, a payload that would naturally cost `max(10, 32) = 32` mint-bytes as a single request, if split into 10 one-byte requests, costs `10 × 32 = 320` mint-bytes — a 10x inflation, unbounded by the number of splits the attacker is willing to submit. The relayer's own account is recovered from the message's `signer` field and is the party credited: [3](#0-2) 

The existing test suite confirms the floor mechanic operates exactly as described (32-byte floor applied even for an empty body) but only tests the single-request case, not the repeated-request/multiplicative case: [4](#0-3) 

### Impact Explanation
This is a "logic attack" on a relayer-reward accounting mechanism: an unprivileged relayer can mint `ReputationAsset` far in excess of the real bytes it delivered, simply by fragmenting requests. `ReputationAsset` is minted directly by `fungible::Mutate::mint_into`, so the inflation is a genuine, permanent, unbacked token creation each time the exploit is run — this is a direct case of a reward moving in an amount other than the "rightful ... amount" called out in the Hyperbridge Pivots for relayer rewards/bandwidth-style incentive accounting. I was not able to fully verify within this scan what `ReputationAsset` currently backs economically at runtime (the crate's own doc notes `pallet-collator-manager`'s consumption of the sibling `IncentivesManager::reset_incentives` trait is presently a no-op "since this version doesn't accumulate per-session state"), so the downstream blast radius (e.g., whether it currently feeds collator selection/staking weight/treasury payouts) could not be confirmed with certainty from the indexed code and should be checked directly in a full session before triage.

### Likelihood Explanation
High feasibility, low cost: any account capable of dispatching and relaying ISMP POST requests (a permissionless role) can trivially construct many minimal-body requests instead of one and sign/deliver them, immediately multiplying its minted reward with no need for a malicious peer, prover, or governance actor — this only requires the ordinary permissionless relaying flow already exercised by `FeeHandler::on_executed`.

### Recommendation
Compute the byte-floor once per delivered **message** (or per logical payload) rather than once per individual request within the message, or apply a floor to the aggregate `sum(body.len())` across the whole batch before flooring, so that splitting a payload into more, smaller requests cannot increase the total minted amount. Consider also capping mint credit to actual aggregate on-wire bytes delivered per relayer per block/epoch to remove the incentive to fragment traffic purely for reward-farming.

### Proof of Concept
1. Governance sets `MintPerByte = 1` via `set_mint_per_byte`.
2. Attacker (as relayer) prepares a `Message::Request` whose `requests` vector contains 10 `PostRequest`s, each with `body.len() == 1` (or empty), signed with the attacker's sr25519 key as `signer`.
3. Attacker submits this to `on_executed` (via the normal relayer message-execution path).
4. `message_bytes` computes `10 × max(1, 32) = 320` instead of the "honest" single-request equivalent of `max(10, 32) = 32`.
5. `ReputationMinted { relayer: attacker, bytes: 320, amount: 320 }` is emitted and 320 units are minted to the attacker's account — a 10x reward versus submitting the same content as one request, reproducible for arbitrarily large multipliers by increasing the number of split requests, following exactly the pattern verified by the existing `on_executed_applies_thirty_two_byte_floor` test at [5](#0-4)  but repeated N times within one `Message::Request`.

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

**File:** modules/pallets/testsuite/src/tests/pallet_messaging_incentives.rs (L117-134)
```rust
/// The bandwidth gate counts a 32-byte minimum even on empty bodies;
/// the mint follows the same floor so undersized payloads can't sneak
/// in for free.
#[test]
fn on_executed_applies_thirty_two_byte_floor() {
	new_test_ext().execute_with(|| {
		let relayer_pair = sr25519::Pair::from_seed(&[8u8; 32]);
		let relayer_account = AccountId32::new(relayer_pair.public().0);
		setup_relayer_and_asset(&relayer_account);

		MessagingRelayerIncentives::set_mint_per_byte(RuntimeOrigin::root(), 1).unwrap();

		let msg = signed_request(&relayer_pair, vec![]);
		MessagingRelayerIncentives::on_executed(vec![msg], vec![]).unwrap();

		assert_eq!(relayer_balance(&relayer_account), 32);
	});
}
```
