### Title
CW20ERC20Pointer.approve() recomputes an absolute allowance target via increase/decrease_allowance, preserving the classic approve front-running double-spend - ([File: contracts/src/CW20ERC20Pointer.sol])

### Summary
`CW20ERC20Pointer` is the EVM-side ERC20 pointer contract that lets EVM users interact with a CW20 token through the wasmd precompile. Its `approve()` override attempts to avoid the classic ERC20 approve race by translating the call into `increase_allowance`/`decrease_allowance` messages against the underlying CW20 contract, but it derives the amount to increase/decrease from a fresh read of the *current* on-chain allowance rather than from a caller-supplied expected-previous-value or a pure relative delta. This reintroduces the exact double-spend/front-running condition the recommended `increaseAllowance`/`decreaseAllowance` pattern is supposed to prevent.

### Finding Description
`approve(address spender, uint256 amount)` in [1](#0-0)  works by:
1. Reading `currentAllowance = allowance(msg.sender, spender)` from the underlying CW20 contract via the wasmd precompile query.
2. If `currentAllowance > amount`, calling `decrease_allowance` for `currentAllowance - amount`.
3. If `currentAllowance < amount`, calling `increase_allowance` for `amount - currentAllowance`.

The intent is for the caller's final allowance to always land on `amount`, but the delta is still computed relative to whatever the allowance happens to be *at the moment the transaction executes on-chain* — not relative to a value the caller actually witnessed or approved. This is functionally equivalent to the vulnerable `allowance = amount` overwrite pattern that the original report flags: a spender that front-runs the owner's `approve` transaction with a `transferFrom` consuming the old allowance will cause the owner's subsequent `approve` transaction to top the allowance back up to the new target amount, on top of the tokens already transferred by the front-run. The underlying CW20 contract's `increase_allowance`/`decrease_allowance` messages, invoked via `_execute` -> `WASMD_PRECOMPILE_ADDRESS.delegatecall` in [2](#0-1) , execute exactly as instructed with no check that the observed `currentAllowance` matches what the owner intended to reduce from.

### Impact Explanation
An EVM user who owns a CW20 token (reachable via any CW20<->ERC20 pointer deployment) and wants to reduce a spender's allowance from `X` to a lower value `Y` can be front-run by the spender: the spender submits `transferFrom` for the full old allowance `X` right before the owner's `approve(spender, Y)` lands. Because the pointer recomputes the delta from the now-reduced current allowance (0 after the spend) rather than from `X`, the owner's transaction ends up granting an *additional* `Y` allowance on top of the `X` already spent — resulting in the spender being able to draw `X + Y` tokens total instead of the owner-intended cap of `Y`. This is unauthorized transfer of CW20-pointed funds beyond the owner's authorization via a pointer contract.

### Likelihood Explanation
Any spender with an existing non-zero allowance on a `CW20ERC20Pointer` instance can trivially monitor the public mempool for the owner's allowance-reducing `approve` transaction and front-run it with a `transferFrom` — this requires no special privileges, only normal EVM transaction submission capability, making it straightforward for a malicious approved spender to exploit whenever an owner attempts to lower their allowance.

### Recommendation
Do not derive the increase/decrease delta from a live re-query of the current allowance inside `approve()`. Instead, expose explicit `increaseAllowance(spender, addedValue)` and `decreaseAllowance(spender, subtractedValue)` functions that operate on caller-specified relative deltas (matching OpenZeppelin's mitigation), and/or require the caller to pass the previously-observed allowance so the contract can revert (rather than silently top-up) if the current on-chain allowance no longer matches expectations before adjusting it.

### Proof of Concept
1. Owner `O` grants spender `S` an allowance of 100 tokens on a CW20 token exposed through `CW20ERC20Pointer` (`pointer.approve(S, 100)`).
2. `O` decides to reduce the allowance and submits `pointer.approve(S, 50)`.
3. `S` observes this pending transaction in the mempool and front-runs it with `pointer.transferFrom(O, S, 100)`, consuming the full original allowance (CW20 allowance becomes 0).
4. `O`'s `approve(S, 50)` transaction then executes: it reads `currentAllowance = 0`, sees `0 < 50`, and calls `increase_allowance` for 50, setting the CW20 allowance to 50.
5. Net result: `S` has received 100 tokens via the front-run `transferFrom` and now holds an additional 50 allowance to spend — a total of 150 tokens accessible, versus the 50 the owner intended to authorize after the reduction.

### Citations

**File:** contracts/src/CW20ERC20Pointer.sol (L59-77)
```text
    function approve(address spender, uint256 amount) public override returns (bool) {
        // if amount is larger uint128 then set amount to uint128 max
        if (amount > type(uint128).max) {
            amount = type(uint128).max;
        }
        uint256 currentAllowance = allowance(msg.sender, spender);
        if (currentAllowance > amount) {
            string memory spenderAddr = _formatPayload("spender", _doubleQuotes(AddrPrecompile.getSeiAddr(spender)));
            string memory amt = _formatPayload("amount", _doubleQuotes(Strings.toString(currentAllowance - amount)));
            string memory req = _curlyBrace(_formatPayload("decrease_allowance", _curlyBrace(_join(spenderAddr, amt, ","))));
            _execute(bytes(req));
        } else if (currentAllowance < amount) {
            string memory spenderAddr = _formatPayload("spender", _doubleQuotes(AddrPrecompile.getSeiAddr(spender)));
            string memory amt = _formatPayload("amount", _doubleQuotes(Strings.toString(amount - currentAllowance)));
            string memory req = _curlyBrace(_formatPayload("increase_allowance", _curlyBrace(_join(spenderAddr, amt, ","))));
            _execute(bytes(req));
        }
        return true;
    }
```

**File:** contracts/src/CW20ERC20Pointer.sol (L98-109)
```text
    function _execute(bytes memory req) internal returns (bytes memory) {
        (bool success, bytes memory ret) = WASMD_PRECOMPILE_ADDRESS.delegatecall(
            abi.encodeWithSignature(
                "execute(string,bytes,bytes)",
                Cw20Address,
                bytes(req),
                bytes("[]")
            )
        );
        require(success, "CosmWasm execute failed");
        return ret;
    }
```
