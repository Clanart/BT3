### Title
CW20ERC20Pointer.approve() overwrites allowance via a stale-read delta, enabling front-run to grant spenders excess allowance - (File: contracts/src/CW20ERC20Pointer.sol)

### Summary
`CW20ERC20Pointer.approve()` does not set the underlying CW20 allowance directly to the caller-specified amount. Instead it queries the *current* on-chain allowance at execution time and issues a relative `increase_allowance`/`decrease_allowance` CosmWasm message for the delta between that just-read value and the desired final amount [1](#0-0) . Because the "current allowance" is read fresh inside the transaction rather than being the value the caller had in mind when constructing the transaction, a `spender` who watches the mempool can front-run the owner's `approve()` call by first spending down (or otherwise changing) the allowance, causing the owner's transaction to compute an inflated delta and grant more allowance than intended — the same root cause as the `OlympusTreasury.setDebt` issue (an absolute "set" is implemented as "read old value, then apply delta", which is unsafe against a party who can manipulate the "old value" before the setting transaction lands).

### Finding Description
`approve(spender, amount)`:
1. Reads `currentAllowance = allowance(msg.sender, spender)` via a live query against the CW20 contract [2](#0-1) .
2. If `currentAllowance > amount`, it sends `decrease_allowance` for `currentAllowance - amount` [3](#0-2) .
3. If `currentAllowance < amount`, it sends `increase_allowance` for `amount - currentAllowance` [4](#0-3) .

This mirrors exactly the vulnerable pattern in the report: the final stored value is derived from `oldValue` (read at execution time) and a delta, rather than being set unconditionally to the intended target value. A `spender` (an ordinary, unprivileged EVM/CW20 pointer user) can observe a pending `approve()` transaction in the mempool and race it with their own `transferFrom` call to alter `currentAllowance` before the owner's transaction executes:

- Owner has previously granted `spender` an allowance of `X` and wants to reset it to a new value `Y` by calling `approve(spender, Y)`.
- `spender` front-runs with a `transferFrom` (or additional actions) that reduces the actual on-chain allowance to some lower value `X'` before the owner's tx is included.
- When the owner's `approve` executes, it reads `X'` (not `X`), computes `Y - X'` (larger than `Y - X`), and calls `increase_allowance` for that larger delta.
- The spender ends up having both consumed the pre-front-run allowance **and** received a final allowance equal to `Y`, i.e., strictly more total spending capability than the owner intended to grant (`X` spent + `Y` new allowance, instead of the intended net swap to `Y`).

This is reachable purely by ordinary CW20↔ERC20 pointer users submitting standard EVM transactions (`approve`, `transferFrom`) — no privileged or admin role is required on either side.

### Impact Explanation
The owner (approving party) can end up granting a spender more effective allowance/spend capacity than intended, allowing the spender to withdraw more of the owner's CW20 tokens through the pointer than the owner authorized in their latest `approve` call. This is a concrete fund-loss vector via a CW↔EVM pointer contract, matching the "unauthorized transfer via precompile or pointer" acceptance criterion.

### Likelihood Explanation
Any spender who has been granted a nonzero allowance and who monitors the mempool for the owner's next `approve()` transaction can exploit this deterministically by submitting a higher-gas-priced `transferFrom` transaction beforehand. No special privileges, timing luck beyond normal front-running, or protocol misconfiguration are required — only that the owner changes an allowance for a spender who has an incentive to grab extra funds.

### Recommendation
Do not derive the new allowance from a live-read "current" value combined with a caller-supplied delta. Instead, either:
- Set the allowance directly to the requested absolute value in a single CosmWasm message (if the underlying CW20 contract exposes a `set_allowance`-equivalent, or by first unconditionally zeroing then setting), or
- Change the pointer's `approve()` semantics to only support `increaseAllowance`/`decreaseAllowance`-style relative operations (mirroring the recommendation in the original report), so callers explicitly reason about deltas rather than assuming atomic "set" semantics that are secretly implemented as delta application against a value that can shift between transaction construction and execution.

### Proof of Concept
1. Owner grants `spender` allowance of 100 CW20 tokens (via pointer `approve(spender, 100)`).
2. Owner decides to reduce the allowance and submits `approve(spender, 0)`, intending to revoke all further spending.
3. `spender` observes this pending transaction and front-runs it with `transferFrom(owner, spender, 100)`, draining the full 100-token allowance and receiving the tokens.
4. Owner's `approve(spender, 0)` now executes: `currentAllowance` (queried at execution time) is `0`, which equals `amount` (`0`), so no message is sent — allowance stays 0, but the spender has already extracted the full 100 tokens.
5. Contrast with the intended "reduce" case where owner instead calls `approve(spender, 50)` intending to lower a 100 allowance to 50: spender front-runs with `transferFrom` for 100, allowance becomes 0; owner's tx then sees `currentAllowance(0) < amount(50)`, computes delta `50`, and issues `increase_allowance` for `50`. Spender ends up having received 100 tokens from the drain **and** a fresh 50-token allowance — 150 total value extracted/available versus the 50 the owner intended to leave outstanding. [1](#0-0)

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
