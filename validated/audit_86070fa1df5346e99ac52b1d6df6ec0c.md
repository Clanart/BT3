## Analysis

The H-01 report's core broken invariant is: **a public, unprivileged, no-minimum-value entrypoint lets an attacker mutate another party's slot-limited state (`unlockTime`) purely by targeting them as the beneficiary, silently displacing the value the victim had already legitimately earned.**

The closest verifiable analog in this codebase is Hyperbridge's **bandwidth subscription system**, specifically the FIFO eviction logic in `push_subscription`.

### Title
Unprivileged attacker can grief an app's paid bandwidth allowance by spamming cheap `purchase()` calls to evict its legitimate subscriptions - (File: `modules/pallets/bandwidth/src/lib.rs`)

### Summary
`BandwidthPallet`'s `Allowance` storage keeps a capped FIFO list (max 1024 entries) of `Subscription` rows per `(app_chain, app)`. Any external party can dispatch a `purchase()` from `BandwidthManager.sol` naming an **arbitrary target `app`** (a beneficiary that is not the caller), exactly like `lockOnBehalf`'s unrestricted `receiver` parameter. Since the target app is caller-specified and any registered, non-zero-priced tier qualifies (cheapest tier, single month), an attacker can push 1024 minimal subscriptions into a victim app's list, evicting the victim's legitimately purchased, high-value subscriptions from the head of the queue — permanently destroying paid-for bandwidth the victim never consumed.

