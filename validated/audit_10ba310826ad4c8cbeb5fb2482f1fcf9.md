### Title
Missing Bandwidth Gate Enforcement in `ProxyModule::on_response` Allows Unmetered GetResponse Delivery - (File: `parachain/runtimes/gargantua/src/ismp.rs`, `parachain/runtimes/nexus/src/ismp.rs`)

### Summary
This is the local analog of the `sellForLP` bug: a guard (`isCrossChainAllowed` there, the bandwidth gate here) exists and is enforced on one code path (`on_accept`) but is documented as applying to a sibling path (`on_response`) while the actual enforcement call is absent, letting cross-chain traffic bypass the accounting/allowance check entirely on that path.

### Finding Description
In both the `gargantua` and `nexus` runtime `ProxyModule` implementations, `IsmpModule::on_accept` explicitly meters incoming `PostRequest` bytes against the sender's bandwidth allowance before dispatch: [1](#0-0) 

`IsmpModule::on_response`, which handles incoming `GetResponse` delivery, carries a comment claiming it "mirrors the request path in `on_accept`" for bandwidth gating, but the function body only checks the destination chain and then dispatches straight to the destination module — there is no call to `pallet_bandwidth::BandwidthGate::try_consume` (or any equivalent) anywhere in the function: [2](#0-1) 

The same pattern (no bandwidth consumption call in `on_response`) exists in the `nexus` runtime: [3](#0-2) 

The core `ismp` response handler (`modules/ismp/core/src/handlers/response.rs`) performs proof verification, timeout checks, and duplicate-response checks, but it too contains no bandwidth accounting logic — that responsibility is delegated entirely to the runtime's `IsmpModule::on_response` callback, exactly as `on_accept` delegates request-side bandwidth accounting to the same trait implementation: [4](#0-3) 

Because the `try_consume` call is only wired into `on_accept`, any module or relayer that can drive `GetRequest`/`GetResponse` traffic to this chain gets response payloads delivered to destination modules for free, while equivalently-sized `PostRequest` payloads are charged against the source/destination pair's bandwidth balance. This is structurally identical to `sellForLP` omitting the `isCrossChainAllowed` modifier that `buyForLP`/other functions in the same contract correctly apply — a guard that exists in the codebase and is asserted (by comment/intent) to protect a specific execution path, but is not actually invoked there.

### Impact Explanation
Bandwidth accounting is one of the explicitly protected invariants for this bounty ("bandwidth balances must move exactly once and only to the rightful beneficiary and amount"). With the gate absent from `on_response`, an attacker can drive unbounded `GetRequest`/`GetResponse` cross-chain traffic through the coprocessor to any destination module without depleting or being blocked by the configured bandwidth allowance for the (source, module) pair — bypassing the resource-accounting/rate-limiting mechanism that the request path enforces. This lets an unprivileged actor consume the chain's bandwidth-gated processing capacity for a class of messages that was supposed to be metered identically to POST requests, effectively defeating the purpose of the allowance system for one whole leg of ISMP traffic.

### Likelihood Explanation
High: `GetRequest`/`GetResponse` is a standard, public, unprivileged part of the ISMP flow (see `handle_get_requests` in `modules/pallets/state-coprocessor/src/impls.rs`), and any user or contract able to dispatch a `DispatchGet` (as used pervasively, e.g. by `IntentGatewayV2` cancel flows) can trigger `on_response` callbacks on the destination chain. No relayer collusion, malicious prover, or governance action is required — this is reachable purely through the public dispatch-request → relayer-submits-proof → `on_response` callback pipeline that already exists for legitimate get-response delivery.

### Recommendation
Add the same `pallet_bandwidth::BandwidthGate::try_consume` (or equivalent) call inside `ProxyModule::on_response` in both `gargantua` and `nexus` runtimes, keyed on the response's `source`/`from` (mirroring `request.source`/`request.from` in `on_accept`), sized by the encoded `GetResponse` payload, before dispatching to the destination module — consistent with the comment's stated intent.

### Proof of Concept
1. Deploy/observe a module with a limited bandwidth allowance configured via `pallet_bandwidth` for a given (source chain, module) pair.
2. From that source chain, repeatedly dispatch `GetRequest`s targeting storage on this chain (any public flow that produces `DispatchGet`, e.g. `IntentGatewayV2.cancelOrder`'s cross-chain source branch, works).
3. Have a relayer submit the resulting `GetResponse`s with valid storage proofs through `handle_get_requests` / the response handler.
4. Observe that `ProxyModule::on_response` (`parachain/runtimes/gargantua/src/ismp.rs:424-441` / `parachain/runtimes/nexus/src/ismp.rs:412-418`) delivers every response to the destination module regardless of volume, with no corresponding decrement of the module's bandwidth balance, unlike equivalent-sized `PostRequest` traffic which is rejected once the allowance in `on_accept`'s `try_consume` call is exhausted.

### Citations

**File:** parachain/runtimes/gargantua/src/ismp.rs (L377-396)
```rust
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

**File:** parachain/runtimes/nexus/src/ismp.rs (L412-418)
```rust
	fn on_response(&self, response: GetResponse) -> Result<Weight, anyhow::Error> {
		if response.dest_chain() != HostStateMachine::get() {
			return Ok(Weight::from_parts(0, 0));
		}

		Err(anyhow!("Destination module not found"))
	}
```

**File:** modules/ismp/core/src/handlers/response.rs (L78-107)
```rust
	let result = msg
		.requests
		.iter()
		.cloned()
		.map(|request| {
			let wrapped_req = Request::Get(request.clone());
			let keys = request.keys.clone();
			let values = state_machine
				.verify_state_proof(host, keys, state.state_root, &proof)?
				.into_iter()
				.map(|(key, value)| StorageValue { key, value })
				.collect();

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
