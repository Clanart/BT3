This confirms `Pallet::<T>::withdraw` at `modules/pallets/relayer/src/withdrawal.rs:161-177` dispatches the relayer's own fee-withdrawal `PostRequest` to the destination chain with `FeeMetadata { payer: [0u8; 32].into(), fee: Default::default() }` — zero fee, zero payer — and, critically, **zeroes the `Fees` balance immediately** (`Fees::<T>::insert(..., U256::zero())`, line 177) *before* the message has any economic backing to be delivered. The withdrawal request is then dependent entirely on relayer altruism to actually reach the destination and disburse the funds.## Analysis

Reducing the C4 report to its core invariant: **a governance-controlled numeric parameter that legitimately defaults to (or can be set to) zero silently converts a "value" into a "kill switch" with no explicit lock semantics or escape hatch**, disproportionately harming users who have already earned/accrued a claim (collateral, in the original report) with no way to recover it except waiting out a long, uncontrollable delay.

I searched Hyperbridge for the equivalent pattern across withdrawal limits, bandwidth tiers, relayer rewards, and challenge periods. Most zero-value guards in this codebase (`challengePeriod == 0` skipping the delay check, `MintPerByte == 0` disabling minting, `TierConfig` `None` revoking a tier) are explicit, well-documented "off switches" that don't strand already-accrued value. The one place where a zero default *does* strand already-accrued, rightfully-owned funds with no explicit lock semantic and no escape hatch is the relayer fee-withdrawal path.

### Title
Relayer fee balance is zeroed and rendered irrecoverable once dispatched with zero fee, with delivery left entirely to altruism - (File: `modules/pallets/relayer/src/withdrawal.rs`)

