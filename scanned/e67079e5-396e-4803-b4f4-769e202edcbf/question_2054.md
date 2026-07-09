# Q2054: Solana init_transfer resume-path replay or duplication

## Question
Can an unprivileged attacker make the deferred path behind `public Solana `init_transfer` instruction` resume more than once or resume after the economic transfer was already completed because `solana/programs/bridge_token_factory/src/lib.rs::init_transfer` relies on charges native fee into the SOL vault, either transfers native custody into the vault or burns bridged supply, and posts a Near-bound message, violating `one outbound Solana transfer must consume exactly the asset and fee implied by the emitted message and must not let native-vault and bridged-burn branches diverge`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/lib.rs::init_transfer`
- Entrypoint: `public Solana `init_transfer` instruction`
- Attacker controls: mint, source token account, optional vault, user signer, amount, fee, native fee, recipient string, and message
- Exploit idea: Abuse yield/resume or asynchronous callback timing so the same pending outbound transfer is restarted after it already progressed.
- Invariant to test: one outbound Solana transfer must consume exactly the asset and fee implied by the emitted message and must not let native-vault and bridged-burn branches diverge
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Trigger timeouts, duplicate funding, and repeated callback delivery and assert that the resumed transfer either progresses once or cleanly fails once.
