## Title
Relayer fee balance is zeroed before dispatch and never restored on timeout, permanently burning relayer withdrawals — ([File: modules/pallets/relayer/src/withdrawal.rs])

### Summary
`pallet-ismp-relayer::withdraw()` zeroes a relayer's accumulated `Fees` balance and dispatches a cross-chain POST request instructing the destination chain to pay out that amount, exactly mirroring the Livepeer `L1Migrator.migrateLPT()` bug: an accounting value is consumed/reset in anticipation of a cross-chain transfer completing, but the completion is not guaranteed, and there is no restoration path if the outbound message fails to be delivered (times out). On Hyperbridge, the module ID used for this dispatch (`b"ISMP-RLYR"`) is not wired to any `on_timeout` handler that restores the zeroed balance, so a timed-out withdrawal permanently and irrecoverably destroys the relayer's fee balance.

### Finding Description
`Pallet::withdraw()` in [1](#0-0)  reads `available_amount` from `Fees::<T>::get(...)`, dispatches a `DispatchPost` with `from: MODULE_ID.to_vec()` (`MODULE_ID = b"ISMP-RLYR"`, defined at [2](#0-1) ) to the destination chain's `HostManager`/`HYPERBRIDGE_MODULE_ID`, and then unconditionally zeroes the balance:

```
dispatcher.dispatch_request(DispatchRequest::Post(post), ...).map_err(...)?;
Fees::<T>::insert(withdrawal_data.dest_chain, address.clone(), U256::zero());
```

The doc comment in the same file even states the design intent explicitly: "The `Fees` entry is zeroed so the same balance cannot be withdrawn twice... The on-chain effect is just dispatching the message; the destination chain settles the payout when the ISMP request is delivered there." [3](#0-2) 

This design assumes the dispatched POST always eventually settles. But ISMP explicitly supports timeouts for exactly this failure mode: if the destination chain becomes unresponsive or the request otherwise expires, the request times out and `IsmpModule::on_timeout` is invoked for the module identified by the request's `from` field [4](#0-3) . The framework's own documentation is explicit about the required contract: *"the `IsmpModule` is responsible for maintaining all invariants before modifying its internal state... revert any state changes that were made prior to dispatching the request"* [5](#0-4) .

However, the Nexus runtime's `ProxyModule::on_timeout` — the router that dispatches `on_timeout` calls based on the `from` module id on the chain where `pallet-ismp-relayer::withdraw()` actually runs — only special-cases `pallet_hyper_fungible_token::PALLET_ID`; every other module id, including `b"ISMP-RLYR"`, falls through to the default arm which just returns `Ok(...)` with no state change:

```rust
match pallet_id {
    id if id == ModuleId::Pallet(pallet_bandwidth::pallet::PALLET_BANDWIDTH) => ...
    pallet_hyper_fungible_token::PALLET_ID => ...
    _ => Ok(Weight::from_parts(300_000_000, 0)),
}
``` [6](#0-5) 

Because this callback returns `Ok`, the core timeout handler treats it as success and permanently finalizes the timeout — deleting the request commitment via `host.on_request_timeout(&request, meta)` [7](#0-6)  — with no code path anywhere in `pallet-ismp-relayer` that re-credits `Fees`. There is no `impl IsmpModule for Pallet<T>` in the relayer pallet at all (confirmed absent by search), so no timeout-driven restoration logic exists for withdrawals.

### Impact Explanation
This is a direct, unauthorized loss of relayer reward funds: the exact bug class flagged in the source report — an accounting balance is consumed on the assumption a cross-chain transfer will complete, but when that transfer fails (times out), the balance is not restored and the funds become permanently unclaimable. Any relayer whose destination chain misses the request's timeout window (chain congestion, liveness fault, challenge-period delay exceeding `timeout_timestamp`, or destination chain briefly unable to process messages) loses 100% of their accrued, previously-earned fee balance for that chain with no recovery mechanism — a genuine "loss of funds" impact matching the bounty's accepted-impact list.

### Likelihood Explanation
No malicious actor is required. `PostRequest` timeouts are a normal, expected, permissionless occurrence in ISMP (as documented in `docs/content/protocol/ismp/timeouts.mdx`), and a relayer's own `withdraw()` call is a legitimate, unprivileged, self-serve action any relayer performs at will. Any transient unavailability of the destination chain around the withdrawal's timeout window triggers the loss — this requires no relayer/prover/admin misbehavior, no front-running, and no privileged access.

### Recommendation
Register an `IsmpModule` implementation for `pallet-ismp-relayer` (module id `b"ISMP-RLYR"`) in the router (both `nexus` and any other chain where `withdraw()` runs), whose `on_timeout` re-credits `Fees::<T>::insert(dest_chain, address, available_amount)` for the timed-out withdrawal request. Alternatively, defer zeroing `Fees` until a delivery/settlement confirmation is observed rather than zeroing eagerly at dispatch time, consistent with the pattern already used elsewhere in the codebase (e.g. `pallet_hyper_fungible_token`'s `on_timeout` refunds escrowed/burned amounts, and `pallet-ismp`'s built-in relayer-fee escrow refunds the payer on timeout, as documented in `docs/content/developers/polkadot/fees.mdx`).

### Proof of Concept
1. Relayer accumulates fees on Hyperbridge for `dest_chain = X` via `accumulate_fees`, growing `Fees::<T>::get(X, relayer)` above the minimum withdrawal threshold.
2. Relayer calls `withdraw_fees` with a valid signature for `dest_chain = X`. `Pallet::withdraw()` dispatches the POST request (`from = b"ISMP-RLYR"`) and immediately sets `Fees::<T>::insert(X, relayer, U256::zero())` [8](#0-7) .
3. Destination chain `X` fails to process the request before `timeout_timestamp` elapses (e.g. transient liveness issue, or an attacker-controllable delay if `X` is a chain with adjustable finality/challenge periods).
4. Anyone submits a `TimeoutMessage::Post` proof for this request. The core timeout handler at [9](#0-8)  looks up the module for `from = b"ISMP-RLYR"`, which resolves (on Nexus) to `ProxyModule::on_timeout`, hits the default arm, and returns `Ok` — finalizing the timeout and deleting the commitment.
5. `Fees::<T>::get(X, relayer)` remains `0` permanently; the relayer has no way to reclaim the amount that was withdrawn but never delivered.

### Citations

**File:** modules/pallets/relayer/src/withdrawal.rs (L16-30)
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
//!
//! The on-chain effect is just dispatching the message; the destination chain settles the
//! payout when the ISMP request is delivered there.
```

**File:** modules/pallets/relayer/src/withdrawal.rs (L116-177)
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

		let post = DispatchPost {
			dest: withdrawal_data.dest_chain,
			from: MODULE_ID.to_vec(),
			to,
			body,
			timeout: 0,
		};

		// Account is not useful in this case
		dispatcher
			.dispatch_request(
				DispatchRequest::Post(post),
				FeeMetadata { payer: [0u8; 32].into(), fee: Default::default() },
			)
			.map_err(|_| Error::<T>::DispatchFailed)?;

		Fees::<T>::insert(withdrawal_data.dest_chain, address.clone(), U256::zero());
```

**File:** modules/pallets/relayer/src/lib.rs (L53-53)
```rust
pub const MODULE_ID: &'static [u8] = b"ISMP-RLYR";
```

**File:** modules/ismp/core/src/handlers/timeout.rs (L90-134)
```rust
			let router = host.ismp_router();
			requests
				.into_iter()
				.map(|post| {
					let cb = router.module_for_id(post.from.clone())?;
					let request = Request::Post(post.clone());
					// Re-check the commitment right before dispatch. The up-front
					// pass above runs before any callback executes; a prior
					// on_timeout in this same batch could have caused the
					// commitment for this request to be removed (directly or by
					// re-entering the handler), and we must not invoke
					// on_timeout for a request that is no longer pending.
					let commitment = hash_request::<H>(&request);
					if host.request_commitment(commitment).is_err() {
						Err(Error::UnknownRequest { meta: (&post).into() })?
					}
					// Delete commitment to prevent rentrancy attack
					let meta = host.delete_request_commitment(&request)?;
					let mut signer = None;
					// If it was a routed request delete the receipt
					if host.host_state_machine() != post.source {
						signer = host.delete_request_receipt(&request).ok();
					}
					let res = cb.on_timeout(request.clone()).map(|weight| {
						total_module_weight.saturating_accrue(weight);
						let commitment = hash_request::<H>(&request);
						Event::PostRequestTimeoutHandled(TimeoutHandled {
							commitment,
							source: post.source,
							dest: post.dest,
						})
					});
					if res.is_ok() {
						host.on_request_timeout(&request, meta)?;
					} else {
						// Module callback failed; restore commitment so the request
						// can be retried.
						host.store_request_commitment(&request, meta)?;
						if host.host_state_machine() != post.source && signer.is_some() {
							host.store_request_receipt(
								&request,
								&signer.ok_or_else(|| anyhow::anyhow!("Infallible"))?,
							)?;
						}
					}
```

**File:** docs/content/protocol/ismp/timeouts.mdx (L47-58)
```text
The timeout `handle` is used to notify onchain `IsmpModule`s of outgoing requests that have now timed out. A relayer will construct the `TimeoutMessage` which holds a batch of these messages, and their relevant proofs. The handler will perform the following operations

- Assert that the state machine's consensus client is not frozen
- Assert that the configured `challenge_period` for the `StateCommitment` has elapsed
- Assert that the messages have indeed timed out
- Assert that the claimed messages are known by the host
- Assert that the relevant state machine's time has advanced past the `timeout_timestamp` of specified messages.
- Assert that the relevant non-membership proofs for the messages are valid
- Finally dispatch the timeouts to the relevant `IsmpModule::on_timeout` and delete the commitments for the outgoing messages.

<Callout title={'Danger'} type={"warn"}>
It's important to note that if the `IsmpModule::on_timeout` does not return `Ok`, the commitment of the relevant messages will not be deleted, allowing the timeout to be **replayed**. Consequently, the `IsmpModule` is responsible for maintaining all invariants before modifying it's internal state to prevent partial state changes that could result in critical vulnerabilities in their timeout handler. This model ensures that if a timeout cannot be executed successfully, it can be retried later.
```

**File:** parachain/runtimes/nexus/src/ismp.rs (L443-449)
```rust
		let pallet_id = ModuleId::from_bytes(from).map_err(|err| Error::Custom(err.to_string()))?;
		match pallet_id {
			pallet_hyper_fungible_token::PALLET_ID =>
				pallet_hyper_fungible_token::Pallet::<Runtime>::default().on_timeout(timeout),
			_ => Ok(Weight::from_parts(300_000_000, 0)),
		}
	}
```
