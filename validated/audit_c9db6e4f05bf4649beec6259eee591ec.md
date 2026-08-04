## Title
Unbounded reputation minting in `pallet-messaging-incentives` lets a self-relaying attacker mint arbitrary reputation and capture collator seats/treasury rewards - (File: `modules/pallets/messaging-incentives/src/lib.rs`)

### Summary
`pallet-messaging-incentives` mints a value-bearing `ReputationAsset` to whichever account signs a delivered ISMP message, scaled linearly by the byte-size of the message body, with **no supply cap, no per-relayer cap, and no correlation to real economic value delivered**. This is the same missing-guardrail class as the external report: a per-unit rate exists (`MintPerByte`, analogous to a price curve) but nothing bounds the total exposure/supply, so an attacker can drive the mint arbitrarily by controlling the one free variable — message size — exactly as unconstrained liquidation volume drove bad debt in the original report.

### Finding Description
`Pallet::on_executed` computes `bytes = max(body.len(), 32)` per request in a delivered message and mints `rate * bytes` of `ReputationAsset` to the account recovered from the message's `signer` field: [1](#0-0) 

The relayer identity is derived purely from a signature check with no registry/allowlist requirement: [2](#0-1) 

`ReputationAsset` is not cosmetic — it is soulbound but directly determines `pallet-collator-manager`'s selection of block-producing collators, which in turn earn treasury-funded `$BRIDGE` (0.7 BRIDGE/block, ~4,000,000 BRIDGE/year), as documented: [3](#0-2) [4](#0-3) 

Relaying is explicitly permissionless — "No staking or approval is required" — so any account can be the signer that gets credited: [5](#0-4) 

There is no ceiling anywhere in the pallet on: total minted supply, mint per account, mint per time window, or mint relative to the real value of the work relayed (unlike `pallet-bandwidth`, which bounds exposure with a 1024-entry FIFO cap and per-tier byte budgets tied to a paid purchase). The only knob is the flat per-byte `rate` set by governance: [6](#0-5) 

Because `bytes` is derived from attacker-controlled request-body size and the reward is strictly linear in that size with no cap, an unprivileged actor who dispatches (or has dispatched to them) messages carrying large but otherwise low-value bodies from any connected chain, then delivers/signs the resulting Hyperbridge-side message themselves, mints reputation disproportionate to any real "market liquidity" of relaying work — mirroring the report's core defect: a rate/price exists but nothing bounds the volume that rate is applied to.

### Impact Explanation
Reputation minted this way feeds directly into collator selection, so accumulating outsized reputation lets an attacker capture collator slots ahead of genuine operators and redirect ongoing treasury-funded block rewards to themselves — a real, measurable transfer of `$BRIDGE` value obtained through a gamed, uncapped internal accounting mechanism rather than legitimate relaying/consensus work. This is a logic attack on a reward/settlement surface (relayer rewards) with no bound matching real economic backing, the same "unlimited exposure with no supply limit" defect flagged in the source report, just relocated to Hyperbridge's own incentive pallet instead of a lending market.

### Likelihood Explanation
Any account can act as a relayer and sign its own delivered messages (no allowlist/registry gate in `relayer_for`), and message body size is attacker-chosen. The only friction is the real cost of getting a message proven and delivered through the normal ISMP pipeline — but nothing in the pallet prevents scaling this horizontally (many messages, or a few with maximal bodies) once the `MintPerByte` rate is non-zero, since there is no per-account/global cap to stop it.

### Recommendation
Introduce a supply/exposure ceiling analogous to what `pallet-bandwidth` already does for byte allowances: a global or per-relayer cap on minted `ReputationAsset` per epoch/session, and/or decouple the mint rate from raw attacker-controlled body size (e.g., cap the `bytes` counted per message/session, or tie the reward to independently-verified real relay cost rather than self-reported payload size). Track and enforce this cap the same way governance is expected to track supply limits per the original report's recommendation.

### Proof of Concept
1. Governance sets `MintPerByte::set_mint_per_byte(rate)` to any non-zero value (a normal, expected configuration state, see `modules/pallets/testsuite/src/tests/pallet_messaging_incentives.rs:87-97`).
2. An attacker-controlled account dispatches an ISMP request with a maximally large `body` from a connected source chain (no bandwidth/relayer registry constraint requires this to be "small" or "cheap" relative to the mint rate).
3. The same or a colluding attacker account signs and submits the delivering message to Hyperbridge; `relayer_for` recovers the attacker's account with no further authorization check.
4. `on_executed` mints `rate * max(body.len(), 32)` `ReputationAsset` to the attacker with no per-account or global cap, as demonstrated by the linear scaling in `on_executed_mints_reputation_proportional_to_bytes` (`modules/pallets/testsuite/src/tests/pallet_messaging_incentives.rs:99-115`), which has no upper bound assertion.
5. Repeating this drives the attacker's `ReputationAsset` balance past genuine operators', letting them win `pallet-collator-manager` selection and redirect treasury-funded block rewards to themselves.

### Citations

**File:** modules/pallets/messaging-incentives/src/lib.rs (L85-89)
```rust
	/// Reputation tokens minted per byte of delivered payload. Zero
	/// disables minting; non-zero applies to every message executed
	/// after it is set.
	#[pallet::storage]
	pub type MintPerByte<T: Config> = StorageValue<_, BalanceOf<T>, ValueQuery>;
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

**File:** docs/content/developers/network/collator.mdx (L17-19)
```text
For their vital contributions, Collators are directly rewarded from the network Treasury. Each successful block authored earns the Collator `0.7 $BRIDGE`.
This creates a consistent and reliable revenue stream, with a total of approximately `4,000,000 $BRIDGE` allocated annually for Collator rewards,
distributed among the active set based on their block production performance.
```

**File:** docs/content/developers/network/collator.mdx (L404-416)
```text

- It fetches the list of all bonded candidates (Stash accounts) from `pallet-collator-selection`.
- For each candidate, it uses the `Controller` storage map to find the linked Controller account.
- It validates that the Controller has registered valid session keys via `pallet-session`.
- It ranks the remaining candidates from highest to lowest based on their Controller's Reputation Asset balance.
- It selects the top candidates to fill the available collator slots for the new session.


If you are selected:

- Your Controller's Reputation Asset balance will be burned. This is somewhat like your stake for the session.
- Your node (run by your Controller) will begin authoring and producing blocks after 2 rounds (sessions). To do this, you must ensure that your node is running (or has been restarted) with the `--collator` flag.
- As you continue operating your relayer or prover, your Controller will keep earning `$BRIDGE` rewards and your Reputation Asset will start increasing again, allowing you to compete for a spot in future sessions.
```

**File:** docs/content/developers/explore/relayers.mdx (L38-45)
```text
## Shared design principles

- **Permissionless.** No staking or approval is required.
- **Economically incentivized.** Operators are compensated for every
  unit of useful work, not for running a node.
- **Competitive.** First-to-submit wins. Latency and infrastructure
  quality decide who earns each reward.

```
