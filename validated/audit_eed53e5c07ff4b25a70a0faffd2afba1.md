Based on my investigation, I found a concrete local analog.

### Title
Reputation-mint splitting inflates collator-selection weight beyond real relayed volume - ([File: modules/pallets/messaging-incentives/src/lib.rs])

### Summary
`pallet-messaging-incentives` mints `ReputationAsset` to whichever relayer's signature is recovered from a delivered ISMP message, scaled by `max(body_len, 32)` **per individual request**, summed across all requests in the batch [1](#0-0) . This reputation balance directly determines collator-selection ranking in `pallet-collator-manager::new_session`, which sorts candidates by `T::ReputationAsset::balance` and grants collator slots (with real native-currency `CollatorReward` payouts per authored block) to the highest holders [2](#0-1) [3](#0-2) . This is the same broken invariant as the FEI report's core primitive: a per-operation formula containing a fixed floor/quadratic term lets an attacker replace one large operation with many small operations to change the total charge/credit non-linearly.

### Finding Description
`message_bytes` applies a 32-byte floor to **each request independently**, then sums:
```rust
req.requests.iter().map(|p| core::cmp::max(p.body.len() as u32, 32)).sum::<u32>()
``` [4](#0-3) 

Any signed relayer dispatching/delivering their own ISMP requests controls both the number and size of the requests they send. Instead of sending one request with `N` bytes of body (which floors to `max(N,32)`), the same relayer (as the dispatching app or a cooperating app) can split the payload into `k` separate requests each with a tiny body (e.g., 1 byte). Each split request still floors to 32 bytes, so the total counted size becomes `k × 32` instead of `max(N, 32)` — a linear multiplier controlled entirely by the attacker with no cap. The code comment claims this only equalizes "packing vs. splitting into one envelope," but that reasoning only holds when comparing grouping into fewer/more *messages* with the same total request count and sizes; it does not defend against increasing the actual number of underlying requests to inflate the floor term, which is the real lever here.

The mint is credited to whoever signed the message (`relayer_for`, recovered via sr25519 signature on `msg.signer`) [5](#0-4) , and there is no minimum-real-payload check, no cap on requests-per-message, and no check that split requests represent genuinely independent application traffic rather than an artificially fragmented single payload.

### Impact Explanation
`ReputationAsset` is not cosmetic — it is the sole ranking signal `pallet-collator-manager::new_session` uses to select the collator set every session, and it is burned/reset each session so it must be continuously re-earned [6](#0-5) . Winning collator slots grants ongoing native-currency `CollatorReward` payouts from the treasury on every authored block [3](#0-2) , and control of the collator/validator set is itself a host-management-adjacent execution privilege (block production, transaction ordering). An attacker who inflates their reputation via message-splitting can unfairly outrank honest relayers for collator seats and extract disproportionate treasury-funded rewards without delivering proportional real message volume — this is a logic attack on the reward/selection formula, directly forwarding to loss of funds (treasury drain relative to honest work) and unauthorized acquisition of block-production privilege.

### Likelihood Explanation
Any account capable of dispatching and delivering its own ISMP requests (a normal, permissionless relayer/app operation, not requiring a malicious peer, prover, or admin) can perform this unilaterally — no cooperation from other network participants is required, since the attacker controls both the sending app and the relaying/delivery of its own messages. The floor-per-request behavior is explicit, intentional code, so the split-to-inflate lever is always available and cheap (each 1-byte-body request costs only the fixed per-message dispatch/delivery overhead, which the messaging-incentives mint does not offset against).

### Recommendation
Apply the 32-byte floor once per delivered **message/envelope** (or per unique commitment/dispatch context) rather than per sub-request, or cap the number of floor-eligible sub-requests counted per message/per relayer/per epoch. Alternatively, track cumulative real byte volume against minted reputation over a rolling window and only apply the floor when the aggregate real payload for that window is below the floor threshold, so splitting cannot multiply the floor credit.

### Proof of Concept
1. Attacker controls app `A` and also runs (or colludes with) a relayer.
2. Instead of dispatching one legitimate request with body length 500 bytes (mint = `rate × 500`), the attacker dispatches 50 requests each with a 1-byte body in the same batch.
3. `message_bytes` computes `sum(max(1,32) for _ in 50) = 50 × 32 = 1600` instead of `500`.
4. `on_executed` mints `rate × 1600` to the attacker's signer instead of `rate × 500` — over 3× inflation for the same real payload, with the multiplier scaling linearly with however many pieces the attacker chooses to split into [7](#0-6) .
5. Repeated across sessions, the attacker's `ReputationAsset` balance outranks honest relayers in `pallet-collator-manager::new_session`'s sort, winning collator seats and the associated `CollatorReward` treasury payouts [8](#0-7) .

### Citations

**File:** modules/pallets/messaging-incentives/src/lib.rs (L117-135)
```rust
impl<T: Config> Pallet<T>
where
	T::AccountId: From<[u8; 32]>,
{
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

**File:** modules/pallets/messaging-incentives/src/lib.rs (L160-182)
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
```

**File:** modules/pallets/collator-manager/src/lib.rs (L475-497)
```rust
	impl<T: Config> pallet_authorship::EventHandler<T::AccountId, BlockNumberFor<T>> for Pallet<T> {
		fn note_author(author: T::AccountId) {
			let reward = CollatorReward::<T>::get();

			if reward > Zero::zero() {
				let treasury_account = T::TreasuryAccount::get().into_account_truncating();

				let result = T::NativeCurrency::transfer(
					&treasury_account,
					&author,
					reward,
					frame_support::traits::ExistenceRequirement::KeepAlive,
				);

				if result.is_ok() {
					Self::deposit_event(Event::CollatorRewarded {
						collator: author,
						amount: reward,
					});
				}
			}
		}
	}
```

**File:** modules/pallets/collator-manager/src/lib.rs (L511-542)
```rust
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

**File:** modules/pallets/collator-manager/src/lib.rs (L548-567)
```rust
			for account_id in &new_set {
				let balance = T::ReputationAsset::balance(account_id);
				if balance.is_zero() {
					continue;
				}
				let result = T::ReputationAsset::burn_from(
					account_id,
					balance,
					Preservation::Expendable,
					Precision::Exact,
					Fortitude::Polite,
				);

				if result.is_ok() {
					Self::deposit_event(Event::ReputationReset {
						who: account_id.clone(),
						amount: balance,
					});
				}
			}
```
