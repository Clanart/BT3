## Finding

### Title
Escrow ledger credited by nominal input amount instead of actual tokens received in Tron `IntentGatewayV2.placeOrder`, letting fee-on-transfer/tax tokens drain other users' escrow - (`evm/tron/contracts/apps/IntentGatewayV2.sol`)

### Summary
The Sense PT report's core broken invariant is: *the code assumes "amount credited to a user == amount of underlying asset actually held," with no check that the two match, and no recomputation when they diverge.* The exact same assumption is baked into the Tron variant of the Intent Gateway's `placeOrder`. It computes the escrow credit (`reducedInputs[i].amount`) from the order's *stated* input amount before any transfer happens, then unconditionally adds that nominal amount to `_orders[commitment][token]` after doing a plain `safeTransferFrom`, without ever checking how many tokens the gateway actually received. The mainline EVM contract (`evm/src/apps/IntentGatewayV2.sol`) was hardened against exactly this scenario (see its "Phase 1: ... record actual received amounts" comment and `balBefore`/`balAfter` diffing), but the Tron contract was never given the same fix.

### Finding Description
In `evm/tron/contracts/apps/IntentGatewayV2.sol::placeOrder`:

1. `reducedInputs` (and therefore the eventual escrow credit and the order `commitment`) is computed straight from `order.inputs[i].amount`, i.e. the amount the caller *claims* to be depositing: [1](#0-0) 

2. In the non-predispatch branch, the contract pulls tokens with a plain `safeTransferFrom` and then credits `_orders[commitment][token]` with `reducedInputs[i].amount` — the pre-fee, nominal amount — with no balance check whatsoever: [2](#0-1) 

For a fee-on-transfer, tax, or rebasing ERC20 (or any token whose `transferFrom` does not deliver exactly the requested amount), the gateway's actual token balance increases by *less* than `order.inputs[i].amount`, yet the escrow ledger `_orders[commitment][token]` is credited with the *full* nominal (only protocol-fee-reduced) amount. This is precisely the "assumes 1:1, no accounting for legitimate losses" bug class from the Sense PT report, transplanted from a redemption context into an escrow-accounting context.

`_orders[commitment][token]` is a per-commitment ledger, but the tokens themselves are held in one shared contract balance across *all* commitments for that token. When `withdraw()` (the internal function backing `fillOrder`/`cancelOrder`) later pays out `_orders[commitment][token]` to a beneficiary via `IERC20(token).transfer(...)`, it transfers real tokens out of that shared pool — it does not re-verify that the amount being paid was actually backed by real deposits for that specific commitment: [3](#0-2) 

So an attacker who places an order with a fee-on-transfer token inflates their own commitment's phantom escrow above what they actually deposited. When they fill/cancel their own order, `withdraw()` pays out the full inflated `_orders[commitment][token]` value, funded by tokens that other, legitimate users deposited for *their* unrelated orders. Those other users' later `cancelOrder`/`fillOrder` settlement then either reverts (Solidity underflow panic on `escrowed - amount`) — a fund freeze — or the pool runs dry entirely, permanently losing principal for whoever is left holding the now-unbacked commitment.

Compare this to the already-fixed mainline contract, which measures `balBefore`/`balAfter` and overwrites `order.inputs[i].amount` with the *actual* received amount before computing `reducedInputs`/`commitment`, guaranteeing the escrow ledger never exceeds custody: [4](#0-3) 
This exact fix — validated by the repo's own fee-on-transfer regression tests — is absent from the Tron deployment: [5](#0-4) 

### Impact Explanation
This is a fund-theft / permanent-loss primitive reachable by any unprivileged user placing an order with a fee-on-transfer or tax token as input — no malicious relayer, prover, or admin needed. The attacker's own inflated escrow entry pays out from tokens genuinely deposited by other, unrelated order placers, and the shared token pool becomes under-collateralized relative to the sum of all `_orders[...]` entries. This directly matches the bounty's required impact of "stealing or loss of funds" / "unauthorized transaction or execution" via wrong-beneficiary/wrong-amount settlement.

### Likelihood Explanation
Requires only that (a) the Tron IntentGatewayV2 accepts an arbitrary ERC20 as an order input and (b) at least one commonly-encountered fee-on-transfer/tax/rebasing token is usable as input — a routine condition for an open, permissionless order-input gateway. No relayer collusion, no cross-chain proof forgery, no privileged role — a single `placeOrder` + `cancelOrder`/self-fill by the attacker triggers the shortfall.

### Recommendation
Port the mainline fix into the Tron contract: measure `balanceOf(address(this))` before and after each `safeTransferFrom` (and similarly for the predispatch/dispatcher sweep path), overwrite `order.inputs[i].amount` with the actual delta, and only then compute `reducedInputs`, `commitment`, and the `_orders[commitment][token]` credit — exactly mirroring `evm/src/apps/IntentGatewayV2.sol` lines 260-298.

### Proof of Concept
1. Deploy a fee-on-transfer ERC20 (e.g. 5% burn/tax on `transferFrom`), as already modeled by `FeeOnTransferToken` in the test suite: [6](#0-5) 
2. A victim places a normal order with a *normal* ERC20 (e.g. USDC) for `1000 USDC`, credited correctly to `_orders[commitmentVictim][USDC] = 1000e6`.
3. Attacker places an order with the fee-on-transfer token as input, stating `amount = 1000e18`. `reducedInputs`/`commitment` are computed from `1000e18` (minus only protocol fee), but the gateway's actual `FOT` balance only increases by `950e18` (5% fee). `_orders[commitmentAttacker][FOT] = ~1000e18` (nominal) even though only `950e18` physically landed.
4. If FOT and USDC pools ever intermix custody assumptions are per-token so this specific PoC would need the shortfall token itself to be the one paid out — construct instead with the *same* token type across two independent orders: victim deposits `1000 FOT` truly (net `950 FOT` after transfer fee, but if victim's order also uses `order.inputs[i].amount` before the fix, it too is credited nominal `1000 FOT` scaled by protocol fee only — i.e., both orders' recorded escrow overstates real custody).
5. Attacker calls `cancelOrder`/self-fills first: `withdraw()` transfers out the attacker's full nominal escrow, drawing down the shared FOT balance below what remains needed to honor the victim's still-outstanding `_orders[commitmentVictim][FOT]` entry.
6. Victim's later `cancelOrder`/fill settlement now reverts (Solidity underflow panic on `escrowed - amount` once the shared pool is short) or — in a partially-drained scenario — the transfer simply fails, permanently freezing/losing the victim's principal.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L353-379)
```text
        if (protocolFeeBps > 0) {
            reducedInputs = new TokenInfo[](inputsLen);
            for (uint256 i; i < inputsLen;) {
                uint256 originalAmount = order.inputs[i].amount;
                uint256 protocolFee = (originalAmount * protocolFeeBps) / 10_000;
                uint256 reducedAmount = originalAmount - protocolFee;
                address token = address(uint160(uint256(order.inputs[i].token)));

                // Emit DustCollected for protocol fee if non-zero
                if (protocolFee > 0) emit DustCollected(token, protocolFee);

                reducedInputs[i] = TokenInfo({token: order.inputs[i].token, amount: reducedAmount});
                unchecked {
                    ++i;
                }
            }

            // Temporarily swap inputs to calculate commitment with reduced amounts
            TokenInfo[] memory originalInputs = order.inputs;
            order.inputs = reducedInputs;
            commitment = keccak256(abi.encode(order));
            order.inputs = originalInputs;
        } else {
            // No protocol fees, use order.inputs directly
            reducedInputs = order.inputs;
            commitment = keccak256(abi.encode(order));
        }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L444-462)
```text
        } else {
            for (uint256 i; i < inputsLen;) {
                if (order.inputs[i].amount == 0) revert InvalidInput();
                address token = address(uint160(uint256(order.inputs[i].token)));
                if (token == address(0)) {
                    // native token
                    if (msgValue < order.inputs[i].amount) revert InsufficientNativeToken();
                    msgValue -= order.inputs[i].amount;
                } else {
                    IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
                }

                // Store reduced amount (after protocol fees) in escrow
                _orders[commitment][token] += reducedInputs[i].amount;

                unchecked {
                    ++i;
                }
            }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-705)
```text
    function withdraw(WithdrawalRequest memory body, bool isRefund) internal {
        address beneficiary = address(uint160(uint256(body.beneficiary)));
        _filled[body.commitment] = beneficiary;

        // redeem escrowed tokens
        uint256 len = body.tokens.length;
        for (uint256 i; i < len;) {
            address token = address(uint160(uint256(body.tokens[i].token)));
            uint256 amount = body.tokens[i].amount;
            if (_orders[body.commitment][token] == 0) revert UnknownOrder();

            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
                if (!success) revert TransferFailed();
            }

            _orders[body.commitment][token] -= amount;
            unchecked {
                ++i;
            }
        }
```

**File:** evm/src/apps/IntentGatewayV2.sol (L281-297)
```text
        } else {
            for (uint256 i; i < inputsLen;) {
                if (order.inputs[i].amount == 0) revert InvalidInput();
                address token = address(uint160(uint256(order.inputs[i].token)));
                if (token == address(0)) {
                    if (msgValue < order.inputs[i].amount) revert InsufficientNativeToken();
                    msgValue -= order.inputs[i].amount;
                } else {
                    uint256 balBefore = IERC20(token).balanceOf(address(this));
                    IERC20(token).safeTransferFrom(msg.sender, address(this), order.inputs[i].amount);
                    order.inputs[i].amount = IERC20(token).balanceOf(address(this)) - balBefore;
                }

                unchecked {
                    ++i;
                }
            }
```

**File:** evm/tests/foundry/IntentGatewayV2SameChainTest.sol (L2256-2308)
```text
    /// @notice Escrow correctly reflects actual received amount for fee-on-transfer tokens.
    function testPlaceOrder_FeeOnTransferToken_EscrowMatchesReceived() public {
        // Deploy a 1% fee-on-transfer token
        FeeOnTransferToken fot = new FeeOnTransferToken(100); // 1% = 100 bps
        fot.mint(user, 10000 * 1e18);

        uint256 inputAmount = 1000 * 1e18;
        uint256 expectedReceived = inputAmount - (inputAmount * 100) / 10000; // 990

        TokenInfo[] memory inputs = new TokenInfo[](1);
        inputs[0] = TokenInfo({token: bytes32(uint256(uint160(address(fot)))), amount: inputAmount});

        TokenInfo[] memory outputAssets = new TokenInfo[](1);
        outputAssets[0] = TokenInfo({token: bytes32(uint256(uint160(address(dai)))), amount: 900 * 1e18});

        PaymentInfo memory output =
            PaymentInfo({beneficiary: bytes32(uint256(uint160(user))), assets: outputAssets, call: ""});

        Order memory order = Order({
            user: bytes32(0),
            source: "",
            destination: host.host(),
            deadline: block.number + 100,
            nonce: 0,
            fees: 0,
            session: address(0),
            predispatch: DispatchInfo({assets: new TokenInfo[](0), call: ""}),
            inputs: inputs,
            output: output
        });

        vm.startPrank(user);
        fot.approve(address(intentGateway), inputAmount);
        intentGateway.placeOrder(order, bytes32(0));
        vm.stopPrank();

        // Gateway should hold only what it actually received
        assertEq(fot.balanceOf(address(intentGateway)), expectedReceived, "Gateway balance should match received amount");

        // Reconstruct the order as placeOrder would have mutated it
        order.user = bytes32(uint256(uint160(user)));
        order.source = host.host();
        order.nonce = 0;
        order.inputs[0].amount = expectedReceived;
        bytes32 commitment = keccak256(abi.encode(order));

        // Escrow should match actual received, not the user-specified amount
        assertEq(
            intentGateway._orders(commitment, address(fot)),
            expectedReceived,
            "Escrow should equal actual received amount"
        );
    }
```

**File:** evm/tests/foundry/IntentGatewayV2SameChainTest.sol (L2501-2547)
```text
/// @dev ERC20 with a configurable transfer fee (in basis points).
contract FeeOnTransferToken {
    string public name = "FeeOnTransferToken";
    string public symbol = "FOT";
    uint8 public decimals = 18;
    uint256 public totalSupply;
    uint256 public feeBps;

    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    constructor(uint256 _feeBps) {
        feeBps = _feeBps;
    }

    function mint(address to, uint256 amount) external {
        balanceOf[to] += amount;
        totalSupply += amount;
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        return true;
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        return _transfer(msg.sender, to, amount);
    }

    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        uint256 allowed = allowance[from][msg.sender];
        if (allowed != type(uint256).max) {
            allowance[from][msg.sender] = allowed - amount;
        }
        return _transfer(from, to, amount);
    }

    function _transfer(address from, address to, uint256 amount) internal returns (bool) {
        uint256 fee = (amount * feeBps) / 10_000;
        uint256 received = amount - fee;
        balanceOf[from] -= amount;
        balanceOf[to] += received;
        // fee is burned
        totalSupply -= fee;
        return true;
    }
}
```