### Summary
`Pallet::withdraw` (`modules/pallets/relayer/src/withdrawal.rs:81-187`) zeroes a relayer's accrued `Fees` balance and dispatches a cross-chain `PostRequest` instructing the destination chain to pay out that balance — but the dispatch uses `FeeMetadata { payer: [0u8; 32].into(), fee: Default::default() }` [1](#0-0) , i.e. zero relayer fee for delivering this specific message. Once the `Fees` entry is zeroed at line 177, the relayer's on-chain accounting shows the balance as spent, and the *only* path to actually receiving the funds is if some third-party relayer voluntarily carries this zero-fee request to the destination chain. This is documented in-repo as a known gap: "these all dispatch with `FeeMetadata { payer: [0u8; 32].into(), fee: Default::default() }`... Zero fee, zero payer. So relayers have no economic reason to pick them up, and the only thing that keeps them flowing today is altruism." [2](#0-1) 

### Finding Description
The withdrawal flow is:
1. Relayer signs `(nonce, dest_chain, beneficiary?)`.
2. `withdraw()` verifies the signature, checks `available_amount >= min_withdrawal_amount`, increments the nonce, and builds a `DispatchPost` targeting the destination's `HostManager` (EVM) or `HYPERBRIDGE_MODULE_ID` (substrate) with a `WithdrawalRequest`/`WithdrawalParams` payload for `available_amount` [3](#0-2) .
3. It dispatches with **zero fee** and **zero payer** — `FeeMetadata { payer: [0u8; 32].into(), fee: Default::default() }` — via `IsmpDispatcher` [4](#0-3) .
4. Immediately after, `Fees::<T>::insert(withdrawal_data.dest_chain, address.clone(), U256::zero())` zeroes the relayer's balance [5](#0-4) .

Unlike the incoming ISMP path, the outgoing `PostRequest` created here carries `timeout: 0`, meaning it never times out and cannot trigger a refund/retry mechanism even in principle [6](#0-5) . Because `fee = Default::default()` (zero), no relayer earns anything by delivering it, so there is no `RequestPayments` entry and no `accumulate_fees` incentive pipeline for this specific message (that pipeline assumes a real user fee-payer at origin) [7](#0-6) . The pallet's own withdrawal path is explicitly listed among the zero-fee system dispatches that this exact gap affects [8](#0-7) .

This mirrors the C4 finding precisely: a governance/design default (`collateralFactorBps = 0` there, `fee: Default::default()` here) causes an implicit, non-explicit lock on funds that a user is *already entitled to withdraw* — the balance has been marked spent (`Fees = 0`) on the accounting ledger before the actual payout is guaranteed to ever land, and there is no escape hatch, no retry-with-fee mechanism, and no timeout to fall back on.

### Impact Explanation
A relayer's already-accrued fee balance is debited from the `Fees` ledger at the moment of `withdraw()`, but the actual token transfer only happens if some external actor altruistically delivers the zero-fee ISMP request to the destination chain. If no relayer picks it up (which the project's own documentation confirms is a live, acknowledged risk — "the only thing that keeps them flowing today is altruism"), the funds are **effectively locked/lost from the relayer's perspective**: their `Fees` storage entry reads zero, so they cannot re-issue the withdrawal, and the dispatched request (with `timeout: 0`) never times out to allow any refund logic to trigger. This is a direct "loss of funds" / "logic attack on relayer rewards" per the impact gate — relayer rewards must move exactly once and only to the rightful beneficiary; here they can fail to move at all after being marked as moved.

### Likelihood Explanation
This does not require a malicious relayer, prover, or governance actor — it is a structural gap in the default fee metadata used by this specific dispatch path, independent of any adversary. Any relayer calling `withdraw_fees` today is exposed to this: the moment their transaction lands, `Fees` is zeroed and the payout is dependent on unpaid, voluntary delivery. The project's own `docs/outbound-request-incentivization.md` confirms this is a known, currently-unmitigated condition affecting this exact code path, not a hypothetical.

### Recommendation
- Do not zero `Fees` until delivery/execution on the destination is confirmed (e.g., zero only after a receipt-based confirmation, or keep a "pending withdrawal" bucket separate from `Fees` that can be reclaimed back into `Fees` if undelivered after a bounded window).
- Attach a real fee (drawn from a treasury/`PalletId` account, mirroring the `OutboundRequestDeliveryReward` design already used elsewhere in `pallet-relayer`) to relayer-initiated withdrawal dispatches so that the accumulate/reward pipeline naturally incentivizes delivery.
- Set a non-zero `timeout` on the withdrawal `DispatchPost` so a stuck request can time out and trigger the standard ISMP refund-to-sponsor path.

### Proof of Concept
1. Relayer accrues fees via `accumulate_fees` on `dest_chain` X, so `Fees::<T>::get(X, relayer_addr) = N` (N ≥ `MinimumWithdrawalAmount`).
2. Relayer calls `withdraw_fees` with a valid signature for `dest_chain = X`.
3. `Pallet::withdraw` executes: checks pass, `Nonce` increments, `dispatch_request` is called with `FeeMetadata { payer: 0x0…0, fee: 0 }` and `timeout: 0` [4](#0-3) , then `Fees::<T>::insert(X, relayer_addr, U256::zero())` [5](#0-4) .
4. No relayer has economic incentive to deliver this zero-fee `PostRequest` to chain X (confirmed in-repo: "relayers have no economic reason to pick them up") [8](#0-7) .
5. `relayer_addr`'s `Fees[X]` now reads `0`; calling `withdraw_fees` again fails the `available_amount < min_withdrawal_amount` check [9](#0-8) . Because `timeout: 0` means the dispatched request never times out, there is no automatic refund path back into `Fees`. The `N` tokens are stranded — accounted as spent on Hyperbridge, never received on the destination.

### Citations

**File:** modules/pallets/relayer/src/withdrawal.rs (L116-159)
```rust
		let available_amount = Fees::<T>::get(withdrawal_data.dest_chain, address.clone());

		if available_amount <
			Self::min_withdrawal_amount(withdrawal_data.dest_chain)
				.unwrap_or(MinWithdrawal::get())
		{
			Err(Error::<T>::NotEnoughBalance)?
		}

		let dispatcher = <T as Config>::IsmpHost::default();

		Nonce::<T>::try_mutate(address.clone(), withdrawal_data.dest_chain, |value| {
			*value += 1;
			Ok::<(), ()>(())
		})
		.map_err(|_| Error::<T>::ErrorCompletingCall)?;

		let beneficiary_address = withdrawal_data.beneficiary.clone().unwrap_or(address.clone());
		let (to, body) = match withdrawal_data.dest_chain {
			s if s.is_substrate() => (
				HYPERBRIDGE_MODULE_ID.to_vec(),
				Message::WithdrawRelayerFees(WithdrawalRequest {
					amount: available_amount.low_u128(),
					account: AccountId32::try_from(&beneficiary_address[..])
						.map_err(|_| Error::<T>::InvalidPublicKey)?,
				})
				.encode(),
			),
			_ => {
				let HostParam::EvmHostParam(params) =
					HostParams::<T>::get(withdrawal_data.dest_chain)
						.ok_or_else(|| Error::<T>::MissingMangerAddress)?;

				let body = WithdrawalParams {
					beneficiary_address: beneficiary_address.clone(),
					amount: available_amount.into(),
					token: params.fee_token,
				}
				.abi_encode()
				.map_err(|_| Error::<T>::InvalidPublicKey)?;

				(params.host_manager.0.to_vec(), body)
			},
		};
```

**File:** modules/pallets/relayer/src/withdrawal.rs (L161-167)
```rust
		let post = DispatchPost {
			dest: withdrawal_data.dest_chain,
			from: MODULE_ID.to_vec(),
			to,
			body,
			timeout: 0,
		};
```

**File:** modules/pallets/relayer/src/withdrawal.rs (L169-177)
```rust
		// Account is not useful in this case
		dispatcher
			.dispatch_request(
				DispatchRequest::Post(post),
				FeeMetadata { payer: [0u8; 32].into(), fee: Default::default() },
			)
			.map_err(|_| Error::<T>::DispatchFailed)?;

		Fees::<T>::insert(withdrawal_data.dest_chain, address.clone(), U256::zero());
```

**File:** docs/outbound-request-incentivization.md (L9-11)
```markdown
A regular cross-chain message that flows *through* hyperbridge has a fee attached at origin (the source chain transfers `fee.payer → RELAYER_FEE_ACCOUNT` and records `RequestPayments[commitment]` in pallet-hyperbridge's child trie). When a relayer delivers and the destination receipt lands back on hyperbridge, the existing `accumulate_fees` flow credits that fee to the relayer. That whole pipeline assumes a *user* paid at origin.

But hyperbridge itself originates requests too: host parameter propagation, host-executive updates, intents-coprocessor responses, token-governor messages, the relayer pallet's withdrawal request. Today these all dispatch with `FeeMetadata { payer: [0u8; 32].into(), fee: Default::default() }` (see `modules/pallets/host-executive/src/lib.rs:228`, `modules/pallets/intents-coprocessor/src/lib.rs:486`, `modules/pallets/relayer/src/lib.rs:638`, and `modules/pallets/token-governor/src/impls.rs`). Zero fee, zero payer. So relayers have no economic reason to pick them up, and the only thing that keeps them flowing today is altruism.
```
