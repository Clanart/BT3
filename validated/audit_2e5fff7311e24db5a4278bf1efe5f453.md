### Title
Permissionless replay of a persistently-failing POST request drains a source app's paid bandwidth allowance before message success is ever achieved - (File: `parachain/runtimes/gargantua/src/ismp.rs` / `parachain/runtimes/nexus/src/ismp.rs`, `modules/ismp/core/src/handlers/request.rs`, `modules/pallets/bandwidth/src/lib.rs`)

### Summary
The external report's broken invariant is: a callback/hook that a caller does not control can be triggered repeatedly and unconditionally, while state that should only be consumed on *success* is instead consumed on every *attempt*, permanently damaging the victim. In Alchemy, reverting hooks block uninstall/upgrade forever. In Hyperbridge, the analog is in `ProxyModule::on_accept`: the bandwidth gate (`pallet_bandwidth::try_consume`) is charged against the request's `from` app *before* the destination module's `on_accept` runs, and `pallet-ismp`'s message handler deletes the request receipt on any module failure so the exact same request can be resubmitted by anyone. This lets an unprivileged, permissionless relayer replay a request that keeps failing at the destination module, charging the source app's prepaid bandwidth balance on every single attempt until it is fully drained — with the app never having done anything wrong and having no way to prevent the replay.

