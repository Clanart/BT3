### Title
Relayer fee withdrawal permanently locked by a decimals-blind, hardcoded `$10` minimum-withdrawal check - (File: `modules/pallets/relayer/src/withdrawal.rs`)

### Summary
`pallet-relayer`'s `withdraw()` gates every relayer's fee withdrawal on a `MinimumWithdrawalAmount` check that is either an admin-configured raw `u128` per `StateMachine`, or, if unset, a hardcoded default (`MinWithdrawal::get()` = `10 * 10^18`) that silently assumes the destination's fee token has 18 decimals. `Fees<T>` accumulates real fee-token units credited from proofs of on-chain `RequestMetadata.fee`/`FeeMetadata.fee` (raw token amount at that host's native decimals), not a normalized USD amount. This is the same broken invariant as the reported LP bug: a dollar-denominated minimum is compared directly against a raw, decimals-dependent token balance.

### Finding Description
`Fees::<T>::get(dest_chain, address)` stores the relayer's accrued balance in the destination chain's fee-token base units, as decoded straight from the proven `fee` field on source/destination chains in `accumulate.rs` (`validate_results`, lines 260–286) — no decimal normalization is performed there.

`withdraw()` in `modules/pallets/relayer/src/withdrawal.rs` (lines 116–123) then compares this raw balance directly against:

