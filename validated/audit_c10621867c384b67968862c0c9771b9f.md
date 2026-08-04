### Title
Bandwidth gate is documented but never enforced on inbound `GetResponse` delivery, letting local modules receive metered payload for free - ([File: parachain/runtimes/gargantua/src/ismp.rs])

### Summary
This is the local analog of the OpenDollar "Fake BasicActions" bug: a mandatory fee/accounting step (tax payment in OpenDollar, bandwidth "payment" here) is expected to be enforced on every code path that performs the metered action, but one path silently skips it while claiming — in its own comment — that it performs the check. Just as `BasicActions`'s tax logic lived outside the core ledger and could be skipped by swapping the delegatecall target, Hyperbridge's bandwidth "tax" logic lives outside the core ISMP response-handling path (in `ProxyModule`) and is simply missing on the `on_response` leg, even though the sibling `on_accept` leg and the state-coprocessor's own Get-response path both call it.

### Finding Description
`pallet-bandwidth` is designed to meter every byte of ISMP traffic that a registered `(chain, app)` pair consumes, via `BandwidthGate::try_consume`, so that apps must pre-pay for bandwidth instead of getting free per-message delivery. [1](#0-0) 

On the inbound POST-request leg, `ProxyModule::on_accept` in the gargantua runtime explicitly enforces the gate before dispatching to the destination module (skipping only authenticated purchase messages): [2](#0-1) 

The sibling leg, `ProxyModule::on_response`, carries a comment stating it "mirrors the request path in `on_accept`" and that "the chain and module that produced the response pay for the bytes they deliver" — but the actual `BandwidthGate::try_consume` call is absent from the function body. It goes straight from the chain-id check to dispatching the response to `pallet_ismp_demo`: [3](#0-2) 

That this omission is a real bug (not an intentional design where responses are unmetered) is confirmed by the state-coprocessor's own Get-handling code, which does call `try_consume` on the exact same `GetResponse` shape, keyed by `response.get.source` / `response.get.from`, immediately after producing/verifying a response: [4](#0-3) 

The `ProxyModule::on_response` function in gargantua's `ismp.rs` is invoked by the core ISMP response handler (`modules/ismp/core/src/handlers/response.rs`) via `router.module_for_id(request.from.clone())` for every verified, proof-checked `GetResponse` destined for a local Hyperbridge module: [5](#0-4) 

Because `Router::module_for_id` always returns `ProxyModule` regardless of the target module id, `ProxyModule::on_response` is the *only* gate through which locally-destined `GetResponse`s pass — and it does not call `try_consume`.

### Impact Explanation
Bandwidth is an explicitly paid, prepaid resource — apps must call `purchase()` and pay a fee token to receive a byte allowance, and every metered request is supposed to drain that allowance; running out means messages get rejected. [6](#0-5) 
Because `on_response` never calls the gate, any local module that dispatches `Get` requests (today `pallet_ismp_demo`, and by design any future GET-consuming module reachable through the same `ProxyModule`) receives the resulting `GetResponse` payload — of arbitrary size, since size is attacker/relayer-influenced by how much data the response carries — without any bandwidth deduction. This is a direct loss of protocol revenue: the "tax" (bandwidth fee) that governance and the pallet's whole subscription/tier model exist to enforce is silently bypassed on this leg, exactly mirroring the OpenDollar finding's characterization ("results in a loss of yield/protocol fees, not user funds"). It also creates an asymmetric metering model: outbound-POST traffic is charged, inbound Get-response traffic to the same class of module is not, which lets an app design its integration to prefer the unmetered Get/response path and drain hyperbridge relaying/storage resources for free.

### Likelihood Explanation
No privileged actor, malicious relayer, or malformed proof is required. Any application already authorized to dispatch a `Get` request to a destination chain (a completely normal, unprivileged action) automatically benefits from this bypass every time a legitimate relayer delivers the corresponding proven `GetResponse` back through the standard core ISMP response path. The bug is deterministic and always triggers — it is a straight code omission, not a race or edge case — so exploitation likelihood is very high whenever any bandwidth-gated app uses the Get/response flow instead of only POST.

### Recommendation
Add the same `BandwidthGate::try_consume(&response.get.source, &response.get.from, bytes)` call (using `ismp::abi::encode_get_response(&response).len()` for `bytes`, mirroring `on_accept`'s `encode_post_request` sizing and the state-coprocessor's own pattern) to `ProxyModule::on_response` in both `parachain/runtimes/gargantua/src/ismp.rs` and `parachain/runtimes/nexus/src/ismp.rs`, before dispatching to the destination module, respecting the same `#[cfg(not(feature = "no-bandwidth"))]` gate used on the request leg. Apply an equivalent allowlist/purchase-message exemption if any Get-response flows are meant to remain gate-exempt by design (there is currently no such carve-out visible for responses).

### Proof of Concept
1. Register `(chain, app)` bandwidth for `pallet_ismp_demo`'s module id with zero remaining allowance (or simply never purchase any bandwidth for it) — confirm `BandwidthGate::try_consume` for a `Get` on that `(source, app)` pair returns `GateError::NoAllowance`.
2. Have `pallet_ismp_demo` dispatch a `Get` request to a destination chain (a call already exposed for testing, see `IsmpDemo::dispatch_to_evm` referenced in `docs/outbound-request-incentivization.md`).
3. Have a relayer deliver the response through the normal core-ISMP response path (`handlers::response::handle` → `router.module_for_id` → `ProxyModule::on_response`).
4. Observe that `on_response` in `parachain/runtimes/gargantua/src/ismp.rs` (lines 424-441) never calls `try_consume`, so the response is delivered and `pallet_ismp_demo::on_response` runs to completion despite the app having zero bandwidth allowance — whereas an equivalent zero-allowance POST request to the same app id is rejected at `on_accept` (lines 375-397) with a "bandwidth gate" error. This differential behavior is directly observable by comparing the two code paths; no live network access was used to confirm it beyond static code inspection, so it is recommended to add a unit/integration test asserting `try_consume` is invoked (or a spend event emitted) for a metered `on_response` call, analogous to existing tests in `modules/pallets/testsuite/src/tests/pallet_bandwidth.rs`.

### Citations

**File:** docs/content/developers/evm/bandwidth/overview.mdx (L8-10)
```text
Hyperbridge meters outbound traffic per `(source chain, app)`. Instead of paying a protocol fee on every dispatch, an app pre-pays for a tier and earns a byte allowance that drains as it sends messages. The allowance is enforced by the **bandwidth gate** on Hyperbridge — a hook the ISMP router consults on every inbound request from a source chain. When the gate is empty, the message is rejected.

Bandwidth is sold per **tier** (a byte budget × a time window) and per **month** (a multiplier on both). Purchases are made from any source chain by calling `purchase()` on the [`BandwidthManager`](https://github.com/polytope-labs/hyperbridge/blob/main/evm/src/apps/BandwidthManager.sol) contract; the contract dispatches a credit message to [`pallet-bandwidth`](https://github.com/polytope-labs/hyperbridge/blob/main/modules/pallets/bandwidth/src/lib.rs) on Hyperbridge, which mints a new subscription for the target `(chain, app)`.
```

**File:** docs/content/developers/evm/bandwidth/overview.mdx (L83-93)
```text
## The Gate

Every non-purchase POST request from a registered source chain runs through `BandwidthGate::try_consume(source, app, bytes)`:

1. If the app is on the **allowlist** for that source, return `Ok` without touching the ledger.
2. Sweep expired subscriptions in place.
3. If no live subscriptions remain → `GateError::NoAllowance`.
4. Sum `remaining_bytes` across live entries. If the sum is short → `GateError::Insufficient { remaining, required }`. **No mutation happens in this case** — the caller can retry after a top-up.
5. Otherwise drain from the head until the requested bytes are satisfied. Pop entries that hit zero. Emit `BandwidthConsumed` with the post-deduct remaining.

The "no mutation on insufficient" property is load-bearing: it means a top-up race is safe — if a message arrives between when an app notices it's short and when the top-up lands, the message stays rejectable rather than half-consuming the subscription.
```

**File:** parachain/runtimes/gargantua/src/ismp.rs (L375-397)
```rust
impl IsmpModule for ProxyModule {
	fn on_accept(&self, request: PostRequest) -> Result<Weight, anyhow::Error> {
		// Bandwidth gate. Always-enforce unless the `no-bandwidth` flag
		// is set; skipped for purchase messages so the recharge flow
		// itself doesn't need bandwidth. With the flag on the gate is a
		// no-op and this block is compiled out entirely.
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

```

**File:** parachain/runtimes/gargantua/src/ismp.rs (L424-441)
```rust
	fn on_response(&self, response: GetResponse) -> Result<Weight, anyhow::Error> {
		// Bandwidth gate. Mirrors the request path in `on_accept`: the chain
		// and module that produced the response pay for the bytes they
		// deliver. Compiled out when the `no-bandwidth` flag is on.
		if response.dest_chain() != HostStateMachine::get() {
			return Ok(Weight::from_parts(0, 0));
		}

		let dest = &response.get.from;

		let pallet_id = ModuleId::from_bytes(dest).map_err(|err| Error::Custom(err.to_string()))?;

		match pallet_id {
			pallet_ismp_demo::PALLET_ID =>
				pallet_ismp_demo::IsmpModuleCallback::<Runtime>::default().on_response(response),
			_ => Err(anyhow!("Destination module not found")),
		}
	}
```

**File:** modules/pallets/state-coprocessor/src/impls.rs (L140-151)
```rust
			let response = GetResponse { get: req, values };

			// Meter the app's bandwidth using the full size of the
			// abi-encoded GetResponse. Charged after proof verification
			// so the value sizes are final.
			let bytes = ismp::abi::encode_get_response(&response).len() as u32;
			<T as Config>::BandwidthGate::try_consume(
				&response.get.source,
				&response.get.from,
				bytes,
			)
			.map_err(|err| Error::Custom(alloc::format!("bandwidth gate: {err}")))?;
```

**File:** modules/ismp/core/src/handlers/response.rs (L91-107)
```rust
			let router = host.ismp_router();
			let cb = router.module_for_id(request.from.clone())?;
			let response = GetResponse { get: request.clone(), values: Default::default() };
			// Re-check the receipt right before dispatch. The up-front pass above
			// runs before any callback executes; a prior response's on_response in
			// this same batch could have stored a receipt for this response
			// (directly or by re-entering the handler), and we must not invoke
			// on_response a second time.
			if host.response_receipt(&response).is_some() {
				Err(Error::DuplicateResponse { meta: (&response).into() })?
			}
			let signer = host.store_response_receipt(&response, &msg.signer)?;
			let res = cb.on_response(GetResponse { get: request.clone(), values }).map(|weight| {
				total_weights.saturating_accrue(weight);
				let commitment = hash_request::<H>(&wrapped_req);
				Event::GetRequestHandled(RequestResponseHandled { commitment, relayer: signer })
			});
```
