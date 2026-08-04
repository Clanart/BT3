Confirmed: `HostManager.sol`'s `onAccept` never references `request.from` at all — grep found zero matches for `from` in that file. The only identity check is against `request.source` (the source *chain*), matching the SPF/DMARC pattern exactly: the "envelope domain" is checked but the specific "sender mailbox" (module id) is not.

### Title
Cross-chain governance messages to `HostManager` are authenticated only by source chain, not by sender module — allows spoofed withdraw/param-update actions - ([File: evm/src/core/HostManager.sol])

### Summary
`HostManager.onAccept` gates privileged actions (`Withdraw`, `SetHostParam`) solely on `request.source` equaling the configured Hyperbridge state machine. It never checks `request.from`, the field that identifies *which* module/pallet on Hyperbridge actually dispatched the message. Any pallet on the Hyperbridge chain that can call `IsmpDispatcher::dispatch_request` with an attacker/user-supplied `from` and a `to` pointed at a destination `HostManager` contract can therefore impersonate the intended privileged sender (e.g. `host-executive`) and trigger `Withdraw`/`SetHostParam` on any connected EVM chain.

### Finding Description
`HostManager.onAccept` performs exactly one authentication check: [1](#0-0) 
```
function onAccept(IncomingPostRequest calldata incoming) external override restrict(_params.host) {
    PostRequest calldata request = incoming.request;
    // Only the Hyperbridge parachain can send requests to this module.
    if (!request.source.equals(IHost(_params.host).hyperbridge())) revert UnauthorizedAction();
```
It never inspects `request.from` — the field the protocol documents as "the initiating module identifier on the source chain" [2](#0-1) . `request.source` is set automatically by every dispatcher to `host.host_state_machine()` regardless of which pallet/account initiated the call [3](#0-2) , so `source` is a chain-level fact anyone dispatching from Hyperbridge automatically satisfies. Authorization to invoke governance (`Withdraw`/`SetHostParam`) should instead be bound to a specific privileged `from` module id (e.g. host-executive's module id), exactly the way SPF alone (checking only the sending domain) is insufficient without DMARC/sender-identity alignment.

The `DispatchPost`/`DispatchGet` dispatch surface documented for pallets explicitly takes `from: Vec<u8>` as a value the calling code controls and can set to whatever bytes it wants [4](#0-3) ; the reference "send_message" pattern shown in the docs takes the entire `DispatchPost` (including `from`) directly from a signed extrinsic's caller-supplied parameter [5](#0-4) . This is the intended extension pattern for third-party pallets built against `IsmpDispatcher`. Since `HostManager` on the EVM side never validates `from`, any pallet exposing this generic dispatch pattern (or any future one) lets its callers pick an arbitrary `from`, including bytes matching host-executive's module id, and target `to = HostManager` on any connected EVM chain.

### Impact Explanation
If reachable, this allows unauthorized execution of privileged host-management actions — draining accumulated protocol/bridge revenue via the `Withdraw` action to an attacker-chosen `beneficiary`, or rewriting `HostParams` (admin, hostManager, consensusClient, challengePeriod, feeToken, etc.) via `SetHostParam`, which can be leveraged to fully compromise the EVM host's trust assumptions (e.g., swapping the consensus client or admin) [6](#0-5) . This directly matches the bounty's "unauthorized transaction or execution" and "stealing or loss of funds" categories, and the pivot explicitly calling out that "cross-chain admin or host-management effects must not be reachable through malformed proofs, wrong module bindings, or unauthenticated message flow."

### Likelihood Explanation
Exploitability depends entirely on whether any deployed Hyperbridge pallet exposes a `DispatchPost`-style extrinsic where an unprivileged, signed caller controls the `from` field verbatim (the documented "send_message" pattern). In the pallets I could inspect in this snapshot (`pallet_ismp_demo`, `host-executive`, `intents-coprocessor`, `token-governor`, `relayer`), `from` is hard-coded to each pallet's own fixed module id rather than taken from caller input, so the currently-shipped runtime pallets do not appear to hand an attacker direct control over `from`. I was not able to fully audit every pallet/runtime configuration for a generic "raw dispatch" extrinsic that mirrors the documented pattern, so I cannot confirm with certainty that such an entrypoint exists in production today. Given this uncertainty, likelihood should be treated as contingent on the existence of any single such generic-dispatch extrinsic — the underlying `HostManager` defect itself is confirmed and unconditional.

### Recommendation
`HostManager.onAccept` should authenticate on `request.from` (the specific privileged module id, e.g. `host-executive`'s module id) in addition to `request.source`, mirroring the DMARC-style requirement that both the "envelope domain" and the specific "from identity" align before a privileged action is honored. This closes the gap regardless of whether a user-facing generic dispatch entrypoint exists now or is added later.

### Proof of Concept
1. Identify (or, if one does not yet exist, note that adding) any Hyperbridge pallet extrinsic that lets a signed caller supply an arbitrary `DispatchPost { from, to, body, .. }` to `IsmpDispatcher::dispatch_request`, per the documented pattern [5](#0-4) .
2. Call it with `from = <host-executive's module id>`, `to = <HostManager address on target EVM chain>`, `dest = StateMachine::Evm(<chain id>)`, `body = [0x00] ++ abi.encode(WithdrawParams{ beneficiary: attacker, amount, token })` (action byte `0` = `Withdraw`) [7](#0-6) .
3. `Ismp::dispatch_request` sets `request.source = host.host_state_machine()` automatically [8](#0-7) , satisfying `HostManager`'s only check.
4. On delivery to the EVM chain, `HandlerV2.handlePostRequests` verifies the state/consensus proof (legitimately, since the request really was included in Hyperbridge state) and calls `host.dispatchIncoming` → `HostManager.onAccept`, which passes the `source` check and executes `IHostManager(_params.host).withdraw(withdrawParams)`, sending `amount` of `token` to the attacker-chosen `beneficiary` [9](#0-8) , [10](#0-9) .

### Citations

**File:** evm/src/core/HostManager.sol (L95-108)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override restrict(_params.host) {
        PostRequest calldata request = incoming.request;
        // Only the Hyperbridge parachain can send requests to this module.
        if (!request.source.equals(IHost(_params.host).hyperbridge())) revert UnauthorizedAction();

        OnAcceptActions action = OnAcceptActions(uint8(request.body[0]));
        if (action == OnAcceptActions.Withdraw) {
            // This is where governance & relayers can withdraw their revenue.
            WithdrawParams memory withdrawParams = abi.decode(request.body[1:], (WithdrawParams));
            IHostManager(_params.host).withdraw(withdrawParams);
        } else if (action == OnAcceptActions.SetHostParam) {
            HostParams memory hostParams = abi.decode(request.body[1:], (HostParams));
            IHostManager(_params.host).updateHostParams(hostParams);
        }
```

**File:** docs/content/developers/polkadot/dispatching.mdx (L15-22)
```text
```rust showLineNumbers
pub struct DispatchPost {
    pub dest: StateMachine,
    pub from: Vec<u8>,
    pub to: Vec<u8>,
    pub timeout: u64,
    pub body: Vec<u8>,
}
```

**File:** docs/content/developers/polkadot/dispatching.mdx (L49-52)
```text
| `dest` | Destination chain, for this you'll use the `StateMachine` enum eg `StateMachine::Evm(1)` for Ethereum Mainnet. |
| `from` | The initiaing module identifier on the source chain. |
| `to` | Receiving module/contract address on the destination chain. |
| `body` | Serialized byte representation of the message (to be decoded by the receiving contract). |
```

**File:** docs/content/developers/polkadot/dispatching.mdx (L57-76)
```text
```rust showLineNumbers
#[pallet::weight(T::dispatch())]
#[pallet::call_index(0)]
pub fn send_message(
    origin: OriginFor<T>,
    post: DispatchPost,
    fee: T::Balance,
) -> DispatchResultWithPostInfo {
    let signer = ensure_signed(origin)?;
    let dispatcher = pallet_ismp::Pallet::<Runtime>::default();
    let commitment = dispatcher.dispatch_request(
        DispatchRequest::Post(post),
        FeeMetadata {
            payer: signer,
            fee,
        }
    )?;

    Ok(())
}
```

**File:** modules/pallets/ismp/src/dispatcher.rs (L128-145)
```rust
			DispatchRequest::Post(dispatch_post) => {
				let post = PostRequest {
					source: self.host_state_machine(),
					dest: dispatch_post.dest,
					nonce: self.next_nonce(),
					from: dispatch_post.from,
					to: dispatch_post.to,
					timeout_timestamp: if dispatch_post.timeout == 0 {
						0
					} else {
						<T::TimestampProvider as UnixTime>::now()
							.as_secs()
							.saturating_add(dispatch_post.timeout)
					},
					body: dispatch_post.body,
				};
				Request::Post(post)
			},
```

**File:** evm/tests/rust/src/tests/host_manager.rs (L72-109)
```rust
#[test]
fn test_host_manager_withdraw() {
	let mut env = TestEnv::new();
	let manager = host_manager_of(&mut env);

	// Mint 1000e18 fee tokens to the host
	let amount_to_mint = U256::from(1000u128) * U256::from(10u128.pow(18));
	env.call(env.fee_token, mintCall { to: env.host, amount: amount_to_mint }.abi_encode());
	assert_eq!(host_balance(&mut env), amount_to_mint);

	// Build a withdraw request (body = [0] + abi.encode(WithdrawParams)).
	// Withdraw the fee token (non-zero `token`) — the zero address would be
	// the native-ETH path which this test isn't exercising.
	let params = WithdrawalParams {
		beneficiary_address: H160::random().as_bytes().to_vec(),
		amount: SubstrateU256::from(500_000_000_000_000_000_000u128),
		token: H160::from_slice(env.fee_token.as_slice()),
	};

	let post = router::PostRequest {
		source: StateMachine::Kusama(2000),
		dest: StateMachine::Evm(1),
		nonce: 0,
		from: env.sender.as_slice().to_vec(),
		to: vec![],
		timeout_timestamp: 100,
		body: params.abi_encode().expect("20-byte beneficiary"),
	};
	let evm_request: EvmPostRequest = post.into();

	// HostManager.onAccept is `restrict(_params.host)` — must call AS the host
	let host_addr = env.host;
	let calldata = onaccept_calldata(evm_request, env.sender);
	env.call_as(host_addr, manager, calldata);

	let withdraw_amount = U256::from(500u128) * U256::from(10u128.pow(18));
	assert_eq!(host_balance(&mut env), amount_to_mint - withdraw_amount);
}
```
