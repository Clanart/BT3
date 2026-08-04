Based on the investigation, the closest local analog to the `MuteBond` "lower a limit below a currently-accrued value → DOS" pattern is in `pallet-relayer`'s minimum-withdrawal threshold and its all-or-nothing withdrawal path.

### Title
Raising `MinimumWithdrawalAmount` above an already-accrued relayer balance permanently locks that balance (no partial withdrawal, no cap check) - (File: `modules/pallets/relayer/src/withdrawal.rs`)

### Summary
`pallet-relayer::withdraw` requires the entire accrued `Fees[dest_chain][relayer]` balance to be at least `MinimumWithdrawalAmount` (or the global `MinWithdrawal` default) before it will dispatch a withdrawal, and it always withdraws the **full** balance, resetting it to zero [1](#0-0) . There is no partial-withdrawal mechanism. `set_minimum_withdrawal` allows the threshold to be changed for any `state_machine` at any time with no check against the amounts currently accrued in `Fees` for that chain [2](#0-1) .

### Finding Description
This mirrors the `MuteBond` bug class exactly: a mutable cap/threshold parameter (`maxPayout` there, `MinimumWithdrawalAmount` here) can be moved past a value that is already committed on-chain (`payoutTotal` there, `Fees[dest_chain][address]` here), with no invariant enforced between the two. Fees continue to accumulate into the `Fees` map via `accumulate_fees` regardless of the current threshold [3](#0-2) . If `set_minimum_withdrawal` raises the threshold above a relayer's current accrued balance for that `(state_machine)`, `withdraw` will unconditionally reject the withdrawal (`Error::NotEnoughBalance`) until the balance grows past the new threshold. Because withdrawal is strictly all-or-nothing (there is no way to withdraw less than the full accrued amount, and no per-relayer override), a relayer whose future traffic to that destination chain is low or who has stopped relaying entirely can have its already-earned fees frozen indefinitely.

### Impact Explanation
This is a loss/lock-of-funds vector on live relayer fee balances: honest relayers' already-earned rewards on `Fees::<T>` become unwithdrawable the moment governance adjusts `MinimumWithdrawalAmount` upward for a chain without checking outstanding balances, with no code path to reconcile or partially recover. Since balance is only ever cleared to zero on a full, successful withdrawal [4](#0-3) , there's no fallback for affected relayers besides waiting for enough new deliveries on that exact destination chain to cross the raised bar — which for lower-traffic routes may never happen.

### Likelihood Explanation
The check is a one-line, unguarded threshold comparison that never consults existing `Fees` balances before allowing the update; any adjustment to `MinimumWithdrawalAmount` (a routine operational parameter, not a rare governance emergency action) can trigger this without any malicious intent, exactly like the original `MuteBond.setMaxPayout` report, which was also triggered by a benign parameter change with an unlucky race relative to accrued state.

### Recommendation
Before writing a new `MinimumWithdrawalAmount` for a `state_machine`, either (a) skip validation only for balances that will exceed it going forward and leave existing balances withdrawable under the old threshold (e.g., record a `MinWithdrawalAt` snapshot per relayer at accrual time), or (b) support partial withdrawals so the accrued balance is never locked out entirely, or (c) simplest: allow `withdraw` to succeed whenever `available_amount > 0` and the caller explicitly opts into a below-minimum withdrawal, decoupling the anti-dust minimum from already-earned funds.

### Proof of Concept
```
1. Relayer R delivers messages to StateMachine::Evm(1), accumulating
   Fees[Evm(1)][R] = 40 (via accumulate_fees).
2. Governance calls set_minimum_withdrawal(Evm(1), 100) — a routine
   threshold bump, unrelated to R's specific balance.
3. R calls withdraw_fees for Evm(1): available_amount (40) < 100,
   so Error::NotEnoughBalance is returned every time.
4. R's 40 units remain frozen in Fees::<T> until R (or any other
   relayer credited to the same key) accumulates 60+ more fee on
   Evm(1) — which may never occur if that route goes quiet.
```

Note: this path is triggered by a governance/admin call (`RelayerOrigin`), similar to how the original `MuteBond` finding was triggered by the contract owner's routine `setMaxPayout` call rather than by an attacker — the vulnerability is the missing invariant check, not malicious governance intent.

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

**File:** modules/pallets/relayer/src/accumulate.rs (L134-144)
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
```
