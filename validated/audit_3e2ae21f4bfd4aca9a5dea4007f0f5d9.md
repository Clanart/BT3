## Finding: Valid — Unrestricted `superApprove` Allows Anyone to Drain Any Holder's `HyperFungibleTokenImpl`/`FeeToken` Balance

### Title
Unauthenticated `superApprove` grants infinite ERC20 allowance to any caller, enabling unrestricted `transferFrom` draining — (`evm/src/utils/HyperFungibleTokenImpl.sol`)

### Summary
`HyperFungibleTokenImpl.sol`, a production contract in `evm/src/utils/`, exposes a `public` function `superApprove(address owner, address spender)` that calls `_approve(owner, spender, type(uint256).max)` with **no caller authentication whatsoever** — not `onlyRole`, not `msg.sender == owner`, nothing. [1](#0-0)  Any unprivileged address can call `superApprove(victim, attacker)` to grant itself unlimited spending allowance over any victim's balance, then call `transferFrom` to drain it — no replenishment loop is even required, since a single call suffices to grant max allowance, and each subsequent call simply re-confirms the max allowance is (still) in place.

### Finding Description
`HyperFungibleTokenImpl` is a token implementation used as the base for `FeeToken` (via inheritance) and referenced by `TokenFaucet` and the deployment script `DeployIsmp.s.sol`. [2](#0-1) [3](#0-2)  The contract correctly gates `mint`/`burn` behind `onlyRole(MINTER_ROLE)`/`onlyRole(BURNER_ROLE)`, and role management behind `onlyRole(DEFAULT_ADMIN_ROLE)`. [4](#0-3)  However, `superApprove` has none of these guards — it is a bare `public` function that directly manipulates the internal `_allowances` mapping via `_approve` for *any* `owner` address supplied by the caller. [5](#0-4)  The doc comment states it is "Helper function for tests," but it lives in `evm/src/utils/` (production source, not `evm/tests/`) and is inherited unconditionally by any deployed token built on this base (e.g. `FeeToken`).

The attack does not require the fuzzing/replenishment scenario described in the question at all — the vulnerability is a direct, single-call authorization bypass: an attacker calls `superApprove(victim, attacker)` once to obtain `type(uint256).max` allowance on the victim's balance, and can then call `transferFrom(victim, attacker, victim.balanceOf())` to steal the victim's entire current balance. If the victim's balance is replenished later (e.g., minted rewards, relayer fees), the attacker's already-granted max allowance remains valid (ERC20 allowances aren't balance-capped), so the attacker can repeatedly call `transferFrom` to drain each new replenishment without needing to call `superApprove` again.

### Impact Explanation
This breaks the "bridged/reward balances must move exactly once to the rightful beneficiary" invariant required by the bounty scope. Any address holding this fee/fungible token — including relayer reward balances, cross-chain settlement balances, or user funds — can be drained by an arbitrary unprivileged caller with zero signature or role requirement. This is a direct, unauthorized asset-movement / theft-of-funds bug matching the "stealing or loss of funds" and "unauthorized transaction or execution" impact categories in scope.

### Likelihood Explanation
Trivial and deterministic: the function requires no special preconditions, no proof, no relayer, no privileged role — just a plain external call from any EOA or contract. If this contract (or any token inheriting it, such as `FeeToken`) is deployed as a production fee/reward token, exploitation is guaranteed and repeatable for the life of the contract.

### Recommendation
Remove `superApprove` from production source entirely, or, if it must exist for test scaffolding, restrict it to a test-only mock contract outside `evm/src/`, and/or gate it with `onlyRole` / `msg.sender == owner` checks so it can only be invoked by the token owner themselves (matching standard ERC20 `approve` semantics).

### Proof of Concept
```solidity
// Attacker contract/EOA — no roles needed
feeToken.superApprove(victim, address(this)); // sets allowance[victim][this] = type(uint256).max
uint256 stolen = feeToken.balanceOf(victim);
feeToken.transferFrom(victim, address(this), stolen); // drains victim entirely

// Later, victim balance replenished (e.g. relayer fee payment / reward mint)
feeToken.transferFrom(victim, address(this), feeToken.balanceOf(victim)); // drains again, allowance already max
```

Note: I could not fully confirm from the indexed portion of `evm/script/DeployIsmp.s.sol` whether `HyperFungibleTokenImpl`/`FeeToken` is the actual token deployed as the live production fee token versus only used in test/deployment-script contexts — the grep matched 21-23 references but full file contents weren't retrievable within the tool limits. If this contract is only ever deployed in test environments and never as the production `IsmpHost` fee token, the severity should be downgraded from "production funds at risk" to "test-scaffolding hygiene issue." A Devin session with full file access would be needed to make that determination conclusively.

### Citations

**File:** evm/src/utils/HyperFungibleTokenImpl.sol (L64-108)
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

    /**
     * @notice Grants minter role to an address
     * @param account The address to grant the minter role to
     */
    function grantMinterRole(address account) external onlyRole(DEFAULT_ADMIN_ROLE) {
        _grantRole(MINTER_ROLE, account);
    }

    /**
     * @notice Grants burner role to an address
     * @param account The address to grant the burner role to
     */
    function grantBurnerRole(address account) external onlyRole(DEFAULT_ADMIN_ROLE) {
        _grantRole(BURNER_ROLE, account);
    }

    /**
     * @notice Revokes minter role from an address
     * @param account The address to revoke the minter role from
     */
    function revokeMinterRole(address account) external onlyRole(DEFAULT_ADMIN_ROLE) {
        _revokeRole(MINTER_ROLE, account);
    }

    /**
     * @notice Revokes burner role from an address
     * @param account The address to revoke the burner role from
     */
    function revokeBurnerRole(address account) external onlyRole(DEFAULT_ADMIN_ROLE) {
        _revokeRole(BURNER_ROLE, account);
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

**File:** evm/tests/foundry/FeeToken.sol (L17-27)
```text
import {HyperFungibleTokenImpl} from "../../src/utils/HyperFungibleTokenImpl.sol";

/**
 * @title FeeToken
 * @notice Test token that extends HyperFungibleTokenImpl with initial supply minting
 */
contract FeeToken is HyperFungibleTokenImpl {
    constructor(address admin, string memory name, string memory symbol) HyperFungibleTokenImpl(admin, name, symbol) {
        // Mint initial supply to tx.origin for testing purposes
        _mint(tx.origin, 1_000_000_000_000000000000000000);
    }
```

**File:** evm/src/utils/TokenFaucet.sol (L17-39)
```text
import {HyperFungibleTokenImpl} from "./HyperFungibleTokenImpl.sol";

/**
 * @title The TokenFaucet.
 * @author Polytope Labs (hello@polytope.technology)
 *
 * @notice Allows access to a fixed amount of tokens to users on a daily basis
 */
contract TokenFaucet {
    mapping(address => uint256) private consumers;

    // @dev Will only drip tokens, once per day
    function drip(address token) public {
        uint256 lastDrip = consumers[msg.sender];
        uint256 delay = block.timestamp - lastDrip;

        if (delay < 1 days) {
            revert("Can only request tokens once daily");
        }

        consumers[msg.sender] = block.timestamp;
        HyperFungibleTokenImpl(token).mint(msg.sender, 1000 * 1e18);
    }
```
