## Analysis

The core broken invariant in the external report: an address that is meaningful/controlled by an actor on one chain is blindly reused as the recipient address on a different chain, without any confirmation that the same actor controls that address there. In Hyperbridge's `IntentGatewayV2`, this exact pattern occurs in the cross-chain intent-fill/settlement flow.

### Title
Cross-chain escrow release trusts the solver's destination-chain address as its source-chain payout address with no ownership proof - (File: `evm/src/apps/intentsv2/ExtrinsicIntents.sol`, `evm/src/apps/intentsv2/IntentsBase.sol`)

### Summary
When a solver fills a cross-chain order, `ExtrinsicIntents._fillCrossChain` builds the `WithdrawalRequest` sent back to the source chain using `beneficiary: bytes32(uint256(uint160(msg.sender)))` — i.e., the solver's `msg.sender` address *on the destination chain* is encoded verbatim as the beneficiary of the escrowed input tokens *on the source chain*. [1](#0-0) 

On the source chain, `IntentsBase._withdraw` decodes this value directly into an EVM address and transfers/pays out the escrowed tokens to it, with no verification that the solver actually controls that address on the source chain: [2](#0-1) 

### Finding Description
The `Order.output.beneficiary` field (destination-chain payout to the user) is explicitly designed as a `bytes32` chosen by the order creator, decoupled from `msg.sender` on either chain — this is correct design. However, the reverse leg of the flow — releasing the *escrowed input tokens* back to the *solver* — does not follow the same principle. Instead of letting the solver supply (and separately prove/opt-in) a source-chain address to receive their payout, the protocol hard-codes `msg.sender` (the address that called `fillOrder` on the destination chain) as the beneficiary for the `RedeemEscrow` message dispatched to the source chain:

```solidity
bytes memory body = bytes.concat(
    bytes1(uint8(RequestKind.RedeemEscrow)),
    abi.encode(
        WithdrawalRequest({
            commitment: commitment, tokens: order.inputs, beneficiary: bytes32(uint256(uint160(msg.sender)))
        })
    )
);
``` [3](#0-2) 

This is the same broken invariant as the external report's `SecuritizeBridge` bug: it assumes `msg.sender` is a single, portable identity that resolves to the *same controlling party* on both the destination chain (where the fill happens) and the source chain (where the payout lands). The docs confirm solvers are expected to use smart contract accounts — the `SolverAccount` combines ERC-4337 account abstraction and EIP-7702 delegation specifically to batch `select`/`fillOrder` calls: [4](#0-3) 

A smart contract wallet's address depends on deployment parameters (factory, salt, nonce, EIP-7702 delegation target, etc.) that can differ across chains, and any given address `0xAAA...` is not guaranteed to be controlled by the same party on the source chain as it is on the destination chain. When `_withdraw` executes on the source chain, it does not check that the beneficiary is an EOA, is a known/whitelisted solver identity, or has opted in to receive funds at that address — it performs an unconditional `safeTransfer`/native `call` to whatever address `msg.sender` (on the destination chain fill) happened to be: [5](#0-4) 

### Impact Explanation
If a solver fills an order using a smart-contract wallet address that is either (a) uninitialized/undeployed on the source chain, or (b) deployed there but controlled by a different party than on the destination chain, the escrowed input tokens released via `RedeemEscrow` are sent to an address the solver does not control on the source chain. This is a direct loss-of-funds/wrong-beneficiary condition matching the bounty's "stealing or loss of funds" and "wrong beneficiary or amount" categories — the escrow, which should compensate the solver for delivering output tokens, can be permanently misdirected or captured by an unrelated address owner on the source chain.

### Likelihood Explanation
This requires no malicious relayer, prover, or governance actor — it is triggered purely by an ordinary solver filling an order from a smart contract wallet (which the protocol's own `SolverAccount` design encourages via ERC-4337/EIP-7702). Any solver using account abstraction whose wallet address differs across the two chains involved in a given order (a common occurrence for CREATE2 factories with chain-dependent salts, or EIP-7702 delegations that are per-chain) will trigger this automatically and unintentionally, without any attacker action; a malicious actor could also deliberately fill orders from a smart-contract address they know is squatted by someone else on the source chain to grief solvers, or exploit their own address collisions to redirect payouts.

### Recommendation
Do not derive the source-chain escrow beneficiary from `msg.sender` on the destination chain. Instead, require the solver to explicitly supply and (ideally) sign for a destination-specific payout address as part of `FillOptions`, analogous to how `order.output.beneficiary` is separately specified rather than inferred from the order creator's address. Alternatively, restrict cross-chain fills to solver identities that have been explicitly registered/bound per-chain (e.g., via the existing `select`/`SolverAccount` session-key mechanism), so the source-chain payout address is asserted once and consistently, not implicitly re-derived from a potentially chain-specific contract address.

### Proof of Concept
1. Solver deploys/uses a smart contract wallet `W` at address `0xAAA...` on the destination chain (e.g., via `SolverAccount`/EIP-7702, or any CREATE2 factory whose salt/deployer differs by chain).
2. Solver calls `fillOrder(order, options)` from `W` on the destination chain; `_fillCrossChain` records `beneficiary = bytes32(uint256(uint160(msg.sender)))` i.e. `0xAAA...` and dispatches the `RedeemEscrow` message to the source chain. [6](#0-5) 
3. On the source chain, `0xAAA...` is either unowned/uncontrolled by the solver or is already controlled by a different party (an EOA, or a different contract deployed there).
4. When Hyperbridge delivers the message and `onAccept` calls `_withdraw`, the escrowed input tokens are irreversibly transferred to `0xAAA...` on the source chain: [5](#0-4) 
5. The solver has delivered the output tokens on the destination chain but cannot claim the escrowed compensation on the source chain — funds are lost or captured by whoever controls `0xAAA...` there.

### Citations

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L89-96)
```text
    function _fillCrossChain(Order calldata order, FillOptions calldata options, bytes32 commitment) internal {
        uint256 outputsLen = order.output.assets.length;

        _filled[commitment] = msg.sender;

        uint256 msgValue = msg.value;
        address beneficiary = address(uint160(uint256(order.output.beneficiary)));
        TokenInfo[] memory outputFills = new TokenInfo[](outputsLen);
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L139-147)
```text
        address hostAddr = host();
        bytes memory body = bytes.concat(
            bytes1(uint8(RequestKind.RedeemEscrow)),
            abi.encode(
                WithdrawalRequest({
                    commitment: commitment, tokens: order.inputs, beneficiary: bytes32(uint256(uint160(msg.sender)))
                })
            )
        );
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

**File:** docs/content/developers/evm/intent-gateway/overview.mdx (L116-118)
```text
### `SolverAccount`

The SolverAccount is a smart account designed for solvers that combines [ERC-4337](https://eips.ethereum.org/EIPS/eip-4337) (account abstraction), [EIP-7702](https://eips.ethereum.org/EIPS/eip-7702), and [ERC-7821](https://eips.ethereum.org/EIPS/eip-7821) (batch execution) to batch `gateway.select(...)` and `gateway.fillOrder(...)` into a single atomic UserOperation. Solvers delegate their EOA to the SolverAccount via EIP-7702 and submit bundled operations through the ERC-4337 EntryPoint.
```
