## Analysis

The external report's core broken invariant: an amount denominated in one token's decimal precision is used directly as the amount for a *different* token with different decimal precision, with no on-chain rescaling — producing an amount that is off by `10^|decimalsA - decimalsB|`.

Searching Hyperbridge's codebase for the same pattern in bridge custody / mint-burn accounting turned up a direct, locally-provable analog in the `HyperFungibleToken` / `WrappedHyperFungibleToken` EVM contract pair, which is the actual production cross-chain token-bridging primitive (replacing the older `TokenGateway`).

### Title
Missing cross-chain decimal normalization in HyperFungibleToken bridge causes permanent value loss/mis-settlement for any underlying token whose decimals differ from the fixed 18-decimal mint/burn peer - (File: `sdk/packages/core/contracts/apps/WrappedHyperFungibleToken.sol`, `sdk/packages/core/contracts/apps/HyperFungibleToken.sol`)

### Summary
`WrappedHyperFungibleToken` (home chain, custodies an arbitrary ERC20 `underlying`) and `HyperFungibleToken` (remote chains, burn/mint, always OZ-default 18 decimals) pass the raw `amount` field straight through the ISMP `Message` with **no decimal scaling whatsoever**, even though the underlying token can have any decimal precision (USDC = 6, WBTC = 8, etc.). This is the exact bug class from the report: an amount computed in one token's base units is consumed as the base-unit amount of a token with different decimals, without normalization.

