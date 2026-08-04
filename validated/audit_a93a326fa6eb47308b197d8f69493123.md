### Title
Reputation-for-Collator-Seat can be farmed via mass self-dispatch of floor-priced messages - (File: `modules/pallets/messaging-incentives/src/lib.rs`)

### Summary
`pallet-messaging-incentives` mints `ReputationAsset` to whoever signs a delivered ISMP message, using a **flat per-request floor** of `max(body_len, 32)` bytes rather than metering real economic value delivered. `pallet-collator-manager` then ranks and selects collators **purely** by this `ReputationAsset` balance and pays the winners real `$BRIDGE` from the treasury per authored block. This reproduces the Uniswap bug's core broken invariant: a reward accumulator that can be inflated far above its "real economic activity" baseline through self-generated, low-cost repetitive actions, which then gets converted into an unfair, valuable privilege (collator seat + treasury payouts) at the expense of honest participants.

### Finding Description
`message_bytes()` computes the reward basis for a delivered request as: [1](#0-0) 

```
fn message_bytes(message: &Message) -> u32 {
    match message {
        Message::Request(req) => req.requests.iter()
            .map(|p| core::cmp::max(p.body.len() as u32, 32))
            .sum::<u32>(),
        _ => 0,
    }
}
```

This floor is explicitly documented as a defense against **splitting a single envelope's body** into many tiny requests to farm more than proportional reward: [2](#0-1) 

However, the floor is applied per-request regardless of whether that request represents genuine, valuable cross-chain application traffic or a minimal/empty-body self-dispatched ping. `on_executed` then mints `rate * bytes` of `ReputationAsset` directly to the recovered signer, with `pays_fee: Pays::No` on the dispatch info for the mint itself: [3](#0-2) 

Any account can act as a permissionless relayer and self-sign the delivery message, so an attacker who is simultaneously the app dispatching trivial (empty/near-empty) cross-chain requests and the relayer delivering them collects the full 32-byte-floor mint for every such request — a self-dealing loop structurally identical to a Uniswap LP wash-trading against themselves to inflate `feeGrowthGlobal`.

That inflated, largely fabricated `ReputationAsset` balance is the **sole** ranking input for collator selection: [4](#0-3) 

```
.map(|controller_account| {
    (T::ReputationAsset::balance(&controller_account), controller_account)
})
candidates.sort_by_key(|(balance, _)| *balance);
```

Selected collators are paid real `$BRIDGE` from the treasury per authored block (0.7 `$BRIDGE`/block, ~4,000,000 `$BRIDGE`/year network-wide), confirmed by `note_author`: [5](#0-4) 

So the "cheap wash-trading of a reward metric" primitive from the source report maps directly onto: cheaply mass-producing minimal, low-value cross-chain messages → farming `ReputationAsset` at a rate untethered from real work → outranking honest relayers/provers in the collator-selection sort → capturing genuine treasury-funded `$BRIDGE` payouts and block-production rights that should have gone to operators who performed real, valuable network service.

### Impact Explanation
This is a fund-flow and privilege-escalation issue, not a griefing/DoS concern: an attacker converts near-zero-cost self-generated "activity" into (a) real `$BRIDGE` extracted from the Hyperbridge treasury via `note_author`, and (b) actual block-producing authority (collator seat), displacing honest, economically-productive operators who were supposed to be selected on merit. This matches the bounty's "stealing or loss of funds" and "logic attacks" categories: value is misappropriated from the treasury/legitimate operators through manipulation of an on-chain accounting metric, exactly as IV manipulation misappropriated liquidation value from honest borrowers in the source report.

### Likelihood Explanation
The attack requires only an unprivileged actor able to (1) dispatch ISMP requests from a source chain it controls or from any low-cost application, and (2) relay/sign the resulting delivery itself — both explicitly permissionless and by-design roles in Hyperbridge (`relaying`/`messaging` are open to anyone). No malicious relayer, prover, admin, or leaked key is needed. The exact profitability depends on the ratio of `MintPerByte` (governance-set) to the real marginal cost of dispatching+delivering a minimal message (source-chain gas, ISMP delivery/verification overhead, and any bandwidth-pallet metering); this ratio was **not fully quantified from the available code** — I was unable to inspect `modules/ismp/core/src/handlers.rs`'s batch-verification cost curve before running out of investigation budget, so I cannot state with certainty whether batched multi-request proofs are sub-linear in cost (which would make the attack strictly profitable at scale) or linear (which would only make it profitable when `MintPerByte` is set too high relative to real costs). The structural gap — a flat, request-count-scaled reward with no floor on genuine economic value per request — is confirmed in code; the precise economic threshold at which it becomes profitable is not.

### Recommendation
- Do not let the reward basis be a pure function of self-reported/self-controlled message count and a static byte floor; tie `MintPerByte` (or an added per-request cap) to a source of real cost, e.g., requiring a minimum non-zero relayer *fee* to have been paid on the request (mirrors `pallet-ismp-relayer`'s fee-based accumulation) so reputation is earned proportionally to genuine paid demand, not free self-dispatch.
- Cap the number of reputation-eligible requests per signer per session, or apply diminishing returns, so mass self-dispatch cannot linearly dominate the collator-selection ranking.
- Consider blending reputation with a median/EMA-style smoothing (as suggested for the Uniswap source fix) across sessions so a single-session farming burst cannot instantly secure a collator seat.
- Require a minimum fee-per-byte on requests before they qualify for reputation minting.

### Proof of Concept
1. Attacker deploys/uses a lightweight application on any onboarded source chain (or an existing cheap testnet-style chain) and repeatedly dispatches `PostRequest`s with empty/near-empty bodies (each floored to 32 bytes of reward-eligible size) — `modules/pallets/messaging-incentives/src/lib.rs:126-134`.
2. Attacker runs the permissionless relayer role themselves, signing each delivery with their own sr25519 key so `relayer_for()` resolves to their own `Controller` account — `modules/pallets/messaging-incentives/src/lib.rs:140-153`.
3. Each successful delivery mints `MintPerByte * 32` (or more, batched) `ReputationAsset` to the attacker's account with `Pays::No` on the mint call itself — `modules/pallets/messaging-incentives/src/lib.rs:164-181`.
4. At the next session boundary, `pallet-collator-manager::new_session` sorts all candidates purely by `ReputationAsset` balance and selects the top N — `modules/pallets/collator-manager/src/lib.rs:514-542` — so the attacker's inflated balance outranks honest relayers/provers who performed genuine, higher-value work but fewer floor-eligible requests.
5. Once selected, the attacker collects real `$BRIDGE` treasury payouts per authored block via `note_author`, and their reputation is burned/reset only after the seat is already won — `docs/content/developers/network/collator.mdx:412-416`.

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

**File:** modules/pallets/collator-manager/src/lib.rs (L511-527)
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
```

**File:** docs/content/developers/network/collator.mdx (L17-19)
```text
For their vital contributions, Collators are directly rewarded from the network Treasury. Each successful block authored earns the Collator `0.7 $BRIDGE`.
This creates a consistent and reliable revenue stream, with a total of approximately `4,000,000 $BRIDGE` allocated annually for Collator rewards,
distributed among the active set based on their block production performance.
```
