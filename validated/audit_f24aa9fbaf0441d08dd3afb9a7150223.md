## Confirmed: `source`/`dest` are both set from the same value

The code confirms the exact bug described. In `modules/ismp/core/src/abi.rs`, the `TryFrom<EvmHostEvents> for crate::events::Event` conversion for both timeout-handled arms decodes only `handled.dest` and then assigns it to *both* fields of `TimeoutHandled`: [1](#0-0) 

```rust
EvmHostEvents::PostRequestTimeoutHandled(handled) => {
    let dest = StateMachine::from_str(&handled.dest).map_err(|e| anyhow!("{}", e))?;
    Ok(crate::events::Event::PostRequestTimeoutHandled(TimeoutHandled {
        commitment: H256(handled.commitment.0),
        dest: dest.clone(),
        source: dest.clone(),   // <-- should be the host's own StateMachine, not dest
    }))
},
EvmHostEvents::GetRequestTimeoutHandled(handled) => {
    let dest = StateMachine::from_str(&handled.dest).map_err(|e| anyhow!("{}", e))?;
    Ok(crate::events::Event::GetRequestTimeoutHandled(TimeoutHandled {
        commitment: H256(handled.commitment.0),
        dest: dest.clone(),
        source: dest.clone(),
    }))
},
```

The `TimeoutHandled` struct explicitly documents `source` and `dest` as distinct fields (source chain vs. destination chain of the message): [2](#0-1) 

Compare this with the correctly-decoded `PostRequestEvent`/`GetRequestEvent` conversions in the same file, where `source` and `dest` are decoded from two separate ABI fields: [3](#0-2) 

This is a clear copy-paste defect: the timeout-handled event, when reconstructed from the on-chain EVM log via this `TryFrom`, always reports `source == dest`, losing the actual originating chain identity.

### What I could not verify given the tool budget
I could not fully trace whether `tesseract` (the relayer/messaging pipeline) or the SDK indexers actually use this specific Rust-side `Event::PostRequestTimeoutHandled`/`Event::GetRequestTimeoutHandled` (reconstructed via `abi.rs`) to make **authorization or dispatch decisions** (e.g., to route a refund/settlement message), as opposed to using it purely for **observability/bookkeeping** (e.g., marking a request as "timeout-processed" for retry suppression). I found references in `tesseract/messaging/messaging/src/events.rs` and `tesseract/messaging/evm/src/lib.rs` and the SDK indexer event handlers (`sdk/packages/indexer/src/handlers/events/evmHost/postRequestTimeoutHandled.event.handler.ts`, `getRequestTimeoutHandled.event.handler.ts`), but did not have iterations left to read their bodies and confirm whether `.source` from this specific decoded event drives any message dispatch/settlement action, or whether the real on-chain timeout handling logic (`modules/ismp/core/src/handlers/timeout.rs`) — which operates on the actual `Request`/`GetRequest` objects and their commitments, not this display `Event` — is the one that authoritatively enforces source/dest binding for state transitions.

Given this, I can confirm the code defect exists exactly as described, but I cannot confirm from the code read so far that it is exploitable to cause **unauthorized fund movement or dispatch on the core protocol path** (the bounty's required impact), since the authoritative timeout/settlement logic appears to operate on strongly-typed request data rather than this reconstructed event. It is likely, based on file naming and usage patterns, that this field is consumed by indexers/relayer bookkeeping (as the question itself frames it) rather than by fund-moving logic directly.

### Title
Corrupted `source` field in `TimeoutHandled` event conversion (dest used for both fields) - (File: `modules/ismp/core/src/abi.rs`)

### Summary
The `TryFrom<EvmHostEvents> for Event` implementation for `PostRequestTimeoutHandled` and `GetRequestTimeoutHandled` sets `TimeoutHandled.source` to the same value as `TimeoutHandled.dest` (both derived from `handled.dest`), instead of using the host chain's own `StateMachine` identity as `source`.

### Finding Description
`TimeoutHandled` is documented to carry distinct source and destination chain identities for a timed-out request/response. The conversion code decodes only the `dest` string from the EVM log and clones it into both `source` and `dest`, so any consumer of this `Event` variant sees `source == dest`, permanently losing the real originating-chain information.

### Impact Explanation
Any downstream consumer that trusts `Event::PostRequestTimeoutHandled(...).source` / `Event::GetRequestTimeoutHandled(...).source` to identify the request's true origin chain (e.g., for indexing, retry bookkeeping, or cross-chain accounting) will be given incorrect data equal to the destination chain. If any relayer/indexer logic keys off this field for routing or state-tracking decisions, it could misattribute the origin chain of a timed-out message.

### Likelihood Explanation
This triggers deterministically on every `PostRequestTimeoutHandled`/`GetRequestTimeoutHandled` EVM event decoded through this path — no attacker input is required beyond a normal timeout being processed, so likelihood of the data corruption itself is certain. Whether it is exploitable for actual unauthorized fund movement depends on unverified downstream consumption logic in `tesseract`/indexer code.

### Recommendation
Fix the conversion to set `source` to the actual originating chain (i.e., the host's own `StateMachine` for a timeout being handled on the source chain, or decode a proper `source` field if available from the emitted Solidity event) rather than cloning `dest`.

### Proof of Concept
Decode any real `PostRequestTimeoutHandled` EVM log through `TryFrom<EvmHostEvents> for Event` and assert:
```rust
let event: Event = EvmHostEvents::PostRequestTimeoutHandled(handled).try_into().unwrap();
if let Event::PostRequestTimeoutHandled(TimeoutHandled { source, dest, .. }) = event {
    assert_eq!(source, dest); // currently always true — bug
    // should instead assert source == actual originating StateMachine, dest == actual destination
}
``` [1](#0-0)

### Citations

**File:** modules/ismp/core/src/abi.rs (L258-273)
```rust
			EvmHostEvents::PostRequestTimeoutHandled(handled) => {
				let dest = StateMachine::from_str(&handled.dest).map_err(|e| anyhow!("{}", e))?;
				Ok(crate::events::Event::PostRequestTimeoutHandled(TimeoutHandled {
					commitment: H256(handled.commitment.0),
					dest: dest.clone(),
					source: dest.clone(),
				}))
			},
			EvmHostEvents::GetRequestTimeoutHandled(handled) => {
				let dest = StateMachine::from_str(&handled.dest).map_err(|e| anyhow!("{}", e))?;
				Ok(crate::events::Event::GetRequestTimeoutHandled(TimeoutHandled {
					commitment: H256(handled.commitment.0),
					dest: dest.clone(),
					source: dest.clone(),
				}))
			},
```

**File:** modules/ismp/core/src/abi.rs (L294-308)
```rust
impl TryFrom<PostRequestEvent> for router::PostRequest {
	type Error = anyhow::Error;

	fn try_from(post: PostRequestEvent) -> Result<Self, Self::Error> {
		Ok(router::PostRequest {
			source: StateMachine::from_str(&post.source).map_err(|e| anyhow!("{}", e))?,
			dest: StateMachine::from_str(&post.dest).map_err(|e| anyhow!("{}", e))?,
			nonce: post.nonce.try_into().map_err(|e| anyhow!("{e}"))?,
			from: post.from.0.to_vec(),
			to: post.to.0.to_vec(),
			timeout_timestamp: post.timeoutTimestamp.try_into().map_err(|e| anyhow!("{e}"))?,
			body: post.body.to_vec(),
		})
	}
}
```

**File:** modules/ismp/core/src/events.rs (L91-100)
```rust
pub struct TimeoutHandled {
	/// The commitment to the request or response
	pub commitment: H256,
	/// The source chain of the message
	#[serde(with = "serde_hex_utils::as_string")]
	pub source: StateMachine,
	/// The destination chain of the message
	#[serde(with = "serde_hex_utils::as_string")]
	pub dest: StateMachine,
}
```
