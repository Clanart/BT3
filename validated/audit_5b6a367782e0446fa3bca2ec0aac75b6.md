### Title
`CW20ERC20Pointer.approve()` implements a delta-based re-approval that is vulnerable to the classic ERC20 allowance front-running / double-spend race - ([File: contracts/src/CW20ERC20Pointer.sol])

### Summary
The `CW20ERC20Pointer` contract (the ERC20-facing pointer that Sei deploys for every CW20 token so it can be used from the EVM) implements `approve()` not as a plain overwrite of the allowance, but by first querying the *current* on-chain CW20 allowance and then issuing a CW20 `increase_allowance`/`decrease_allowance` message for the delta between the current value and the requested value. This re-introduces the classic ERC20 allowance front-running/double-spend problem that `increase/decrease allowance` patterns are normally used to *prevent* — here it is used in a way that makes the pointer's `approve()` racy against a spender who is watching the mempool.

### Finding Description
`approve()` in `CW20ERC20Pointer.sol` works as follows: [1](#0-0) 

1. It reads the live CW20 allowance via `allowance(msg.sender, spender)` (a synchronous query into the CW20 contract at execution time, not a cached/stale value from calldata).
2. It computes `delta = |currentAllowance - amount|` and sends a `decrease_allowance` or `increase_allowance` execute message for that delta to the underlying CW20 contract via the wasmd precompile.

Because the "current allowance" used to compute the delta is read live at the moment the `approve()` transaction executes on-chain (not snapshotted when the owner signed/submitted the transaction), a malicious `spender` who observes the owner's pending `approve(spender, newAmount)` transaction in the mempool can front-run it:

1. Owner has allowance `A` set for `spender` and broadcasts `approve(spender, B)` intending to reduce/change the allowance to `B`.
2. `spender` front-runs with `transferFrom(owner, spender, A)`, fully draining the old allowance `A` and receiving `A` tokens.
3. The owner's `approve(spender, B)` transaction now executes. It queries the *current* allowance, which is `0` (already spent by the front-run), and issues `increase_allowance(spender, B)`, resulting in a final allowance of `B`.
4. `spender` has now received `A` tokens from the drained allowance **and** still holds a fresh, unspent allowance of `B` — i.e., the owner's original intent of "the spender should be able to draw at most `B`" is violated, and the spender effectively obtains `A + B` in total spending power instead of the owner-intended `B`.

This is functionally the same bug class as the reported Notional issue: an allowance-consuming operation (`withdrawViaProxy`/`transferFrom`) can be raced against an allowance-updating operation (`nTokenTransferApprove`/`approve`) such that the spender captures both the old and the new allowance instead of just the new one, because the contract does not force the owner to reset the allowance to zero (or otherwise atomically supersede the prior allowance) before applying a new value.

The `CW20ERC20Pointer` contract is not a demo/test artifact — it is the actual bytecode deployed by the chain for every CW20↔ERC20 pointer, referenced from the pointer-creation logic and precompiled artifacts: [2](#0-1) [3](#0-2) 

Any EVM account can create/use such a pointer and call `approve`/`transferFrom` on it, making this reachable from ordinary transaction submission with no special privileges.

### Impact Explanation
A spender with an existing nonzero allowance can front-run the account owner's attempt to reduce or change that allowance, extracting funds from the owner up to the sum of the old and new allowances rather than being capped at the owner-intended final allowance. This is a direct unauthorized-transfer/fund-loss vector via a CW20↔EVM pointer contract, matching the "unauthorized transfer via precompile or pointer" acceptance criterion.

### Likelihood Explanation
Exploitation only requires the attacker to be an approved spender (a state the attacker itself can be granted honestly, e.g. as part of a normal integration) and to monitor the mempool for the owner's `approve()` transaction that changes their allowance — a widely known and easily automated MEV/front-running pattern requiring no special chain privileges, just a public RPC/mempool observer and the ability to submit a transaction with higher gas priority.

### Recommendation
Change `approve()` in `CW20ERC20Pointer.sol` to not depend on the currently-observed allowance value to compute a delta. Standard mitigations:
- Only support `increaseAllowance`/`decreaseAllowance`-style entry points that take an explicit delta argument (matching what OpenZeppelin recommends), rather than deriving the delta internally from a racy read of current state within `approve(address,uint256)`.
- Alternatively, require the allowance to be reset to zero before it can be changed to a new nonzero value (revert if `currentAllowance != 0 && amount != 0`), forcing owners to use two transactions and eliminating the ambiguous intermediate state that a front-runner can exploit.

### Proof of Concept
1. Owner sets `pointer.approve(spender, 100)` — allowance becomes 100 on the underlying CW20 contract.
2. Owner decides to reduce exposure and submits `pointer.approve(spender, 10)`.
3. `spender`, watching the mempool, submits (and gets mined first via higher gas) `pointer.transferFrom(owner, spender, 100)`, draining the full old allowance and receiving 100 tokens; allowance is now 0.
4. Owner's `approve(spender, 10)` executes: `currentAllowance = allowance(owner, spender)` returns 0 (post front-run), so the code path takes the `increase_allowance` branch and issues `increase_allowance(spender, 10)`, setting the allowance to 10.
5. Net result: `spender` obtained 100 tokens from the drained old allowance and still holds a fresh, unspent allowance of 10 — total extractable value of 110, whereas the owner's final intended cap was 10. [4](#0-3)

### Citations

**File:** contracts/src/CW20ERC20Pointer.sol (L59-96)
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

    function transfer(address to, uint256 amount) public override returns (bool) {
        require(to != address(0), "ERC20: transfer to the zero address");
        string memory recipient = _formatPayload("recipient", _doubleQuotes(AddrPrecompile.getSeiAddr(to)));
        string memory amt = _formatPayload("amount", _doubleQuotes(Strings.toString(amount)));
        string memory req = _curlyBrace(_formatPayload("transfer", _curlyBrace(_join(recipient, amt, ","))));
        _execute(bytes(req));
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) public override returns (bool) {
        require(to != address(0), "ERC20: transfer to the zero address");
        string memory sender = _formatPayload("owner", _doubleQuotes(AddrPrecompile.getSeiAddr(from)));
        string memory recipient = _formatPayload("recipient", _doubleQuotes(AddrPrecompile.getSeiAddr(to)));
        string memory amt = _formatPayload("amount", _doubleQuotes(Strings.toString(amount)));
        string memory req = _curlyBrace(_formatPayload("transfer_from", _curlyBrace(_join(_join(sender, recipient, ","), amt, ","))));
        _execute(bytes(req));
        return true;
    }
```

**File:** x/evm/artifacts/cw20/artifacts.go (L1-1)
```go
package cw20
```

**File:** x/evm/keeper/pointer.go (L1-1)
```go
package keeper
```
