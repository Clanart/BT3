### Title
`CW20ERC20Pointer.approve()` allowance change can be front-run to grant unintended extra allowance - (File: `contracts/src/CW20ERC20Pointer.sol`)

### Summary
`CW20ERC20Pointer.approve()` does not set an absolute allowance value on the underlying CW20 contract. Instead, it reads the *current* on-chain allowance at execution time and computes a delta (`increase_allowance`/`decrease_allowance`) to reach the caller's desired target amount. Because the read-then-write is not atomic with respect to the spender's own transactions, a malicious spender can front-run an owner's allowance-reducing `approve()` call, causing the owner's transaction to compute its delta against an allowance value the spender has already manipulated — resulting in the spender ending up with more allowance than the owner ever intended to grant.

### Finding Description
The pointer's `approve()` function is: [1](#0-0) 

It calls `allowance(msg.sender, spender)` (line 64) to read the *live* CW20 allowance, then computes the difference to the caller's `amount` and issues either `increase_allowance` or `decrease_allowance` for that delta. This is the classic ERC-20 "allowance race condition" pattern, except made worse: standard ERC-20 `approve()` simply overwrites the allowance (so a front-run only lets the attacker spend the old value once before the new value takes effect), whereas this pointer computes a **relative** change based on a **stale read**.

Attack sequence:
1. Owner has approved `spender` for 100 tokens on the CW20 contract via the pointer.
2. Owner decides to reduce the allowance and submits `approve(spender, 50)`.
3. The malicious `spender` observes this transaction in the mempool and front-runs it with `transferFrom(owner, spender, 100)`, draining the full existing allowance (now `0`).
4. The owner's `approve(spender, 50)` transaction executes afterward: it reads `currentAllowance = 0` (line 64), and since `0 < 50`, it calls `increase_allowance` for `50` (lines 70-74).
5. Net result: the spender has now transferred the original 100 tokens **and** has been granted an additional 50 allowance — strictly more than the owner ever authorized at any single point in time.

This is directly analogous to the `toggleRoll()` issue: a party changes the mempool ordering to subvert a transaction that was meant to reduce/restrict a counterparty's permission, exploiting a default/interim state that is more permissive than intended.

### Impact Explanation
A spender with any existing allowance can extract more value than the owner ever intended to approve, by combining a drain of the stale allowance with the reducing `approve()` transaction. This causes direct fund loss for the CW20/ERC20 pointer token owner, is reachable by any unprivileged EVM transaction sender interacting with a public `CW20ERC20Pointer` contract (part of the CW↔EVM pointer bridge), and requires no privileged role.

### Likelihood Explanation
Likelihood is high: any account that has ever been granted an allowance via this pointer can watch the public mempool for an `approve()` call from the owner that reduces or changes their allowance, and front-run it with a `transferFrom`. No special access or timing beyond standard MEV/front-running capability is required.

### Recommendation
Do not compute allowance changes based on a live read of on-chain state at execution time. Either:
- Implement `approve()` to set an absolute value on the CW20 side (e.g., via a `set_allowance`-style message if the CW20 spec supports it), or
- Require callers to use `increaseAllowance`/`decreaseAllowance` with an expected-current-value parameter (matching the common OpenZeppelin race-condition mitigation), reverting if the on-chain allowance does not match the expected value at execution time, so a front-run cannot silently change the computed delta.

### Proof of Concept
1. Owner calls `pointer.approve(spender, 100)` on `CW20ERC20Pointer`, granting `spender` 100 tokens allowance.
2. Owner later calls `pointer.approve(spender, 50)` intending to reduce the allowance to 50.
3. Before that transaction is included, `spender` submits `pointer.transferFrom(owner, spender, 100)` with a higher gas price, draining the full 100 allowance and receiving 100 tokens.
4. Owner's `approve(spender, 50)` executes next: `allowance(owner, spender)` now returns `0` (line 64), so `currentAllowance < amount` triggers `increase_allowance` of `50 - 0 = 50` (lines 70-74).
5. `spender` now holds an allowance of `50` in addition to having already received the original `100` tokens via `transferFrom` — exceeding what the owner intended to expose at any point. [2](#0-1)

### Citations

**File:** contracts/src/CW20ERC20Pointer.sol (L50-77)
```text
    function allowance(address owner, address spender) public view override returns (uint256) {
        string memory o = _formatPayload("owner", _doubleQuotes(AddrPrecompile.getSeiAddr(owner)));
        string memory s = _formatPayload("spender", _doubleQuotes(AddrPrecompile.getSeiAddr(spender)));
        string memory req = _curlyBrace(_formatPayload("allowance", _curlyBrace(_join(o, s, ","))));
        bytes memory response = WasmdPrecompile.query(Cw20Address, bytes(req));
        return JsonPrecompile.extractAsUint256(response, "allowance");
    }

    // Transactions
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
