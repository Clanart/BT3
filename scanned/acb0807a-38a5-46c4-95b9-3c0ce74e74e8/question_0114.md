# Q114: EVM OmniBridge native/bridged branching burn or lock before irreversible state

## Question
Can an unprivileged attacker use `public EVM init/finalize entrypoints` to force `evm/src/omni-bridge/contracts/OmniBridge.sol::finTransfer and initTransfer` to burn or lock assets before the transfer record becomes safely irreversible, and then recover or redirect the bridge flow via selects between ETH release, ERC-1155 transfer, custom-minter mint/burn, bridge-token mint/burn, or plain ERC-20 custody based on mutable branch predicates, violating `branch selection must never be attacker-steerable toward a more profitable asset path than the one the signed or emitted payload actually authorizes`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::finTransfer and initTransfer`
- Entrypoint: `public EVM init/finalize entrypoints`
- Attacker controls: zero address versus token address, custom-minter registration, bridge-token registration, and message presence
- Exploit idea: Look for branches where custody changes happen before the final pending-state, mapping, or callback outcome is fixed.
- Invariant to test: branch selection must never be attacker-steerable toward a more profitable asset path than the one the signed or emitted payload actually authorizes
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model failures between custody changes and state writes, then assert that no branch both consumes user value and allows the transfer to be replayed or dropped.