### Finding Description
`ProxyModule::on_accept` in both `parachain/runtimes/gargantua/src/ismp.rs` and `parachain/runtimes/nexus/src/ismp.rs` unconditionally consumes bandwidth for a message before dispatching it to the target pallet: [1](#0-0) 

```rust
#[cfg(not(feature = "no-bandwidth"))]
if !pallet_bandwidth::Pallet::<Runtime>::is_purchase_message(&request) {
    let bytes = ismp::abi::encode_post_request(&request).len() as u32;
    <pallet_bandwidth::Pallet<Runtime> as pallet_bandwidth::BandwidthGate>::try_consume(
        &request.source, &request.from, bytes,
    )
    .map_err(|err| anyhow!("bandwidth gate: {err} ..."))?;
}
...
match pallet_id {
    ... => pallet_hyper_fungible_token::Pallet::<Runtime>::default().on_accept(request),
    _ => Err(anyhow!("Destination module not found")),
}
```

`try_consume` in `modules/pallets/bandwidth/src/lib.rs` deducts bytes from the `(source, from)` app's FIFO subscription list irreversibly and emits `BandwidthConsumed`: [2](#0-1) 

This charge happens whether or not the subsequent `on_accept` for the actual destination pallet (e.g. `pallet_hyper_fungible_token`) succeeds. The core ISMP request handler then deletes the request receipt on any module failure, explicitly to allow the request to be retried: [3](#0-2) 

```rust
// Store request receipt to prevent reentrancy attack
let signer = host.store_request_receipt(&wrapped_req, &msg.signer)?;
let res = cb.on_accept(request.clone()).map(|weight| { ... });
// Delete receipt if module callback failed so it can be timed out
if res.is_err() {
    host.delete_request_receipt(&wrapped_req)?;
}
```

Because `handle()` for `RequestMessage` is a public, permissionless entrypoint (any signer can submit a `RequestMessage` with a valid membership proof for an already-dispatched source-chain request), and because a failed `on_accept` leaves no receipt behind, **anyone** can resubmit the identical `RequestMessage`/proof for a request that keeps failing at the destination pallet (e.g. `pallet_hyper_fungible_token::on_accept`, which can fail deterministically and repeatably for reasons outside the sender's control — `HftError::UnknownSourceContract`, `DecimalsNotConfigured`, `InvalidAmountConversion`, a filtered/failing embedded runtime call, etc., see `modules/pallets/hyper-fungible-token/src/module.rs` lines 55–91, 194–200). Each resubmission re-enters `ProxyModule::on_accept`, re-runs the bandwidth gate, and re-charges the same `bytes` against the sending app's `(source, from)` allowance — even though the message never succeeds and the sender gains nothing.

Existing guards do not stop this:
- The duplicate-receipt check (`request.rs` line 58) only blocks replay of *successful* deliveries, not failed ones — it is explicitly designed to allow retries.
- `is_purchase_message` only exempts bandwidth-purchase messages, not ordinary application messages.
- Nothing rate-limits or ties bandwidth consumption to eventual success; it is billed per submission attempt, not per delivered byte of *useful* work.

### Impact Explanation
This is a bandwidth-accounting violation matching the stated pivot ("bandwidth balances must move exactly once and only to the rightful beneficiary and amount"). A prepaid bandwidth subscription (`Allowance<T>`) that an app purchased is drained by attacker-controlled resubmissions of a request the app never controls the outcome of, with zero benefit delivered. This can fully exhaust a victim app's allowance (`GateError::NoAllowance` / `Insufficient`), denying that app's *legitimate* future cross-chain messages from being accepted at all (`ProxyModule::on_accept` will reject them once the allowance is gone) — a real loss of a purchased/paid resource and a denial-of-service on the app's ability to use the bridge, without requiring any malicious relayer/prover/admin assumption: any ordinary, honest user permitted to submit ISMP messages can trigger this by simply resubmitting a `RequestMessage` that is known to fail.

### Likelihood Explanation
Likelihood is moderate-to-high in practice: any request that legitimately fails at the destination pallet even once (misconfigured `Precisions`/`ContractToAsset` mapping, a filtered embedded call, a transiently-failing mint) becomes an indefinitely resubmittable "bandwidth-draining oracle" until either it succeeds or the source-chain timeout elapses. Submitting the `RequestMessage` handler call is a normal, cheap, permissionless operation (paying only local extrinsic weight fees), so an attacker (or a griefer unrelated to the sender) can loop resubmission cheaply while the victim's bandwidth balance is metered in bytes-per-attempt rather than bytes-per-success.

### Recommendation
- Do not charge bandwidth before the destination module's `on_accept` succeeds; either meter bandwidth after a successful callback, or refund/roll back the bandwidth debit when `on_accept` returns `Err`.
- Alternatively/additionally, rate-limit or dedupe resubmission attempts for the same failing request commitment (e.g., only allow the bandwidth gate to charge once per commitment regardless of how many times delivery is attempted, tracking a "charged" flag keyed by the request commitment rather than re-running `try_consume` on every retry).
- Ensure this charge-then-maybe-fail pattern is audited across all `IsmpModule::on_accept` router implementations that gate on bandwidth before dispatch (`gargantua` and `nexus` `ProxyModule`s), since both share the vulnerable pattern.

### Proof of Concept
1. App `A` on source chain `S` purchases a bandwidth tier for `(S, A)`, giving it `N` bytes of allowance via `pallet_bandwidth`.
2. `A` dispatches a legitimate cross-chain POST request destined for `pallet_hyper_fungible_token` on the destination chain, but the request will deterministically fail `on_accept` (e.g., the destination lacks a `Precisions` entry for the asset/source pair, so `HftError::DecimalsNotConfigured` is returned) — this is a realistic misconfiguration/edge case independent of any wrongdoing by `A`.
3. Any unprivileged account (not `A`, not a relayer under `A`'s control) repeatedly calls the `pallet-ismp` request handler (`modules/ismp/core/src/handlers/request.rs::handle`) with the same `RequestMessage`/proof.
4. Each call: `ProxyModule::on_accept` → `try_consume(&request.source, &request.from, bytes)` deducts `bytes` from `A`'s `(S, A)` allowance (`modules/pallets/bandwidth/src/lib.rs` lines 509-564) → `pallet_hyper_fungible_token::on_accept` fails → `request.rs` deletes the receipt, allowing the next resubmission.
5. Repeating step 3/4 `N / bytes` times fully exhausts `A`'s bandwidth allowance without `A` ever getting a single message through, and without the attacker needing any special privilege, relayer role, or malicious infrastructure — only the ability to submit an already-valid request message repeatedly.

Note: I was not able to execute this scenario end-to-end in a live test environment (no terminal/test-runner access here) — the finding is derived from static code review of the cited files and the documented "receipt deleted on failure to allow retry" behavior. A background Devin session with repo/test access could confirm this with a concrete integration test in `modules/pallets/testsuite`.

### Citations

**File:** parachain/runtimes/gargantua/src/ismp.rs (L381-422)
```rust
		#[cfg(not(feature = "no-bandwidth"))]
		if !pallet_bandwidth::Pallet::<Runtime>::is_purchase_message(&request) {
			let bytes = ismp::abi::encode_post_request(&request).len() as u32;
			<pallet_bandwidth::Pallet<Runtime> as pallet_bandwidth::BandwidthGate>::try_consume(
				&request.source,
				&request.from,
				bytes,
			)
			.map_err(|err| {
				anyhow!(
					"bandwidth gate: {err} (source={:?}, from={:x?})",
					request.source,
					request.from
				)
			})?;
		}

		if request.dest != HostStateMachine::get() {
			Ismp::dispatch_request(
				Request::Post(request),
				FeeMetadata::<Runtime> { payer: [0u8; 32].into(), fee: Default::default() },
			)?;
			return Ok(Weight::from_parts(0, 0));
		}

		let pallet_id =
			ModuleId::from_bytes(&request.to).map_err(|err| Error::Custom(err.to_string()))?;

		match pallet_id {
			pallet_ismp_demo::PALLET_ID =>
				pallet_ismp_demo::IsmpModuleCallback::<Runtime>::default().on_accept(request),

			#[cfg(not(feature = "no-bandwidth"))]
			id if id == ModuleId::Pallet(pallet_bandwidth::pallet::PALLET_BANDWIDTH) =>
				pallet_bandwidth::Pallet::<Runtime>::default().on_accept(request),

			pallet_hyper_fungible_token::PALLET_ID =>
				pallet_hyper_fungible_token::Pallet::<Runtime>::default().on_accept(request),

			_ => Err(anyhow!("Destination module not found")),
		}
	}
```

**File:** modules/pallets/bandwidth/src/lib.rs (L509-564)
```rust
impl<T: Config> BandwidthGate for Pallet<T> {
	fn try_consume(
		source: &ismp::host::StateMachine,
		app: &[u8],
		bytes: u32,
	) -> Result<(), GateError> {
		let key = AppKey::truncate_from(app.to_vec());
		if Allowlist::<T>::contains_key(source, &key) {
			return Ok(());
		}

		let need: u128 = bytes.into();
		let now = <T as pallet_ismp::Config>::TimestampProvider::now().as_secs();

		let total = pallet::Allowance::<T>::mutate(source, &key, |list| {
			// Sweep expired in-place. Order-preserving.
			list.retain(|s| s.expires_at > now);

			if list.is_empty() {
				return Err(GateError::NoAllowance);
			}

			let total: u128 = list.iter().map(|s| s.remaining_bytes).sum();
			if total < need {
				return Err(GateError::Insufficient { remaining: total, required: need });
			}

			// Drain from the front in insertion order. Once a sub is
			// fully consumed, pop it and continue with the next.
			// `get_mut` defends against a malformed list that satisfies
			// the `total >= need` precheck but is structurally empty;
			// we'd otherwise panic via `list[0]`.
			let mut left = need;
			while left > 0 {
				let Some(head) = list.get_mut(0) else {
					return Err(GateError::NoAllowance);
				};
				let take = head.remaining_bytes.min(left);
				head.remaining_bytes = head.remaining_bytes.saturating_sub(take);
				left = left.saturating_sub(take);
				if head.remaining_bytes == 0 {
					list.remove(0);
				}
			}

			Ok(total)
		})?;

		Self::deposit_event(Event::BandwidthConsumed {
			source: *source,
			app: key,
			bytes: need,
			remaining: total - need,
		});
		Ok(())
	}
```

**File:** modules/ismp/core/src/handlers/request.rs (L111-126)
```rust
				// Store request receipt to prevent reentrancy attack
				let signer = host.store_request_receipt(&wrapped_req, &msg.signer)?;
				let res = cb.on_accept(request.clone()).map(|weight| {
					total_weights.saturating_accrue(weight);

					let commitment = hash_request::<H>(&wrapped_req);
					Event::PostRequestHandled(RequestResponseHandled {
						commitment,
						relayer: signer,
					})
				});
				// Delete receipt if module callback failed so it can be timed out
				if res.is_err() {
					host.delete_request_receipt(&wrapped_req)?;
				}
				Ok(res)
```
