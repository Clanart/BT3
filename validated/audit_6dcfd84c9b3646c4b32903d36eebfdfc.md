## Analysis

The Astaria bug's core invariant is: **a value stored as a full-width type gets silently downcast to a narrower type at settlement time, with no validation that the cast is lossless, and the code proceeds as if the cast succeeded** — losing the excess and, in that case, reverting the whole path forever.

The closest local analog is in Hyperbridge's relayer fee withdrawal path.

### Title
Silent `U256 → u128` truncation in `pallet-relayer`'s substrate fee withdrawal zeroes the full `Fees` balance while paying out only the truncated low 128 bits - (File: `modules/pallets/relayer/src/withdrawal.rs`)

### Summary
`Pallet::withdraw` reads a relayer's accumulated fee balance as a `U256` from `Fees::<T>`, then for substrate destinations narrows it with `available_amount.low_u128()` when building the `WithdrawRelayerFees` payload, with no bounds check that `available_amount` actually fits in `u128`. Immediately after dispatching the request, the code unconditionally zeroes the entire `Fees` entry with `Fees::<T>::insert(..., U256::zero())` and emits the `Withdraw` event carrying the *original, untruncated* `available_amount`. If the true balance exceeds `u128::MAX`, the dispatched payout is silently smaller than what's owed, yet the full balance is wiped and the event misreports the amount actually sent.

