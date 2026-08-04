### Title
Unauthenticated `superApprove` allows arbitrary allowance grant and draining of any account via `transferFrom` - (File: evm/src/utils/HyperFungibleTokenImpl.sol)

### Summary
`HyperFungibleTokenImpl.superApprove(address owner, address spender)` is a `public` function with no access-control modifier (no `onlyRole`, no `onlyGateway`, no `msg.sender == owner` check) that directly calls the internal `_approve(owner, spender, type(uint256).max)`, letting any caller set unlimited ERC20 allowance from any arbitrary `owner` to any `spender`.

### Finding Description
`mint` and `burn` are properly gated with `onlyRole(MINTER_ROLE)`/`onlyRole(BURNER_ROLE)`, and role management is gated with `onlyRole(DEFAULT_ADMIN_ROLE)`. [1](#0-0)  By contrast, `superApprove` has no modifier at all and unconditionally sets `owner`'s allowance to `spender` to the maximum uint256 value: [2](#0-1) 

Since `_approve` writes directly to the ERC20 `allowance` mapping (the custody value that gates `transferFrom`), any unprivileged EOA can call `superApprove(victim, attacker)` for an arbitrary `victim` address, then call the standard OpenZeppelin `transferFrom(victim, attacker, amount)` to move the victim's entire balance without their consent and without going through any role-authenticated minter/burner/gateway path.

The docstring labels it "Helper function for tests," but it is not restricted to a test build — it lives in the same production contract file (`evm/src/utils/HyperFungibleTokenImpl.sol`) that is deployed via the production deployment script `evm/script/DeployIsmp.s.sol`, which instantiates `HyperFungibleTokenImpl` alongside other production ISMP contracts. [3](#0-2)  There is no compile-time flag, `#ifdef`-style guard, or separate test-only artifact excluding this function from the deployed bytecode — it is a permanent, callable entry point on the mainnet-deployed token contract.

### Impact Explanation
Any address holding balance in a `HyperFungibleTokenImpl`-based token can have its funds drained by an unprivileged attacker: the attacker self-approves unlimited spend against any account and then calls `transferFrom` to sweep the balance to an address they control. This directly breaks the ERC20 custody invariant that only the token owner (or an address the owner approved) can authorize transfers, and it does so without any proof, role, or gateway authentication — a complete, unauthenticated loss-of-funds primitive on any deployed instance of this token implementation.

### Likelihood Explanation
Trivial and deterministic: the function is `public`, requires no special role, no proof, and no prior state; a single transaction (`superApprove`) followed by a standard `transferFrom` call suffices. Any EOA can execute this against any account holding balance in the token, as long as `HyperFungibleTokenImpl` (or a token using this implementation) is deployed and used to custody value.

### Recommendation
Remove `superApprove` from the production contract entirely, or if a test helper is genuinely required, move it to a test-only mock contract that is never deployed in production, and/or gate it with `onlyRole(DEFAULT_ADMIN_ROLE)` plus restrict its use to non-production environments (e.g., behind a constructor-set test flag that can never be enabled on mainnet deployments).

### Proof of Concept
```solidity
// Deploy HyperFungibleTokenImpl(admin, "Token", "TKN")
// admin grants MINTER_ROLE to itself and mints balance to victim
token.mint(victim, 1000e18);

// attacker (no role, unrelated EOA) calls:
vm.prank(attacker);
token.superApprove(victim, attacker); // sets allowance[victim][attacker] = type(uint256).max

// attacker drains victim's funds:
vm.prank(attacker);
token.transferFrom(victim, attacker, 1000e18); // succeeds, no consent from victim
assertEq(token.balanceOf(attacker), 1000e18);
assertEq(token.balanceOf(victim), 0);
```
This matches the existing test usage pattern of `superApprove` seen in `evm/tests/foundry/HyperFungibleTokenTest.sol`, `evm/tests/foundry/BaseTest.sol`, and `evm/tests/foundry/HandlerV2Test.sol`, confirming the function is callable with no restriction from any caller context.

### Citations

**File:** evm/src/utils/HyperFungibleTokenImpl.sol (L64-76)
```text
    function mint(address to, uint256 amount) external onlyRole(MINTER_ROLE) {
        _mint(to, amount);
    }

    /**
     * @notice Burns tokens from the specified account
     * @dev Can be called by any address with BURNER_ROLE
     * @param from The address from which tokens will be burned
     * @param amount The amount of tokens to burn
     */
    function burn(address from, uint256 amount) external onlyRole(BURNER_ROLE) {
        _burn(from, amount);
    }
```

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

**File:** evm/script/DeployIsmp.s.sol (L1-1)
```text
// SPDX-License-Identifier: UNLICENSED
```