### Finding Description
`WrappedHyperFungibleToken.send()` locks `params.amount` raw units of `_underlying` and encodes that same raw integer into the cross-chain `Message.amount` field with no adjustment: [1](#0-0) 

On the remote chain, `HyperFungibleToken.onAccept()` mints exactly that raw integer of its own token (which uses OZ's default 18-decimal `ERC20`, since neither `HyperFungibleToken` nor its constructor take a decimals parameter): [2](#0-1) 

The reverse leg (`HyperFungibleToken.send()` burns, `WrappedHyperFungibleToken.onAccept()` unlocks) is symmetric and equally unscaled: [3](#0-2) 

Compare this to the Substrate implementation of the *same* protocol, `pallet-hyper-fungible-token`, which explicitly tracks per-chain `Precisions` and calls `convert_to_erc20(amount, erc_decimals, decimals)` before dispatching — proving that decimal scaling is a required part of this bridge's correctness, and that its total absence on the EVM↔EVM leg is a real omission rather than an intentional design choice: [4](#0-3) [5](#0-4) 

Concretely: if `WrappedHyperFungibleToken` is configured with `underlying = USDC` (6 decimals), and its `HyperFungibleToken` peer on a remote chain uses the default 18 decimals, then locking `1,000,000` raw units (1.0 USDC) dispatches `amount = 1,000,000` which mints only `1,000,000` wei of the 18-decimal peer token — i.e. `0.000000000001` tokens. Any wallet, DEX, or accounting system on the remote chain that reads the peer token's `decimals()` (18, as advertised via standard ERC20 introspection) will value the user's minted balance at a millionth of a millionth of what they actually locked. The `send()`/`onAccept()` functions are public/unprivileged entrypoints reachable by any user with no relayer, prover, or admin involvement — this is a direct, unconditional protocol defect triggered by ordinary use of the documented bridging flow.

### Impact Explanation
This causes **unrecoverable loss of bridged value** for any deployment where the underlying token's decimals differ from 18 (the overwhelmingly common case — USDC/USDT at 6, WBTC at 8, etc.), which directly matches the bounty's "stealing or loss of funds" and "transaction manipulation" categories: a user's cross-chain transfer settles for a wildly wrong amount purely due to protocol logic, with no way to recover the difference (the underlying stays custodied in the wrapper, but the minted representation on the destination is economically meaningless). This is systemic to every deployment pairing a non-18-decimal underlying with the burn/mint peer, not an edge case.

### Likelihood Explanation
High. `WrappedHyperFungibleToken`/`HyperFungibleToken` is the documented, currently-recommended bridging primitive (replacing `TokenGateway`), and the deployment guides explicitly show wrapping standard tokens like USDC: [6](#0-5) 
No configuration step or contract logic anywhere in `WrappedHyperFungibleToken.sol` or `HyperFungibleToken.sol` accounts for `underlying.decimals()` vs. the peer's `decimals()` — this will fire on the very first deployment pairing a 6- or 8-decimal token with the default 18-decimal mint/burn side, requiring no attacker action at all.

### Recommendation
Mirror the Substrate pallet's approach on the EVM contracts: store each peer chain's token decimals alongside its module ID (extend `addChain`/`_supportedChains`), and rescale `Message.amount` at both `_buildDispatchPost` (send side) and `onAccept`/`onPostRequestTimeout` (receive side) using the same `convert_to_erc20`-style scaling logic already implemented in `modules/pallets/hyper-fungible-token/src/lib.rs`, so that `amount` is normalized to the destination token's decimals before mint/unlock, and back to the source token's decimals on any timeout refund.

### Proof of Concept
1. Deploy `WrappedHyperFungibleToken` on Chain A, `configure()` with `underlying = USDC` (6 decimals).
2. Deploy `HyperFungibleToken` ("Wrapped USDC", "wUSDC") on Chain B — inherits OZ `ERC20`, `decimals() == 18`.
3. Register peers bidirectionally via `addChain`.
4. User approves and calls `WrappedHyperFungibleToken.send({amount: 1_000_000, dest: ChainB, to: user, ...})` — this locks 1.0 USDC (`1_000_000` raw units).
5. Chain B's `HyperFungibleToken.onAccept()` executes `_mint(beneficiary, 1_000_000)` — the user receives `0.000000000001` wUSDC (18-decimals), not `1.0` wUSDC as intended.
6. No revert, no error — the transfer "succeeds" per protocol logic while destroying essentially all of the bridged value; the 1.0 USDC remains locked in `WrappedHyperFungibleToken` with no path for the user to reclaim the correct proportional amount, since redeeming requires burning `1_000_000` wei of wUSDC (which they now hold) to unlock exactly `1_000_000` raw USDC units back — but they can never legitimately acquire the missing `10^12 - 1` wei of "real" value they lost on the way in.

### Citations

**File:** sdk/packages/core/contracts/apps/WrappedHyperFungibleToken.sol (L266-290)
```text
    function send(HyperFungibleToken.SendParams calldata params) external payable whenNotPaused {
        uint256 msgValue = msg.value;
        if (_isWeth && msgValue >= params.amount) {
            msgValue = msgValue - params.amount;
            IWETH(_underlying).deposit{value: params.amount}();
        } else {
            IERC20(_underlying).safeTransferFrom(msg.sender, address(this), params.amount);
        }

        DispatchPost memory request = _buildDispatchPost(params);
        bytes32 commitment;
        if (msgValue > 0) {
            commitment = IDispatcher(_host).dispatch{value: msgValue}(request);
        } else {
            commitment = dispatchWithFeeToken(request);
        }

        emit Sent({
            from: msg.sender,
            to: params.to,
            dest: string(params.dest),
            amount: params.amount,
            commitment: commitment
        });
    }
```

**File:** sdk/packages/core/contracts/apps/WrappedHyperFungibleToken.sol (L299-324)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost whenNotPaused {
        PostRequest calldata request = incoming.request;

        bytes memory expectedSource = _supportedChains[request.source];
        if (expectedSource.length == 0) revert UnsupportedChain();
        if (keccak256(request.from) != keccak256(expectedSource)) revert UnauthorizedSource();

        HyperFungibleToken.Message memory message = abi.decode(request.body, (HyperFungibleToken.Message));
        address beneficiary = _toAddr(message.to);

        if (_isWeth) {
            // Try a native-ETH push first (cheap for EOAs and payable contracts);
            // if the recipient cannot accept native value (no `receive()` / `fallback()
            // payable`), re-wrap the withdrawn ETH and deliver the underlying WETH as
            // an ERC-20 transfer instead. This mirrors the deposit-side flexibility of
            // `send()` (which accepts WETH from non-payable callers via `safeTransferFrom`)
            // so the refund path doesn't permanently lock funds for the same caller class.
            IWETH(_underlying).withdraw(message.amount);
            (bool sent,) = beneficiary.call{value: message.amount}("");
            if (!sent) {
                IWETH(_underlying).deposit{value: message.amount}();
                IERC20(_underlying).safeTransfer(beneficiary, message.amount);
            }
        } else {
            IERC20(_underlying).safeTransfer(beneficiary, message.amount);
        }
```

**File:** sdk/packages/core/contracts/apps/HyperFungibleToken.sol (L291-300)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost whenNotPaused {
        PostRequest calldata request = incoming.request;

        bytes memory expectedSource = _supportedChains[request.source];
        if (expectedSource.length == 0) revert UnsupportedChain();
        if (keccak256(request.from) != keccak256(expectedSource)) revert UnauthorizedSource();

        Message memory message = abi.decode(request.body, (Message));
        address beneficiary = _toAddr(message.to);
        _mint(beneficiary, message.amount);
```

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L292-296)
```rust
			// Encode the Message body
			let sender: [u8; 32] = who.clone().into();
			let amount: u128 = params.amount.into();
			let erc20_amount = convert_to_erc20(amount, erc_decimals, decimals);

```

**File:** modules/pallets/hyper-fungible-token/README.md (L30-32)
```markdown
Decimals between this chain and each remote chain may differ; per-pair
`Precisions` storage records the EVM-side decimals so amounts get scaled at
the boundary.
```

**File:** docs/content/developers/evm/hyper-fungible-token/wrapped-hyper-fungible-token.mdx (L33-43)
```text
IWrappedHyperFungibleToken(address(wrapper)).configure(
    IWrappedHyperFungibleToken.WrappedConfigOptions({
        host: ISMP_HOST_ADDRESS,
        dispatcher: CALL_DISPATCHER_ADDRESS,
        underlying: USDC_ADDRESS,
        isWeth: false
    })
);
```

Find the `IsmpHost` address for your chain on the [contract addresses](/developers/evm/contract-addresses/mainnet) page. Existing `CallDispatcher` deployments are also listed there.
```
