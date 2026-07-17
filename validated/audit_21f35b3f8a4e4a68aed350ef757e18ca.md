Let me analyze the external report's core invariant and search for nearcore analogs systematically.

The external bug's core pattern: **A commitment (redemption NFT) is issued representing a claim on funds, but the underlying funds can be drained by a privileged action, breaking the redemption invariant and leaving users unable to withdraw.**

Translating to nearcore: Look for cases where a commitment (hash/root/part ordinal) is issued/stored, but the underlying data can be removed or invalidated before the commitment is redeemed/reconstructed.