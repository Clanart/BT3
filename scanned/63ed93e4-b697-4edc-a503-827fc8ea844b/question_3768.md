# Q3768: EVM OmniBridge initTransfer callback refund creates value gap

## Question
Can an unprivileged attacker cause the callback resolution behind `public EVM outbound transfer entrypoint` to refund tokens, keep a pending claim, or skip a compensating burn in `evm/src/omni-bridge/contracts/OmniBridge.sol::initTransfer` because of increments `currentOriginNonce`, enforces `fee < amount`, collects or burns assets depending on native/custom/bridge-token branches, and forwards value to `initTransferExtension`, violating `one outbound transfer must consume exactly the assets represented by the emitted bridge event and must not let branch-specific accounting diverge from the signed or relayed payload`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::initTransfer`
- Entrypoint: `public EVM outbound transfer entrypoint`
- Attacker controls: token address, amount, fee, native fee, recipient string, message bytes, and msg.value
- Exploit idea: Target `ft_transfer_call`-style paths where refund semantics affect whether state is removed or custody is burned.
- Invariant to test: one outbound transfer must consume exactly the assets represented by the emitted bridge event and must not let branch-specific accounting diverge from the signed or relayed payload
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Simulate every callback result and assert that no branch leaves both user-accessible funds and a still-live bridge claim for the same transfer.