```rust
let available_amount = Fees::<T>::get(withdrawal_data.dest_chain, address.clone());
if available_amount <
    Self::min_withdrawal_amount(withdrawal_data.dest_chain)
        .unwrap_or(MinWithdrawal::get())
{
    Err(Error::<T>::NotEnoughBalance)?
}
``` [1](#0-0) 

`MinWithdrawal` is a hardcoded constant documented as "$10", computed as `10 * 1_000_000_000_000_000_000` (10 × 10^18): [2](#0-1) 

This constant only equals "$10" if the destination chain's configured `feeToken` happens to have 18 decimals (e.g. DAI). But the docs explicitly state the fee token is configurable per host and can be any ERC-20:
> "the host stores a configurable `feeToken` address that governance can update without a redeploy" [3](#0-2) 

`set_minimum_withdrawal` lets governance override the threshold per `StateMachine`, but it is a manual, per-chain opt-in call — nothing enforces that it is set correctly (or set at all) whenever a new destination is onboarded with a fee token that isn't 18-decimal: [4](#0-3) 

If governance onboards (or forgets to configure) a destination whose `feeToken` has fewer decimals (e.g. a 6-decimal USDC-style token), the default `MinWithdrawal` of `10 × 10^18` raw units becomes an astronomically large USD-equivalent threshold (`$10,000,000,000,000`) relative to that token's actual unit scale. Relayers on that chain can never accumulate enough raw balance to clear the check, so `withdraw()` always reverts with `NotEnoughBalance` — their legitimately earned, correctly-accounted `Fees` entry is permanently unreachable. Conversely, for fee tokens with *more* than 18 decimals or extremely low unit price, the same hardcoded assumption produces the opposite failure mode (thresholds far too low, in effect no minimum at all, defeating the anti-spam purpose the team cited for the original bug's mitigation).

This exactly mirrors TRST-M-1's root cause — mixing a dollar-denominated minimum with a token-unit-denominated balance without any decimals/price normalization — but here the blast radius is systemic per-chain fund lockup rather than a single small LP's dust.

### Impact Explanation
This is a loss-of-funds bug for relayers: `Fees<T>` correctly and durably tracks funds owed to the relayer (verified via consensus/state proofs in `accumulate()`), but `withdraw()`'s decimals-blind gate can make that balance permanently inaccessible on any destination chain whose fee token isn't 18-decimal and whose minimum wasn't (or can't be practically) tuned to match. Because `withdraw_fees` is the *only* path that dispatches the payout message to the destination's host manager, an unreachable threshold means the accrued balance can never be withdrawn — a systemic, protocol-level fund lock rather than an edge-case dust issue, satisfying the "loss of funds" impact bar.

### Likelihood Explanation
Likelihood is high in any deployment with a non-18-decimal fee token: this requires no attacker action, malicious peer, or privileged misuse — it is triggered purely by normal relayer operation once a destination's fee token decimals diverge from 18 and governance has not (or cannot practically) set a correctly-scaled `set_minimum_withdrawal` override for every such chain. The bug is deterministic and reproducible from the code path alone; it does not depend on relayer/prover/admin compromise.

### Recommendation
Normalize `MinimumWithdrawalAmount` semantics against the fee token's actual decimals for the given `StateMachine` (or store per-chain minimums natively in raw token units validated against the registered `HostParams` fee-token decimals at configuration time), and remove/replace the hardcoded 18-decimal `MinWithdrawal` default with a chain-aware computation, mirroring the fix direction the team already applied conceptually for consumer-facing `tesseract` config (`fee_token_decimals()`-scaled `minimum_withdrawal_amount` in `tesseract/messaging/messaging/src/fees.rs`) but which is missing on the pallet's own enforcement path.

### Proof of Concept
1. Governance registers a new EVM destination via `pallet_ismp_host_executive::HostParams` with a `fee_token` that has 6 decimals (e.g. a USDC-style token), and does **not** call `set_minimum_withdrawal` for that `StateMachine`.
2. A relayer delivers messages and successfully calls `accumulate_fees`, which credits `Fees::<T>::get(dest_chain, relayer)` with the real (6-decimal) fee amounts proven from source/destination state, per `validate_results` in `accumulate.rs`.
3. The relayer accumulates, say, `50 * 10^6` (== $50 of the 6-decimal token) — a healthy balance by any real-world minimum.
4. The relayer calls `withdraw_fees`. `withdraw()` compares `50_000_000` against `Self::min_withdrawal_amount(dest_chain).unwrap_or(MinWithdrawal::get())` = `10_000_000_000_000_000_000`.
5. `50_000_000 < 10_000_000_000_000_000_000` is always true, so `Error::<T>::NotEnoughBalance` is returned every time, regardless of how much the relayer accumulates in practice (short of accumulating an economically impossible `10^13` "dollars" of the token) — the relayer's fees are permanently locked in `Fees<T>` on that chain.

### Citations

**File:** modules/pallets/relayer/src/withdrawal.rs (L116-123)
```rust
		let available_amount = Fees::<T>::get(withdrawal_data.dest_chain, address.clone());

		if available_amount <
			Self::min_withdrawal_amount(withdrawal_data.dest_chain)
				.unwrap_or(MinWithdrawal::get())
		{
			Err(Error::<T>::NotEnoughBalance)?
		}
```

**File:** modules/pallets/relayer/src/lib.rs (L137-144)
```rust
	/// Default minimum withdrawal is $10
	pub struct MinWithdrawal;

	impl Get<U256> for MinWithdrawal {
		fn get() -> U256 {
			U256::from(10u128 * 1_000_000_000_000_000_000)
		}
	}
```

**File:** modules/pallets/relayer/src/lib.rs (L370-381)
```rust
		/// Sets the minimum withdrawal amount using the correct decimals
		#[pallet::call_index(2)]
		#[pallet::weight(<T as frame_system::Config>::DbWeight::get().reads_writes(0, 1))]
		pub fn set_minimum_withdrawal(
			origin: OriginFor<T>,
			state_machine: StateMachine,
			amount: u128,
		) -> DispatchResult {
			T::RelayerOrigin::ensure_origin(origin)?;
			MinimumWithdrawalAmount::<T>::insert(state_machine, U256::from(amount));
			Ok(())
		}
```

**File:** docs/content/developers/polkadot/fees.mdx (L34-40)
```text
Relayers accumulate their earned fees on Hyperbridge. When a relayer initiates a withdrawal, Hyperbridge dispatches a POST request back to the source chain. `pallet-ismp`'s [`RefundingRouter`](https://github.com/polytope-labs/hyperbridge/blob/main/modules/pallets/ismp/src/dispatcher.rs) intercepts requests addressed to the built-in module id `b"HYPR-FEE"`, decodes a `WithdrawRelayerFees` payload, and transfers the owed balance from `RELAYER_FEE_ACCOUNT` to the relayer.

No runtime configuration is required to enable this — it ships with `pallet-ismp`.

## Pay in BRIDGE tokens

The simplest integration is to run your own [relayer](/developers/network/relayer) and pay Hyperbridge in its native token, BRIDGE. Payments happen entirely offchain at the point of relaying requests to Hyperbridge, so applications don't need to attach relayer fees on dispatch. The trade-off is that you take on the relayer-operations burden — Hyperbridge's permissionless relayer network will not deliver your messages.
```
