### Title
Unauthenticated `superApprove` allows anyone to forge unlimited ERC20 allowances for arbitrary token holders - (File: evm/src/utils/HyperFungibleTokenImpl.sol)

### Summary
`superApprove(address owner, address spender)` is declared `public` with no access-control modifier, no `msg.sender == owner` check, and no role gate, yet it directly calls the internal `_approve(owner, spender, type(uint256).max)` on behalf of any arbitrary `owner`.

### Finding Description
`HyperFungibleTokenImpl` inherits OpenZeppelin's `ERC20`/`AccessControlEnumerable` and defines minting/burning/role functions all properly gated by `onlyRole(...)`. `superApprove`, however, has no such gate: [1](#0-0) 

Because `_approve` bypasses the normal `msg.sender`-is-owner requirement that the standard ERC20 `approve` enforces, any unprivileged external caller can invoke `superApprove(victim, attackerSpender)` and set `allowance[victim][attackerSpender] = type(uint256).max` without any authorization from `victim`. The attacker-controlled `attackerSpender` can then call `transferFrom(victim, attacker, victim.balanceOf(victim))` and drain the victim's entire balance, even though the victim never called `approve`.

This breaks the fundamental ERC20 invariant that only the token owner (or an address it has already authorized) may determine spenders of its balance.

### Impact Explanation
This is a direct, unauthenticated fund-theft primitive: any address holding this token can have its full balance drained by an attacker with no prior interaction, transaction signing, or approval from the victim. This matches the bounty's "stealing or loss of funds" / "unauthorized transaction or execution" impact category, since it constitutes wrongful asset movement to an attacker-chosen beneficiary.

### Likelihood Explanation
Trivial and fully unprivileged: the function is `public`, callable by any EOA or contract, takes arbitrary `owner`/`spender` parameters, and requires no proof, message, or cross-chain settlement step — a single direct call is sufficient. The only mitigating factor is whether this contract (or a subclass exposing it) is actually deployed as a production token; the code references in `DeployIsmp.s.sol` and `TokenFaucet.sol` suggest it is part of deployable/production utility contracts rather than being confined to test mocks.

### Recommendation
Remove `superApprove` entirely from production code, or if it must exist for testing, move it into a test-only mock contract that is never deployed alongside real value-bearing tokens. If any legitimate use case requires it, gate it behind `require(msg.sender == owner, ...)` or an appropriate role check equivalent to a meta-approval scheme (e.g., signature-based `permit`).

### Proof of Concept
```solidity
// Attacker contract/EOA, no prior relationship with victim
HyperFungibleTokenImpl token = HyperFungibleTokenImpl(tokenAddress);

// victim never called approve(); attacker forges it
token.superApprove(victim, address(this));

// attacker now has unlimited allowance and drains victim's balance
uint256 bal = token.balanceOf(victim);
token.transferFrom(victim, attacker, bal);
``` [2](#0-1)

### Citations

**File:** evm/src/utils/HyperFungibleTokenImpl.sol (L110-117)
```text
    /**
     * @notice Helper function for tests - approves unlimited tokens
     * @param owner The owner of the tokens
     * @param spender The spender address
     */
    function superApprove(address owner, address spender) public {
        _approve(owner, spender, type(uint256).max);
    }
```
