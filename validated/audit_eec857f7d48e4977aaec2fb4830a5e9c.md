Based on the evidence gathered, the closest verifiable Hyperbridge analog to the Rubicon "interacting with a liquidated position locks funds" bug is `EvmHost.fundRequest()` (and its Substrate counterpart `fund_message`), which accept funds for a commitment without checking whether that commitment has already been delivered/finalized.

### Title
`fundRequest`/`fund_message` accept and permanently strand funds for already-delivered ISMP requests - (File: `evm/src/core/EvmHost.sol`)

### Summary
`fundRequest` only checks that a `FeeMetadata` entry exists for the commitment (`metadata.sender == address(0)` → `revert UnknownRequest()`); it never checks whether the request has already been delivered (`_requestReceipts[commitment]` on the destination side) or, for GET requests, whether the response has already arrived (`_responseReceipts[commitment]`) on the source side. [1](#0-0) 

### Finding Description
`_requestCommitments[commitment]` is written when a POST/GET request is dispatched, and is only ever deleted on a timeout path (`dispatchTimeOut`). On the success path, `dispatchIncoming(GetResponse ...)` pays the stored fee to the relayer but never deletes or zeroes `_requestCommitments[commitment]`, so the entry (and its `sender`) persists indefinitely after the request has already been fully serviced. [2](#0-1) 

Because `fundRequest` only checks `metadata.sender != address(0)`, it will happily accept new funds for a commitment whose request has already been delivered and paid out. The docstring itself concedes this: "If called on an already delivered request, these funds will be seen as a donation to the hyperbridge protocol." [3](#0-2) 

This is structurally identical to the Rubicon `Position` bug: a public entrypoint (`increaseMargin` / `fundRequest`) that operates on a per-commitment/per-position record without verifying the record's lifecycle has already terminated (liquidated / delivered). In both cases the guard that exists elsewhere in the same contract for "is this already finished" (e.g. `_filled[commitment]` checks in `IntentGatewayV2`/`IntentsBase` reverting with `Filled()`) is simply absent from this function, so a caller's funds enter a state controlled by the contract with no code path to return them. [4](#0-3) 

The Substrate equivalent, `fund_message`, has the same shape: it looks up `RequestCommitments`/`ResponseCommitments` purely by key existence, with no check that a response/timeout has already retired the message. [5](#0-4) 

### Impact Explanation
Any unprivileged user who calls `fundRequest`/`fund_message` on a commitment that has already been delivered loses the supplied `feeToken`/native amount permanently — there is no sweep-to-user or refund mechanism for this "donation," only a general note that it becomes protocol dust. This is a genuine, unrecoverable loss of user funds through a normal, unauthenticated entrypoint, matching the bounty's "stealing or loss of funds" category. It does not require a malicious relayer, prover, or admin — only an ordinary user calling the fee top-up function slightly too late (e.g. racing a relayer's delivery, or fat-fingering a stale commitment).

### Likelihood Explanation
This is highly reachable: `fundRequest` is a documented, intended-for-use public function (advertised in `docs/content/developers/evm/messaging/post-requests.mdx` as the way to bump relayer incentives on gas spikes). Any user monitoring an in-flight request has a realistic window to call it just as/after a relayer delivers, especially under network congestion when delivery and fee-bump transactions race. No proof forgery or privileged role is needed — it's a plain state-check omission. [6](#0-5) 

### Recommendation
Add an explicit finalization check to `fundRequest`/`fund_message` before accepting the top-up: reject (or refund inline) if `_requestReceipts[commitment] != address(0)` (POST delivered) or `_responseReceipts[commitment].relayer != address(0)` (GET responded), mirroring the `Filled()`-style guards already used in `IntentsBase`/`IntentGatewayV2` for order commitments. This closes the same class of bug the Rubicon report flagged: allow interaction only while the underlying object is still "live."

### Proof of Concept
1. User A dispatches a GET request via `IDispatcher.dispatch(DispatchGet)`, paying an initial fee; `_requestCommitments[commitment]` is set. [7](#0-6) 
2. A relayer submits the response; `EvmHost.dispatchIncoming(GetResponse, relayer)` runs, pays out `_requestCommitments[commitment].fee` to the relayer, but leaves `_requestCommitments[commitment]` (with non-zero `sender`) in storage. [8](#0-7) 
3. User A (unaware delivery already completed, e.g. due to indexer lag) calls `fundRequest(commitment, amount)` with fresh `feeToken`. The check `metadata.sender == address(0)` passes (sender is still set from step 1), so the transfer succeeds and `amount` is pulled from User A with no recipient ever able to claim it. [1](#0-0) 
4. `amount` is now permanently stuck in the `EvmHost` contract, unrecoverable by User A — the same "interact with an already-finalized object → funds locked forever" pattern as the Rubicon `Position.increaseMargin` bug.

### Citations

**File:** evm/src/core/EvmHost.sol (L820-847)
```text
    /**
     * @dev Dispatch an incoming GET response to source module
     * @param response - get response
     */
    function dispatchIncoming(GetResponse memory response, address relayer) external restrict(_hostParams.handler) {
        // replay protection
        bytes32 commitment = response.request.hash();
        _responseReceipts[commitment] = ResponseReceipt({
            relayer: relayer,
            responseCommitment: response.hash()
        });

        (bool success,) = _bytesToAddress(response.request.from)
            .call(abi.encodeWithSelector(IApp.onGetResponse.selector, IncomingGetResponse(response, relayer)));

        if (!success) {
            // so that it can be retried
            delete _responseReceipts[commitment];
            return;
        }

        // reward the relayer fee
        uint256 fee = _requestCommitments[commitment].fee;
        if (fee != 0) {
            IERC20(feeToken()).safeTransfer(relayer, fee);
        }
        emit GetRequestHandled({commitment: commitment, relayer: relayer});
    }
```

**File:** evm/src/core/EvmHost.sol (L974-1013)
```text
    function dispatch(DispatchGet memory get) external payable notFrozen returns (bytes32 commitment) {
        if (msg.value > 0) {
            address[] memory path = new address[](2);
            address uniswapV2 = _hostParams.uniswapV2;
            path[0] = IUniswapV2Router02(uniswapV2).WETH();
            path[1] = feeToken();
            IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msg.value}(
                get.fee, path, address(this), block.timestamp
            );
        } else if (get.fee > 0) {
            IERC20(feeToken()).safeTransferFrom(_msgSender(), address(this), get.fee);
        }

        uint64 timeoutTimestamp = get.timeout == 0 ? 0 : uint64(block.timestamp) + uint64(get.timeout);
        GetRequest memory request = GetRequest({
            source: host(),
            dest: get.dest,
            nonce: uint64(_nextNonce()),
            from: abi.encodePacked(_msgSender()),
            timeoutTimestamp: timeoutTimestamp,
            keys: get.keys,
            height: get.height,
            context: get.context
        });

        // make the commitment
        commitment = request.hash();
        _requestCommitments[commitment] = FeeMetadata({sender: _msgSender(), fee: get.fee});
        emit GetRequestEvent({
            source: string(request.source),
            dest: string(request.dest),
            from: request.from,
            keys: request.keys,
            nonce: request.nonce,
            height: request.height,
            context: request.context,
            timeoutTimestamp: request.timeoutTimestamp,
            fee: get.fee
        });
    }
```

**File:** evm/src/core/EvmHost.sol (L1015-1051)
```text
    /**
     * @dev Increase the relayer fee for a previously dispatched request.
     * This is provided for use only on pending requests, such that when they timeout,
     * the user can recover the entire relayer fee.
     *
     * @notice Payment can be made with either the native token or the feeToken.
     * If native tokens are supplied, it will perform a swap under the hood using the local uniswap router.
     * Will revert if enough native tokens are not provided.
     *
     * If no native tokens are provided then it will try to collect payment from the calling contract in
     * the feeToken.
     *
     * If called on an already delivered request, these funds will be seen as a donation to the hyperbridge protocol.
     * @param commitment - The request commitment
     * @param amount - The amount provided in `feeToken()`
     */
    function fundRequest(bytes32 commitment, uint256 amount) external payable notFrozen {
        if (msg.value > 0) {
            address[] memory path = new address[](2);
            address uniswapV2 = _hostParams.uniswapV2;
            path[0] = IUniswapV2Router02(uniswapV2).WETH();
            path[1] = feeToken();
            IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msg.value}(
                amount, path, address(this), block.timestamp
            );
        } else {
            IERC20(feeToken()).safeTransferFrom(_msgSender(), address(this), amount);
        }

        FeeMetadata memory metadata = _requestCommitments[commitment];
        if (metadata.sender == address(0)) revert UnknownRequest();

        metadata.fee += amount;
        _requestCommitments[commitment] = metadata;

        emit RequestFunded({commitment: commitment, newFee: metadata.fee});
    }
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L390-410)
```text
    function _withdraw(WithdrawalRequest memory body, bool isRefund, bool finalize) internal {
        address beneficiary = address(uint160(uint256(body.beneficiary)));
        if (finalize) _filled[body.commitment] = beneficiary;

        uint256 len = body.tokens.length;
        for (uint256 i; i < len; i++) {
            address token = address(uint160(uint256(body.tokens[i].token)));
            uint256 amount = body.tokens[i].amount;
            if (amount == 0) continue;

            uint256 escrowed = _orders[body.commitment][token];
            if (escrowed == 0) revert UnknownOrder();

            _orders[body.commitment][token] = escrowed - amount;
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
            }
        }
```

**File:** modules/pallets/ismp/src/lib.rs (L442-480)
```rust
		/// those funds will be lost forever.
		#[pallet::weight(<T as frame_system::Config>::DbWeight::get().writes(5))]
		#[pallet::call_index(4)]
		pub fn fund_message(
			origin: OriginFor<T>,
			message: FundMessageParams<T::Balance>,
		) -> DispatchResult {
			let account = ensure_signed(origin)?;

			let metadata = match message.commitment {
				MessageCommitment::Request(commitment) => RequestCommitments::<T>::get(commitment),
				MessageCommitment::Response(commitment) =>
					ResponseCommitments::<T>::get(commitment),
			};

			let Some(mut metadata) = metadata else {
				return Err(Error::<T>::MessageNotFound.into());
			};

			T::Currency::transfer(
				&account,
				&RELAYER_FEE_ACCOUNT.into_account_truncating(),
				message.amount,
				Preservation::Expendable,
			)?;

			match message.commitment {
				MessageCommitment::Request(commiment) => {
					metadata.fee.fee += message.amount;
					RequestCommitments::<T>::insert(commiment, metadata);
				},
				MessageCommitment::Response(commiment) => {
					metadata.fee.fee += message.amount;
					ResponseCommitments::<T>::insert(commiment, metadata);
				},
			};

			Ok(())
		}
```

**File:** docs/content/developers/evm/messaging/post-requests.mdx (L252-275)
```text
### Increasing Fees for In-Flight Requests

If gas prices spike after dispatching a request, you can increase the relayer fee to incentivize delivery:

```solidity lineNumbers
// Get the request commitment from the dispatch call
bytes32 commitment = IDispatcher(host).dispatch{value: nativeCost}(post);

// Later, if gas prices spike...
uint256 additionalFee = 100e18; // Additional relayer incentive

// Fund with native token
IDispatcher(host).fundRequest{value: additionalNative}(
    commitment,
    additionalFee
);

// Or fund with feeToken
IERC20(feeToken).approve(host, additionalFee);
IDispatcher(host).fundRequest(commitment, additionalFee);
```

Note that `fundRequest` increases the **relayer fee**. The additional fee goes to the `payer` address if the request times out. This function can be called multiple times to incrementally increase fees and only works for pending requests.

```