### Finding Description
`push_subscription` unconditionally appends a new subscription to `Allowance::<T>` for the `(app_chain, app)` key, and evicts the oldest entry once the 1024-entry cap is reached: [1](#0-0) 

Credit is granted via `on_accept` whenever a `BandwidthPurchaseMsg` arrives from a *registered* `BandwidthManager` — the check only validates `request.from == manager address`, not who paid or who benefits: [2](#0-1) 

Per the documented lifecycle, the sponsorship model explicitly allows the payer and the credited app to be different, and the message carries its own target `chain`/`app` independent of the caller: [3](#0-2) 

Crucially, a subscription's terms are **immutable and never merge or renew** — every purchase, however small, becomes its own new FIFO row: [4](#0-3) 

And eviction is strictly FIFO by insertion order, not by value or tier size: [5](#0-4) 

This mirrors the `lockOnBehalf` bug exactly: a caller-specified beneficiary, an attacker-controlled, no-minimum-value repeated call, and a state structure (FIFO queue / `unlockTime`) that gets pushed/evicted purely by call volume rather than by any relationship or consent from the victim.

### Impact Explanation
An attacker who wants to sabotage a specific dApp's cross-chain bandwidth (e.g. to censor/DoS its ISMP messages or force it to re-purchase) can:
1. Determine the victim's `(app_chain, app)` key (both are public — needed for anyone to inspect allowances).
2. Repeatedly call `BandwidthManager.purchase()` on the victim's behalf using the cheapest configured tier and `months = 1`.
3. Once 1024 cheap subscriptions have been pushed, every subsequent purchase evicts the **oldest** entry — which, if the victim purchased a large/long-duration tier earlier, is exactly the entry with the most economic value.

This destroys bandwidth the victim already paid for (a real, on-chain, prepaid asset), with `SubscriptionEvicted` merely logging the loss after the fact rather than preventing it. This is a direct "loss of funds"-class impact: prepaid protocol credit is unrecoverably burned by an unrelated, unprivileged third party.

### Likelihood Explanation
- No privileged role, relayer, prover, or governance actor is required — `purchase()` is a normal permissionless EVM call.
- The attack cost scales with the cheapest configured tier price × up to 1024 purchases; if that total is less than the value of the victim's large legitimate subscription(s), the attack is profitable purely as griefing (denial of service against a competitor dApp), and even if not "profitable," it is cheap and repeatable, unlike a one-off gas-DoS.
- No race condition, front-running, or mempool timing is needed — the attacker can perform the whole sequence at leisure since eviction only depends on cumulative call count, not on timing relative to the victim's actions.

### Recommendation
- Restrict `push_subscription`/`purchase()` credit target: either require the payer to be the `app` itself (removing third-party sponsorship) or gate sponsorship behind an explicit opt-in/allowlist from the target app (mirroring the report's suggested "two-step accept" mitigation).
- Alternatively, change eviction policy to evict the **lowest remaining-value** entry (smallest `remaining_bytes × time-left`) rather than strictly FIFO-oldest, so an attacker cannot cheaply displace a victim's high-value entry with many low-value ones.
- Enforce a minimum purchase size (in bytes or fee-token value) relative to existing entries, analogous to disallowing zero/near-zero "donations" in the original report.

### Proof of Concept
1. Victim app buys `TierFour` for 12 months on `EVM-8453`, landing subscription #1 in its FIFO list (large `remaining_bytes`, long `expires_at`).
2. Attacker calls `BandwidthManager.purchase(TierOne, months=1, app=victimApp, chain=EVM-8453)` 1023 times, each dispatching a `BandwidthPurchaseMsg` that `on_accept` credits via `push_subscription` to the *same* `(app_chain, app)` key as the victim's.
3. On the attacker's 1024th call, `push_subscription` hits `MAX_SUBSCRIPTIONS` and evicts index 0 — the victim's original `TierFour`/12-month subscription — emitting `SubscriptionEvicted { lost_bytes: victim's full allowance }`.
4. The victim's dApp is left with only cheap, short-lived `TierOne` subscriptions it never purchased and did not want, having lost the bandwidth it paid for, entirely at the discretion of an unrelated actor.

**Uncertainty note:** I was unable to directly inspect `BandwidthManager.sol`'s Solidity `purchase()` signature (only referenced via docs and the SDK keeper service) to confirm there is no additional access-control check on the `app` parameter beyond what the docs describe. If the wiki/documentation is accurate about the sponsorship model, the pallet-side logic shown above confirms the eviction primitive is real and unguarded. A Devin session with full repo access should verify `evm/src/apps/BandwidthManager.sol`'s `purchase()` function directly before treating this as fully confirmed.

### Citations

**File:** modules/pallets/bandwidth/src/lib.rs (L400-437)
```rust
		/// Append a fresh subscription with a fixed expiry. If the list
		/// is already at `MaxSubscriptions`, evict the oldest entry and
		/// emit [`Event::SubscriptionEvicted`] so the lost bytes are
		/// auditable. Returns the new subscription's `expires_at`.
		fn push_subscription(
			app_chain: &StateMachine,
			app: &AppKey,
			tier: TierIndex,
			bytes: BandwidthBytes,
			duration_secs: u64,
		) -> u64 {
			let now = <T as pallet_ismp::Config>::TimestampProvider::now().as_secs();
			let expires_at = now.saturating_add(duration_secs);
			let new_sub =
				Subscription { tier, remaining_bytes: bytes, expires_at, purchased_at: now };

			let evicted = Allowance::<T>::mutate(app_chain, app, |list| {
				let evicted = if list.len() == MAX_SUBSCRIPTIONS as usize {
					Some(list.remove(0))
				} else {
					None
				};
				// Capacity is now guaranteed; try_push can't fail.
				let _ = list.try_push(new_sub);
				evicted
			});

			if let Some(old) = evicted {
				Self::deposit_event(Event::SubscriptionEvicted {
					app_chain: *app_chain,
					app: app.clone(),
					tier: old.tier,
					lost_bytes: old.remaining_bytes,
				});
			}

			expires_at
		}
```

**File:** modules/pallets/bandwidth/src/lib.rs (L439-445)
```rust
		/// The router uses this to skip the gate on purchases —
		/// otherwise a depleted app couldn't recharge.
		pub fn is_purchase_message(request: &PostRequest) -> bool {
			BandwidthManager::<T>::get(&request.source)
				.map(|m| request.from == m.0.to_vec())
				.unwrap_or(false)
		}
```

**File:** docs/content/developers/evm/bandwidth/overview.mdx (L56-68)
```text
## Subscription Lifecycle

Each `(chain, app)` row holds a FIFO list of subscriptions, capped at **1024** entries. Every purchase appends a new subscription — same-tier repurchases don't stack, they queue.

A subscription is immutable across its lifetime:

| Field             | Behavior                                                                       |
| ----------------- | ------------------------------------------------------------------------------ |
| `tier`            | Recorded at purchase time. Used for events and analytics, not for gating.      |
| `remaining_bytes` | Drains as the gate consumes messages. Pops once it hits zero.                  |
| `expires_at`      | Fixed at purchase. Never extends — a repurchase is a _new_ row, not a renewal. |
| `purchased_at`    | Insertion timestamp. Fixes FIFO order under same-block buys.                   |

```

**File:** docs/content/developers/evm/bandwidth/overview.mdx (L75-77)
```text
### Eviction

Pushing onto a full list (1024 entries) evicts the **oldest** entry and emits `SubscriptionEvicted` with the lost bytes so the loss is auditable on-chain. In practice this only happens under pathological repeat-buy behavior — at the default of one purchase per cycle, 1024 buys is years of headroom.
```

**File:** docs/content/developers/evm/bandwidth/overview.mdx (L108-114)
```text
## Sponsorship

The purchase message carries its own `chain` (the _credit chain_) which is **independent of the source chain** that sent the message. This means a buyer on Ethereum can credit an app on Base by dispatching a purchase whose payload sets `chain = "EVM-8453"`.

The pallet keys allowance storage by `(app_chain, app)` taken from the message body, not by `request.source`. The event `BandwidthCredited` carries both — `app_chain` (where the credit lands) and `paid_from` (where the payment came from) — so the cross-chain payer is auditable.

This is what makes the system multi-tenant friendly: a treasury on a single chain can sponsor bandwidth for an app deployed across many chains, without having to deploy `BandwidthManager` on each chain the app lives on.
```