### Finding Description
`Fees` is a `StorageDoubleMap<..., U256, ValueQuery>` accumulated without any cap across arbitrarily many `accumulate_fees` calls: [1](#0-0) , and each accepted proof adds a proof-derived `fee: U256` via `*inner += total_fee` / `*inner += fee` with plain unchecked `AddAssign` on `U256`: [2](#0-1) [3](#0-2) .

At withdrawal time, for substrate destinations the code does:
```
amount: available_amount.low_u128(),
``` [4](#0-3) 

`U256::low_u128()` is a non-panicking truncation — it silently discards the high 128 bits rather than failing like the Astaria `safeCastTo88` (which reverts). Right after dispatch, regardless of what was actually encoded in the outbound message, the pallet unconditionally zeroes the full recorded balance:
```
Fees::<T>::insert(withdrawal_data.dest_chain, address.clone(), U256::zero());
``` [5](#0-4) 

and emits `Event::Withdraw { ..., amount: available_amount }` — the untruncated value — even though only `available_amount.low_u128()` was actually dispatched for payout. The EVM destination path, by contrast, correctly carries the full `U256` through to ABI encoding without truncation: [6](#0-5) [7](#0-6) , confirming the substrate branch's narrowing is an inconsistency rather than an intentional design constraint of the wire format.

### Impact Explanation
This falls squarely under "bridged assets ... relayer rewards ... must move exactly once and only to the rightful beneficiary and amount." If a relayer's `Fees` balance for a given substrate destination ever exceeds `u128::MAX`, the withdrawal flow: (1) computes and dispatches a payout for only the low 128 bits of the true balance, (2) zeroes the *entire* tracked balance so it can never be withdrawn again (comment explicitly states this zeroing exists to prevent double-withdrawal: "The `Fees` entry is zeroed so the same balance cannot be withdrawn twice" [8](#0-7) ), and (3) emits a `Withdraw` event that misreports the amount that was actually dispatched. The excess (any full multiple of `2^128` in the true balance) is permanently and silently lost — never paid to the relayer, never recoverable, with no revert and no on-chain evidence of the shortfall since the event lies about the amount sent.

### Likelihood Explanation
Reaching a `Fees` balance above `u128::MAX` (≈3.4×10^38, e.g. ≈3.4×10^20 tokens at 18 decimals) through ordinary fee accumulation is not realistic under current fee levels — this mirrors the original report's caveat that hitting `2**88-1` required an atypically large `liquidationInitialAsk`, yet was still accepted as valid because nothing in the code prevents it. Here too, nothing in `accumulate_fees`, `Fees` storage, or `withdraw` enforces an upper bound, so the invariant "recorded balance always payable in full" is not actually guarded by the type system or any explicit check — it silently relies on balances never getting that large. Any unprivileged relayer that legitimately accrues fees over a long enough operating period (or via a large number of accepted deliveries) can hit this without any malicious peer, relayer collusion, or governance action.

### Recommendation
Replace `available_amount.low_u128()` with a checked/saturating conversion that either (a) rejects/errors the withdrawal if `available_amount > u128::MAX`, splitting into multiple withdrawals instead, or (b) only zeroes/decrements `Fees` by the amount actually dispatched (`available_amount - remainder`) rather than unconditionally zeroing the full entry. The `Withdraw` event should also carry the amount that was actually encoded into the dispatched request, not the pre-truncation balance.

### Proof of Concept
1. Set `Fees::<T>::insert(dest_chain, relayer_address, U256::from(u128::MAX) + U256::from(1_000_000u128))` (representing accumulated fees exceeding `u128::MAX` from many `accumulate_fees` calls over time).
2. Call `withdraw_fees` for a substrate `dest_chain` with a valid signature.
3. Observe the dispatched `WithdrawRelayerFees` body contains `amount = available_amount.low_u128() = 1_000_000` (the value wraps past `u128::MAX`), while `Fees::<T>::get(dest_chain, relayer_address)` is reset to `U256::zero()` — losing the `u128::MAX` portion of the balance permanently, with the `Withdraw` event still reporting `amount: available_amount` (the full, untruncated original balance) despite only `1_000_000` being paid out.

### Citations

**File:** modules/pallets/relayer/src/lib.rs (L111-122)
```rust
	/// double map of address to source chain, which holds the amount of the relayer address
	#[pallet::storage]
	#[pallet::getter(fn relayer_fees)]
	pub type Fees<T: Config> = StorageDoubleMap<
		_,
		Blake2_128Concat,
		StateMachine,
		Blake2_128Concat,
		Vec<u8>,
		U256,
		ValueQuery,
	>;
```

**File:** modules/pallets/relayer/src/accumulate.rs (L134-146)
```rust
			let _ = Fees::<T>::try_mutate(state_machine, beneficiary_address.clone(), |inner| {
				*inner += total_fee;
				Ok::<(), ()>(())
			});

			beneficiary_address
		} else {
			let _ = Fees::<T>::try_mutate(state_machine, delivery_address.clone(), |inner| {
				*inner += total_fee;
				Ok::<(), ()>(())
			});

			delivery_address
```

**File:** modules/pallets/relayer/src/accumulate.rs (L353-368)
```rust
	pub fn accumulate_fee_and_deposit_event(
		state_machine: StateMachine,
		address: Vec<u8>,
		fee: U256,
	) {
		let _ = Fees::<T>::try_mutate(state_machine, address.clone(), |inner| {
			*inner += fee;
			Ok::<(), ()>(())
		});

		Self::deposit_event(Event::<T>::AccumulateFees {
			address: sp_runtime::BoundedVec::truncate_from(address),
			state_machine,
			amount: fee,
		});
	}
```

**File:** modules/pallets/relayer/src/withdrawal.rs (L16-27)
```rust
//! Relayer fee withdrawal.
//!
//! Once fees have been accumulated into [`crate::pallet::Fees`] by
//! [`crate::accumulate`], relayers withdraw them via [`Pallet::withdraw`].
//! The flow:
//!
//! 1. The relayer signs a `(nonce, dest_chain, beneficiary?)` payload with their per-chain key (EVM
//!    secp256k1 / sr25519 / ed25519).
//! 2. The pallet verifies the signature, increments the per-relayer nonce, and dispatches an ISMP
//!    POST request to the destination's host manager (EVM) or `HYPERBRIDGE_MODULE_ID` (substrate)
//!    instructing it to disburse `available_amount` of the fee token to the beneficiary.
//! 3. The `Fees` entry is zeroed so the same balance cannot be withdrawn twice.
```

**File:** modules/pallets/relayer/src/withdrawal.rs (L134-143)
```rust
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
```

**File:** modules/pallets/relayer/src/withdrawal.rs (L177-184)
```rust
		Fees::<T>::insert(withdrawal_data.dest_chain, address.clone(), U256::zero());

		Self::deposit_event(Event::<T>::Withdraw {
			address: sp_runtime::BoundedVec::truncate_from(address.clone()),
			beneficiary_address: sp_runtime::BoundedVec::truncate_from(beneficiary_address),
			state_machine: withdrawal_data.dest_chain,
			amount: available_amount,
		});
```

**File:** evm/rust/src/host_params.rs (L212-221)
```rust
pub struct WithdrawalParams {
	/// 20-byte EVM beneficiary address. Stored as a `Vec<u8>` so the SCALE
	/// extrinsic input doesn't have to pre-validate the length.
	pub beneficiary_address: Vec<u8>,
	/// Amount to withdraw, in the token's smallest unit.
	pub amount: U256,
	/// ERC20 token contract to withdraw. The EVM host treats the zero address
	/// as the chain's native asset.
	pub token: H160,
}
```

**File:** evm/rust/src/host_params.rs (L254-259)
```rust
		Ok(WithdrawParamsAbi {
			beneficiary: beneficiary.0.into(),
			amount: alloy_primitives::U256::from_be_bytes(value.amount.to_big_endian()),
			token: value.token.0.into(),
		})
	}
```
