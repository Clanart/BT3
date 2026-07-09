# Q618: EVM OmniBridge native/bridged branching burn or lock before irreversible state at boundary values

## Question
Can an unprivileged attacker trigger `public EVM init/finalize entrypoints` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `evm/src/omni-bridge/contracts/OmniBridge.sol::finTransfer and initTransfer` violate `branch selection must never be attacker-steerable toward a more profitable asset path than the one the signed or emitted payload actually authorizes` in the `burn or lock before irreversible state` attack class because selects between ETH release, ERC-1155 transfer, custom-minter mint/burn, bridge-token mint/burn, or plain ERC-20 custody based on mutable branch predicates becomes fragile at those edges?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::finTransfer and initTransfer`
- Entrypoint: `public EVM init/finalize entrypoints`
- Attacker controls: zero address versus token address, custom-minter registration, bridge-token registration, and message presence
- Exploit idea: Look for branches where custody changes happen before the final pending-state, mapping, or callback outcome is fixed. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: branch selection must never be attacker-steerable toward a more profitable asset path than the one the signed or emitted payload actually authorizes
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model failures between custody changes and state writes, then assert that no branch both consumes user value and allows the transfer to be replayed or dropped. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
