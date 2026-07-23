Looking at the external bug class: a state that should be invalidated after an operation is not updated, allowing the operation to be repeated. I need to find an analog where a "permission" or "context" persists after use and can be reused.

Let me trace the relevant code paths.